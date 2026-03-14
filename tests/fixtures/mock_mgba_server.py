"""Mock mGBA TCP server for integration testing.

Simulates all mGBA Lua socket responses so integration tests can run without
a real ROM or mGBA installation.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime

MOCK_GAME_STATE = {
    "frame_number": 42000,
    "map_id": 3,
    "map_name": "Littleroot Town",
    "player_x": 15,
    "player_y": 8,
    "party_count": 2,
    "party": [
        {
            "slot": 1,
            "nickname": "TORCHIC",
            "level": 5,
            "current_hp": 20,
            "max_hp": 20,
            "attack": 45,
            "defense": 40,
            "speed": 45,
            "sp_attack": 70,
            "sp_defense": 50,
            "status": "healthy",
            "species_id": 280,
            "species_name": "Torchic",
            "moves": ["Scratch", "Growl"],
            "pp": [35, 40],
        },
        {
            "slot": 2,
            "nickname": "POOCHYENA",
            "level": 2,
            "current_hp": 11,
            "max_hp": 11,
            "attack": 9,
            "defense": 9,
            "speed": 9,
            "sp_attack": 6,
            "sp_defense": 6,
            "status": "healthy",
            "species_id": 286,
            "species_name": "Poochyena",
            "moves": ["Tackle", "Howl"],
            "pp": [35, 40],
        },
    ],
    "badges": [],
    "in_battle": False,
    "can_save": True,
}


class MockMGBAServer:
    """Async TCP server that mimics the mGBA Lua socket protocol."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5001):
        self.host = host
        self.port = port
        self.server = None
        self.frame_count = MOCK_GAME_STATE["frame_number"]
        self.events_to_emit: list[dict] = []

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            try:
                line = await reader.readline()
                if not line:
                    break
                request = json.loads(line.decode().strip())
                response = self._dispatch(request)
                # Emit any queued events before the response
                for event in self.events_to_emit:
                    writer.write((json.dumps(event) + "\n").encode())
                self.events_to_emit.clear()
                await writer.drain()
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionResetError, json.JSONDecodeError):
                break

    def _dispatch(self, request: dict) -> dict:
        payload = request.get("payload", {})
        action = payload.get("action", "")
        req_id = request.get("id", 0)
        ts = datetime.now(UTC).isoformat()

        if action == "get_state":
            self.frame_count += 30
            state = dict(MOCK_GAME_STATE)
            state["frame_number"] = self.frame_count
            return {
                "type": "response",
                "id": req_id,
                "timestamp": ts,
                "payload": {**state, "status": "ok"},
            }

        if action in ("press_button", "press_buttons"):
            return {
                "type": "response",
                "id": req_id,
                "timestamp": ts,
                "payload": {"status": "ok"},
            }

        if action == "wait":
            return {
                "type": "response",
                "id": req_id,
                "timestamp": ts,
                "payload": {"status": "ok", "frames_waited": payload.get("frames", 0)},
            }

        if action == "capture_screenshot":
            path = payload.get("path", "/tmp/test_screenshot.png")
            try:
                open(path, "w").close()
            except Exception:
                pass
            return {
                "type": "response",
                "id": req_id,
                "timestamp": ts,
                "payload": {"status": "ok", "path": path, "width": 240, "height": 160},
            }

        if action == "save_game":
            return {
                "type": "response",
                "id": req_id,
                "timestamp": ts,
                "payload": {"status": "ok", "save_completed": True},
            }

        if action == "shutdown":
            return {
                "type": "response",
                "id": req_id,
                "timestamp": ts,
                "payload": {"status": "shutting_down"},
            }

        return {
            "type": "response",
            "id": req_id,
            "timestamp": ts,
            "payload": {"status": "error", "error_code": "UNKNOWN_ACTION"},
        }

    def queue_event(self, event_payload: dict) -> None:
        """Queue an event to be emitted before the next response."""
        self.events_to_emit.append(
            {
                "type": "event",
                "id": int(uuid.uuid4().int) % 10000 + 1000,
                "timestamp": datetime.now(UTC).isoformat(),
                "payload": event_payload,
            }
        )
