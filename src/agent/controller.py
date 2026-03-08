"""Main game loop controller for the Pokemon Emerald AI Agent.

Orchestrates the observe → decide → act cycle between the agent and the mGBA emulator.
"""

import asyncio
import logging
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .formatter import DecisionParser, ObservationFormatter
from .knowledge import KnowledgeBase
from .mgba_client import MGBAClient
from .models import AgentDecision, ExtendedState, GameState, Observation, SequenceStep

logger = logging.getLogger(__name__)

# Default screenshot directory (relative to project root)
SCREENSHOTS_DIR = Path("data/screenshots")

# File-based IPC paths for CLI agent integration
# The agent reads OBSERVATION_FILE and writes DECISION_FILE each turn.
OBSERVATION_FILE = Path("data/current_observation.txt")
DECISION_FILE = Path("data/current_decision.json")

# mGBA binary built from 0.11-dev source (packaged 0.10.x does not support --script)
MGBA_BINARY = Path("~/mgba/build/qt/mgba-qt").expanduser()

# mGBA config path — ensure fullscreen=0 to prevent WSL2 window freeze
MGBA_CONFIG = Path("~/.config/mgba/config.ini").expanduser()


def _configure_mgba(mute: bool = False) -> None:
    """Patch mGBA config for agent use before launch.

    Keys patched:
    - fullscreen=0          avoid WSL2 window freeze on launch
    - pauseOnFocusLost=0    prevent mGBA pausing when terminal takes focus
    - audioBuffers=8192     larger audio buffer reduces crunchiness under WSL2
    - mute=1                (optional) disable audio entirely — agent does not need it

    Args:
        mute: If True, set mute=1 in the config to disable audio output.
              Recommended when running under WSLg to eliminate audio crunchiness.
    """
    if not MGBA_CONFIG.exists():
        return
    text = MGBA_CONFIG.read_text()
    patches = {
        "fullscreen": "0",
        "pauseOnFocusLost": "0",
        "audioBuffers": "8192",
    }
    if mute:
        patches["mute"] = "1"
    for key, value in patches.items():
        if re.search(rf"^{key}\s*=", text, re.MULTILINE):
            text = re.sub(rf"^{key}\s*=.*$", f"{key}={value}", text, flags=re.MULTILINE)
        else:
            text += f"\n{key}={value}\n"
    MGBA_CONFIG.write_text(text)


class GameController:
    """Manages the agent's main loop against a live mGBA instance."""

    def __init__(self, mgba_client: MGBAClient, screenshots_dir: Path = SCREENSHOTS_DIR):
        self.mgba_client = mgba_client
        self.screenshots_dir = screenshots_dir
        # Ensure screenshot directory exists at construction time
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    async def request_screenshot(self, frame_number: int) -> Path:
        """Capture a screenshot and return its path.

        Constructs a zero-padded filename based on the frame number, requests
        the screenshot from Lua, and returns the resulting Path.

        Args:
            frame_number: Current emulator frame number (used in filename).

        Returns:
            Path to the saved PNG file.

        Raises:
            RuntimeError: If Lua reports a screenshot failure.
        """
        path = self.screenshots_dir / f"frame_{frame_number:08d}.png"
        payload = await self.mgba_client.capture_screenshot(str(path))
        if payload.get("status") != "ok":
            raise RuntimeError(f"Screenshot failed: {payload.get('error_message', 'unknown error')}")
        # Poll for file flush — on WSL2, Lua's response can arrive before the OS
        # has fully written the PNG bytes to disk.
        elapsed = 0.0
        timeout = 1.0
        while not path.exists() and elapsed < timeout:
            await asyncio.sleep(0.05)
            elapsed += 0.05
        if not path.exists():
            raise RuntimeError(f"Screenshot file never appeared on disk: {path}")
        return path


