import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import SequenceStep

logger = logging.getLogger(__name__)


class MGBAClient:
    """Async TCP client for the mGBA Lua socket server.

    Sends JSON requests to the Lua script running inside mGBA and awaits
    newline-terminated JSON responses. All communication is on 127.0.0.1:5000.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5000):
        """Initialise the client without connecting.

        Args:
            host: IP address of the mGBA Lua socket server. Use ``127.0.0.1``
                on WSL2 to avoid IPv6 resolution issues with ``localhost``.
            port: TCP port the Lua script listens on (default 5000).
        """
        self.host = host
        self.port = port
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._message_id = 0
        self._connected = False
        self._event_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self, retries: int = 3, delay: float = 2.0) -> None:
        """Connect to the mGBA Lua socket server with retry logic.

        Args:
            retries: Maximum number of connection attempts before raising.
            delay: Seconds to wait between failed attempts.

        Raises:
            ConnectionError: If all retry attempts are exhausted.
        """
        for attempt in range(retries):
            try:
                self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
                self._connected = True
                logger.info(f"Connected to mGBA at {self.host}:{self.port}")
                return
            except (ConnectionRefusedError, OSError) as e:
                logger.warning(f"Connect attempt {attempt + 1}/{retries} failed: {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(delay)
        raise ConnectionError(f"Could not connect to mGBA at {self.host}:{self.port} after {retries} attempts")

    def _next_id(self) -> int:
        """Return the next monotonically increasing message ID.

        Returns:
            An integer suitable for use as the ``id`` field in a request.
        """
        self._message_id += 1
        return self._message_id

    async def send_request(self, action: str, **params) -> dict[str, Any]:
        """Send a request to Lua and wait for its response.

        Serialises the action and params as a newline-terminated JSON message,
        writes it to the socket, then reads response lines until a non-event
        message is found. Any unsolicited event lines received while waiting
        are pushed onto ``_event_queue`` for the controller to drain later.

        Args:
            action: The Lua action name (e.g. ``"get_state"``, ``"press_button"``).
            **params: Additional key-value pairs included in the request payload
                (e.g. ``button="A"``, ``duration_frames=5``).

        Returns:
            The full parsed response dict from Lua, including ``type``, ``id``,
            ``timestamp``, and ``payload``.

        Raises:
            RuntimeError: If called before a successful ``connect()``.
            ConnectionResetError: If the socket closes before a response arrives.
        """
        if not self._connected or not self.writer or not self.reader:
            raise RuntimeError("Not connected to mGBA")

        msg_id = self._next_id()
        request = {
            "type": "request",
            "id": msg_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": {"action": action, **params},
        }
        line = json.dumps(request) + "\n"
        self.writer.write(line.encode())
        await self.writer.drain()
        logger.debug(f"Sent: {action} (id={msg_id})")

        # Read lines until we get the matching response (skip events)
        while True:
            response_line = await self.reader.readline()
            if not response_line:
                raise ConnectionResetError("mGBA socket closed unexpectedly")
            msg = json.loads(response_line.decode().strip())
            if msg.get("type") == "event":
                logger.debug(f"Queued event: {msg.get('payload', {}).get('event')}")
                await self._event_queue.put(msg)
            else:
                logger.debug(f"Received response id={msg.get('id')}")
                return msg

    async def request_state(self) -> dict[str, Any]:
        """Request the current game state from Lua.

        Returns:
            The ``payload`` dict from the ``get_state`` response, containing
            keys such as ``frame_number``, ``map_id``, ``party``, etc.
        """
        response = await self.send_request("get_state")
        return response.get("payload", {})

    async def request_extended_state(self) -> dict[str, Any]:
        """Request bag and PC box data via the get_extended_state action.

        Separate from request_state to avoid the per-frame overhead of reading
        14 boxes × 30 slots on every iteration.

        Returns:
            Dict with keys ``bag`` (pocket name → item list) and
            ``pc_boxes`` (list of occupied box dicts).
        """
        response = await self.send_request("get_extended_state")
        return response.get("payload", {})

    async def press_button(self, button: str, duration_frames: int = 5) -> dict[str, Any]:
        """Press a single GBA button for the specified number of frames.

        Args:
            button: Button name — one of ``A``, ``B``, ``UP``, ``DOWN``,
                ``LEFT``, ``RIGHT``, ``START``, ``SELECT``, ``L``, ``R``.
            duration_frames: How many emulated frames to hold the button down.

        Returns:
            The ``payload`` dict from the Lua response (``{"status": "ok"}``).
        """
        response = await self.send_request("press_button", button=button, duration_frames=duration_frames)
        return response.get("payload", {})

    async def press_buttons(self, buttons: list[str], duration_frames: int = 5) -> dict[str, Any]:
        """Press multiple GBA buttons simultaneously.

        Args:
            buttons: List of button names to hold at the same time.
            duration_frames: How many emulated frames to hold the buttons down.

        Returns:
            The ``payload`` dict from the Lua response (``{"status": "ok"}``).
        """
        response = await self.send_request("press_buttons", buttons=buttons, duration_frames=duration_frames)
        return response.get("payload", {})

    async def execute_sequence(
        self,
        steps: "list[SequenceStep]",
        repeat: int = 1,
    ) -> list[dict[str, Any]]:
        """Execute a sequence of input steps without returning to the game loop.

        Each step is dispatched to Lua in order. The entire sequence can be
        repeated ``repeat`` times. This allows the agent to express multi-step
        actions (e.g. "walk 10 steps north then open the menu") as a single
        decision, avoiding the overhead of a full observe→decide cycle between
        each individual button press.

        Args:
            steps: Ordered list of ``SequenceStep`` objects to execute.
            repeat: Number of times to run the full sequence (default 1).

        Returns:
            List of ``payload`` dicts, one per step per repetition, in
            execution order.

        Raises:
            RuntimeError: If called before a successful ``connect()``.
            ValueError: If an unrecognised step action is encountered.
        """

        results: list[dict[str, Any]] = []
        for _ in range(max(1, repeat)):
            for step in steps:
                if step.action == "press_button":
                    result = await self.press_button(
                        step.button,
                        step.duration_frames,  # type: ignore[arg-type]
                    )
                elif step.action == "press_buttons":
                    result = await self.press_buttons(step.buttons, step.duration_frames)
                elif step.action == "wait":
                    result = await self.wait_frames(step.wait_frames)
                else:
                    raise ValueError(f"Unknown sequence step action: {step.action!r}")
                results.append(result)
        return results

    async def capture_screenshot(self, path: str) -> dict[str, Any]:
        """Capture a screenshot of the current mGBA frame and save it as PNG.

        The directory for ``path`` must exist before calling this method.
        Python is responsible for creating ``data/screenshots/`` before
        requesting screenshots.

        Args:
            path: Absolute filesystem path where the PNG should be written.

        Returns:
            The ``payload`` dict from the Lua response, including ``path``
            confirming where the file was saved.
        """
        response = await self.send_request("capture_screenshot", path=path)
        return response.get("payload", {})

    async def wait_frames(self, frames: int = 30) -> dict[str, Any]:
        """Instruct Lua to hold off input for a number of emulated frames.

        Useful when the game needs time to animate (e.g. dialog transitions)
        before the next button press will have any effect.

        Args:
            frames: Number of frames to wait (at ~60 fps, 60 frames ≈ 1 s).

        Returns:
            The ``payload`` dict from the Lua response (``{"status": "ok"}``).
        """
        response = await self.send_request("wait", frames=frames)
        return response.get("payload", {})

    async def save_game(self) -> dict[str, Any]:
        """Send save game command to Lua.

        Returns:
            The ``payload`` dict from the Lua response.
        """
        response = await self.send_request("save_game")
        return response.get("payload", {})

    async def shutdown(self) -> None:
        """Send a shutdown command to the Lua script and suppress errors.

        Errors are swallowed because the connection may already be closing
        when this is called during controller teardown.
        """
        try:
            await self.send_request("shutdown")
        except Exception:
            pass

    async def disconnect(self) -> None:
        """Close the TCP connection to mGBA gracefully."""
        self._connected = False
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        logger.info("Disconnected from mGBA")

    async def reconnect(self, retries: int = 3, delay: float = 2.0) -> bool:
        """Attempt to re-establish the connection after a disconnect.

        Args:
            retries: Maximum number of reconnection attempts.
            delay: Seconds to wait between failed attempts.

        Returns:
            True if reconnection succeeded, False if all attempts failed.
        """
        logger.info("Attempting reconnect to mGBA...")
        self._connected = False
        for attempt in range(retries):
            try:
                self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
                self._connected = True
                logger.info(f"Reconnected to mGBA (attempt {attempt + 1})")
                return True
            except Exception as e:
                logger.warning(f"Reconnect attempt {attempt + 1} failed: {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(delay)
        logger.error("Failed to reconnect to mGBA after all attempts")
        return False