class PokemonAgentController:
    """Full lifecycle controller for the Pokemon Emerald AI Agent.

    Owns all component references (mGBA client, knowledge base, mGBA process)
    and manages the observe → decide → execute game loop.
    """

    def __init__(
        self,
        rom_path: Path,
        lua_script_path: Path,
        knowledge_db_path: Path,
        host: str = "127.0.0.1",
        port: int = 5000,
        launch_mgba: bool = True,
        screenshot_interval: int = 1,
    ):
        self.rom_path = rom_path
        self.lua_script_path = lua_script_path
        self.launch_mgba = launch_mgba
        self.screenshot_interval = max(1, screenshot_interval)
        self.mgba_client = MGBAClient(host=host, port=port)
        self.knowledge_base = KnowledgeBase(knowledge_db_path)
        self.mgba_process: Optional[subprocess.Popen] = None
        self.running = False
        self.paused = False
        self._pause_msg_shown = False
        self._pending_event_context = ""
        self._iteration = 0
        self._current_map_id = 0
        self.formatter = ObservationFormatter()
        self.parser = DecisionParser()
        self._guidance_cache: tuple[float, list] = (0.0, [])
        # Signals that game_loop has exited; used by stop() to serialise
        # socket access — stop() waits here before calling save_game() so it
        # never conflicts with an in-progress wait_frames/request_state call.
        self._game_loop_done: asyncio.Event = asyncio.Event()
        self._game_loop_done.set()  # "done" until start() clears it

    async def start(self, mute: bool = False) -> None:
        """Full startup: optionally launch mGBA, connect, init KB, start loop.

        When launch_mgba=False the agent skips launching mGBA and connects to
        an already-running instance (e.g. started manually in a separate
        terminal or running natively on Windows). This lets the user load a
        save and reach a desired game state before the agent loop begins.

        Args:
            mute: If True, patch mGBA config with mute=1 before launch.
        """
        logger.info("Starting Pokemon AI Agent...")

        await self.knowledge_base.initialize()
        logger.info("Knowledge base ready")

        if self.launch_mgba:
            self._launch_mgba(mute=mute)
            # Give mGBA time to start and bind the socket
            await asyncio.sleep(3.0)

        await self.mgba_client.connect(retries=5, delay=2.0)
        logger.info("Connected to mGBA Lua socket")

        Path("data/screenshots").mkdir(parents=True, exist_ok=True)

        print(f"\n{'=' * 60}")
        print("CLI Agent Integration — file-based IPC")
        print(f"  Read observations from : {OBSERVATION_FILE.absolute()}")
        print(f"  Write decisions to     : {DECISION_FILE.absolute()}")
        print(f"{'=' * 60}\n")

        self._stop_task: Optional[asyncio.Task] = None

        def _on_signal() -> None:
            if self._stop_task is None:
                self._stop_task = asyncio.create_task(self.stop())

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _on_signal)

        self._game_loop_done.clear()  # mark loop as active before entering it
        self.running = True
        # Start user input loop (pause/resume/guidance) as a background task
        self._input_task: asyncio.Task = asyncio.create_task(self._user_input_loop())
        try:
            await self.game_loop()
        finally:
            self._input_task.cancel()
            try:
                await self._input_task
            except asyncio.CancelledError:
                pass

        # If shutdown was triggered by a signal, wait for it to finish before
        # returning so asyncio.run() does not cancel the stop task mid-cleanup.
        if self._stop_task is not None and not self._stop_task.done():
            await self._stop_task

    def _launch_mgba(self, mute: bool = False) -> None:
        """Launch mGBA with the Lua script and ROM."""
        if not MGBA_BINARY.exists():
            raise FileNotFoundError(
                f"mGBA binary not found at {MGBA_BINARY}. " "Build mGBA from source — see docs/building-mgba.md."
            )
        _configure_mgba(mute=mute)
        cmd = [str(MGBA_BINARY), "--script", str(self.lua_script_path), str(self.rom_path)]
        logger.info(f"Launching mGBA: {' '.join(cmd)}")
        self.mgba_process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    async def stop(self) -> None:
        """Graceful shutdown: save game, close connections, terminate process."""
        if not self.running:
            return
        logger.info("\n[Agent] Shutting down gracefully...")
        self.paused = False  # ensure game_loop exits its pause sleep
        self.running = False

        # Wait for the game loop to exit its current iteration so no socket
        # operation is in flight when we call save_game() below.
        try:
            await asyncio.wait_for(self._game_loop_done.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("[Agent] Game loop did not exit in time; proceeding")

        try:
            logger.info("[Agent] Saving game...")
            await self.mgba_client.save_game()
            logger.info("[Agent] Game saved.")
        except Exception as e:
            logger.warning(f"[Agent] Could not save game: {e}")

        try:
            await self.mgba_client.shutdown()
        except Exception:
            pass  # mGBA may already be closing

        await self.knowledge_base.close()

        if self.mgba_process and self.mgba_process.poll() is None:
            self.mgba_process.terminate()

        logger.info("[Agent] Shutdown complete.")

    async def game_loop(self) -> None:
        """Main game loop: screenshot → observe → decide → execute → repeat.

        The screenshot is captured at the *start* of each iteration so the
        observation always reflects the current on-screen state:
            [screenshot] → observe → decide → execute → [screenshot] → …
        """
        try:
            while self.running:
                if self.paused:
                    if not self._pause_msg_shown:
                        print("\n[Agent] ⏸  PAUSED — Game loop suspended. mGBA is still running.")
                        print("[Agent] Type 'resume' to continue, 'stop' to quit.")
                        self._pause_msg_shown = True
                    await asyncio.sleep(0.5)
                    continue
                self._pause_msg_shown = False

                self._iteration += 1
                logger.info(f"\n{'=' * 60}")
                logger.info(f"ITERATION {self._iteration}")
                logger.info(f"{'=' * 60}")

                try:
                    # Drain any pending events before building the observation
                    await self._process_pending_events()

                    state_data = await self.mgba_client.request_state()
                    game_state = GameState.from_dict(state_data)
                    self._current_map_id = game_state.map_id

                    # Fetch extended state (bag + PC) every 5 iterations to
                    # avoid the per-frame overhead of reading 14 boxes × 30 slots.
                    extended_state: ExtendedState | None = None
                    if self._iteration % 5 == 1:
                        try:
                            ext_data = await self.mgba_client.request_extended_state()
                            extended_state = ExtendedState.from_dict(ext_data)
                        except Exception as e:
                            logger.warning("Failed to fetch extended state: %s", e)

                    # Capture screenshot now so the observation always has a
                    # current image of what is on screen before the agent decides.
                    # Skip capture on iterations that don't match the interval
                    # to reduce emu:screenshot() overhead (PNG write in onFrame).
                    if self._iteration % self.screenshot_interval == 0:
                        screenshot_path = await self._capture_screenshot(game_state.frame_number)
                        if screenshot_path:
                            # Poll for file existence (up to 1s) to guarantee
                            # the PNG is on disk before the observation is written.
                            screenshot_path = await self._await_screenshot_file(screenshot_path)
                        if screenshot_path:
                            logger.info(f"Screenshot: {screenshot_path}")
                    else:
                        screenshot_path = None

                    observation = Observation(
                        game_state=game_state,
                        frame_number=game_state.frame_number,
                        screenshot_path=screenshot_path,
                        extended_state=extended_state,
                    )
                    self._log_observation(observation)

                    decision = await self._get_decision(observation)
                    self._pending_event_context = ""  # consumed for this iteration
                    await self._execute_decision(decision)
                    await self._record_decision_knowledge(decision, observation)
                    await self._cleanup_old_screenshots()

                except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError) as e:
                    # Attempt reconnect on socket disconnect
                    logger.warning(f"mGBA connection lost: {e}. Attempting reconnect...")
                    success = await self.mgba_client.reconnect()
                    if not success:
                        logger.error("Could not reconnect to mGBA. Stopping agent.")
                        await self.stop()
                        break
                    continue
                except Exception as e:
                    if self.running:  # suppress expected connection errors on shutdown
                        logger.error(f"Loop error (iteration {self._iteration}): {e}")
                    await asyncio.sleep(1.0)

                await asyncio.sleep(0.2)

        finally:
            self._game_loop_done.set()  # unblock stop() which may be waiting

    async def _user_input_loop(self) -> None:
        """Read stdin for pause/resume/guidance commands while the game loop runs."""
        loop = asyncio.get_event_loop()
        while self.running:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                user_input = line.strip()
                cmd = user_input.lower()
                if cmd == "pause":
                    self.paused = True
                    print("[Agent] ⏸  Pausing game loop...")
                elif cmd == "resume":
                    self.paused = False
                    self._pause_msg_shown = False
                    print("[Agent] ▶  Resuming game loop...")
                elif cmd in ("stop", "quit", "exit"):
                    if self._stop_task is None:
                        self._stop_task = asyncio.create_task(self.stop())
                    break
                elif user_input:
                    # Store as user guidance for the agent to follow
                    try:
                        gid = await self.knowledge_base.add_user_guidance(
                            instruction=user_input,
                            context=f"map={self._current_map_id}",
                            priority=5,
                        )
                        print(f"[Guidance] Stored (id={gid}): {user_input}")
                    except Exception as e:
                        logger.warning("Failed to store guidance: %s", e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Input loop error: %s", e)
                break

    async def _process_pending_events(self) -> None:
        """Drain the event queue and handle each event before building observation."""
        context_parts: list[str] = []
        while not self.mgba_client._event_queue.empty():
            event_msg = await self.mgba_client._event_queue.get()
            await self._handle_event(event_msg, context_parts)
        if context_parts:
            self._pending_event_context = "\n".join(context_parts)

    async def _handle_event(self, event_msg: dict, context_parts: list[str]) -> None:
        """Route a single event to the appropriate handler."""
        payload = event_msg.get("payload", {})
        etype = payload.get("event", "unknown")
        if etype == "battle_started":
            battle_type = payload.get("battle_type", "unknown")
            msg = f"Battle started ({battle_type})"
            print(f"\n[EVENT] ⚔  {msg}")
            context_parts.append(f"[EVENT] {msg}")
        elif etype == "battle_ended":
            outcome = payload.get("outcome", "unknown")
            icon = "✓" if outcome == "victory" else "✗"
            msg = f"Battle ended: {outcome}"
            print(f"\n[EVENT] {icon} {msg}")
            context_parts.append(f"[EVENT] {msg}")
            try:
                await self.knowledge_base.record_progress("milestone", f"Battle {outcome}", str(payload))
            except Exception as e:
                logger.warning("Failed to record battle event: %s", e)
        elif etype == "level_up":
            slot = payload.get("slot", "?")
            new_level = payload.get("new_level", "?")
            msg = f"Pokemon slot {slot} leveled up to {new_level}!"
            print(f"\n[EVENT] ⬆  {msg}")
            context_parts.append(f"[EVENT] {msg}")
            try:
                await self.knowledge_base.record_progress("milestone", f"Level up to {new_level}", str(payload))
            except Exception as e:
                logger.warning("Failed to record level-up event: %s", e)
        elif etype == "pokemon_fainted":
            slot = payload.get("slot", "?")
            msg = f"Pokemon slot {slot} fainted!"
            print(f"\n[EVENT] 💀 {msg}")
            context_parts.append(f"[EVENT] {msg}")
        else:
            logger.debug("Unknown event type: %s", etype)

    async def _await_screenshot_file(self, path: Path, timeout: float = 1.0, interval: float = 0.1) -> Optional[Path]:
        """Poll for screenshot file existence up to *timeout* seconds.

        Returns the path when the file appears, or None if it never arrives
        (degraded mode — the observation proceeds without a screenshot).
        """
        elapsed = 0.0
        while elapsed < timeout:
            if path.exists():
                return path
            await asyncio.sleep(interval)
            elapsed += interval
        logger.warning(
            "Screenshot %s did not appear within %.1fs — proceeding in degraded mode",
            path,
            timeout,
        )
        return None

    async def _capture_screenshot(self, frame_number: int) -> Optional[Path]:
        path = Path(f"data/screenshots/frame_{frame_number:08d}.png")
        try:
            await self.mgba_client.capture_screenshot(str(path))
            return path
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return None

    def _log_observation(self, obs: Observation) -> None:
        gs = obs.game_state
        print(f"\n{'─' * 60}")
        print(f"  Iteration {self._iteration} | Frame {gs.frame_number}")
        print(f"  Map: {gs.map_name} ({gs.map_id}) | Pos: ({gs.player_x},{gs.player_y}) | Battle: {gs.in_battle}")
        if gs.party:
            party_str = " | ".join(
                f"{p.species_name or p.nickname} Lv{p.level} {p.current_hp}/{p.max_hp}HP" for p in gs.party
            )
            print(f"  Party: {party_str}")
        if obs.screenshot_path:
            print(f"  Screenshot: {obs.screenshot_path}")
        print(f"{'─' * 60}")

    async def _cleanup_old_screenshots(self, keep_last: int = 20) -> None:
        """Delete screenshots beyond the most recent N to prevent unbounded disk use."""
        files = sorted(SCREENSHOTS_DIR.glob("frame_*.png"), key=lambda f: f.stat().st_mtime)
        for f in files[:-keep_last]:
            try:
                f.unlink()
            except Exception:
                pass

    async def _get_active_guidance_cached(self) -> list[dict]:
        """Return active guidance, refreshing from DB at most once every 5 seconds."""
        now = time.monotonic()
        if now - self._guidance_cache[0] > 5.0:
            guidance = await self.knowledge_base.get_active_guidance()
            self._guidance_cache = (now, guidance)
        return self._guidance_cache[1]

    async def _get_decision(self, obs: Observation) -> AgentDecision:
        """Present observation to the agent and get a structured decision.

        Fetches active guidance and relevant knowledge from the KB, formats
        the observation prompt, prints it to stdout AND writes it to
        OBSERVATION_FILE for CLI agent to read via the Read tool.

        Polls DECISION_FILE every 0.5 s until CLI agent writes a JSON
        decision there via the Write tool. Clears the file after reading.
        """
        gs = obs.game_state
        active_guidance = await self._get_active_guidance_cached()
        context_keyword = str(gs.map_id) if gs.map_id else "general"
        relevant_knowledge = await self.knowledge_base.get_relevant_knowledge(context_keyword, limit=3)

        obs_text = self.formatter.format(
            observation=obs,
            active_guidance=active_guidance,
            relevant_knowledge=relevant_knowledge,
        )
        # Prepend any pending event context so the agent sees recent events first
        if self._pending_event_context:
            obs_text = f"## RECENT EVENTS\n{self._pending_event_context}\n\n{obs_text}"
        print("\n" + obs_text)

        if active_guidance:
            print(f"\n[Guidance] Following {len(active_guidance)} active instruction(s):")
            for g in active_guidance[:3]:
                print(f"  • {g['instruction']}")

        # Write observation to file for CLI agent to read
        OBSERVATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        OBSERVATION_FILE.write_text(obs_text)

        # Remove any stale decision from the previous iteration
        DECISION_FILE.unlink(missing_ok=True)

        print(f"\n[Waiting for decision... write JSON to: {DECISION_FILE.absolute()}]")

        # Poll until CLI agent writes the decision file or shutdown is requested
        while self.running:
            if DECISION_FILE.exists():
                try:
                    response = DECISION_FILE.read_text()
                    DECISION_FILE.unlink(missing_ok=True)
                    decision = self.parser.parse(response)
                    print(f"[Decision received: {decision.action_type}]")
                    return decision
                except Exception as e:
                    logger.warning("Failed to read decision file: %s", e)
                    DECISION_FILE.unlink(missing_ok=True)
            await asyncio.sleep(0.5)

        # shutdown was requested while waiting
        return AgentDecision(
            action_type="wait",
            action_params={"frames": 1},
            reasoning="shutdown requested",
        )

    async def _execute_decision(self, decision: AgentDecision) -> None:
        """Execute a decision from the agent."""
        action = decision.action_type
        params = decision.action_params

        if decision.reasoning:
            print("\n[Agent's reasoning]")
            for line in decision.reasoning.split("\n"):
                print(f"  {line}")

        print(f"\n[Action] {action}: {params}")

        if action == "press_button":
            await self.mgba_client.press_button(params["button"], params.get("duration_frames", 8))
        elif action == "press_buttons":
            await self.mgba_client.press_buttons(params["buttons"], params.get("duration_frames", 8))
        elif action == "press_sequence":
            steps = [SequenceStep.from_dict(s) for s in params.get("sequence", [])]
            repeat = params.get("repeat", 1)
            await self.mgba_client.execute_sequence(steps, repeat=repeat)
        elif action == "wait":
            await self.mgba_client.wait_frames(params.get("frames", 60))
        elif action == "save_game":
            await self.mgba_client.save_game()
        elif action == "pause":
            self.paused = True
            print("[Agent] ⏸  PAUSED via decision. Type 'resume' to continue.")

    async def _record_decision_knowledge(self, decision: AgentDecision, obs: Observation) -> None:
        """Persist knowledge entries from the agent's decision to SQLite."""
        if not decision.knowledge_to_store:
            return
        gs = obs.game_state
        for entry in decision.knowledge_to_store:
            if not isinstance(entry, dict):
                continue
            description = entry.get("description", "")
            if not description:
                continue
            try:
                row_id = await self.knowledge_base.record_discovery(
                    category=entry.get("category", "mechanic"),
                    title=entry.get("title", "Untitled"),
                    description=description,
                    map_id=gs.map_id,
                    x=gs.player_x,
                    y=gs.player_y,
                )
                print(
                    f"[Knowledge] Saved [{entry.get('category', 'mechanic')}] "
                    f"'{entry.get('title', 'Untitled')}' (id={row_id})"
                )
            except Exception as e:
                logger.warning("Failed to save knowledge entry: %s", e)
