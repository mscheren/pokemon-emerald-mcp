"""Unit tests for MGBAClient input injection and screenshot methods.

These tests use mock streams to verify the client sends correct JSON messages
and parses responses correctly without requiring a live mGBA instance.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.mgba_client import MGBAClient


def make_response(payload: dict, msg_id: int = 1) -> bytes:
    """Build a newline-terminated JSON response as bytes."""
    msg = {"type": "response", "id": msg_id, "timestamp": "2026-01-01T00:00:00Z", "payload": payload}
    return (json.dumps(msg) + "\n").encode()


def mock_connected_client(response_payload: dict, msg_id: int = 1) -> MGBAClient:
    """Return an MGBAClient with mocked reader/writer that returns one response."""
    client = MGBAClient()
    client._connected = True
    client._message_id = 0

    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    client.writer = writer

    reader = MagicMock()
    response_data = make_response(response_payload, msg_id)
    reader.readline = AsyncMock(return_value=response_data)
    client.reader = reader

    return client


class TestPressButton:
    async def test_sends_correct_action(self):
        client = mock_connected_client({"status": "ok", "frames_executed": 5}, msg_id=1)
        result = await client.press_button("A")
        # Verify the write was called
        assert client.writer.write.called
        written_data = client.writer.write.call_args[0][0].decode()
        request = json.loads(written_data.strip())
        assert request["payload"]["action"] == "press_button"
        assert request["payload"]["button"] == "A"
        assert request["payload"]["duration_frames"] == 5

    async def test_custom_duration(self):
        client = mock_connected_client({"status": "ok", "frames_executed": 10}, msg_id=1)
        await client.press_button("UP", duration_frames=10)
        written_data = client.writer.write.call_args[0][0].decode()
        request = json.loads(written_data.strip())
        assert request["payload"]["duration_frames"] == 10

    async def test_returns_payload(self):
        client = mock_connected_client({"status": "ok", "frames_executed": 5})
        result = await client.press_button("B")
        assert result == {"status": "ok", "frames_executed": 5}

    async def test_all_buttons_accepted(self):
        """Verify all GBA buttons can be sent via press_button."""
        buttons = ["A", "B", "UP", "DOWN", "LEFT", "RIGHT", "START", "SELECT", "L", "R"]
        for btn in buttons:
            client = mock_connected_client({"status": "ok", "frames_executed": 5})
            result = await client.press_button(btn)
            written_data = client.writer.write.call_args[0][0].decode()
            request = json.loads(written_data.strip())
            assert request["payload"]["button"] == btn


class TestPressButtons:
    async def test_sends_buttons_list(self):
        client = mock_connected_client({"status": "ok", "buttons_pressed": ["A", "B"]})
        result = await client.press_buttons(["A", "B"])
        written_data = client.writer.write.call_args[0][0].decode()
        request = json.loads(written_data.strip())
        assert request["payload"]["action"] == "press_buttons"
        assert request["payload"]["buttons"] == ["A", "B"]
        assert request["payload"]["duration_frames"] == 5

    async def test_returns_payload(self):
        client = mock_connected_client({"status": "ok", "buttons_pressed": ["UP", "A"]})
        result = await client.press_buttons(["UP", "A"])
        assert result["status"] == "ok"
        assert result["buttons_pressed"] == ["UP", "A"]

    async def test_custom_duration(self):
        client = mock_connected_client({"status": "ok", "buttons_pressed": ["L", "R"]})
        await client.press_buttons(["L", "R"], duration_frames=15)
        written_data = client.writer.write.call_args[0][0].decode()
        request = json.loads(written_data.strip())
        assert request["payload"]["duration_frames"] == 15


class TestWaitFrames:
    async def test_sends_wait_action(self):
        client = mock_connected_client({"status": "ok", "frames_waited": 30})
        result = await client.wait_frames(30)
        written_data = client.writer.write.call_args[0][0].decode()
        request = json.loads(written_data.strip())
        assert request["payload"]["action"] == "wait"
        assert request["payload"]["frames"] == 30

    async def test_default_frames(self):
        client = mock_connected_client({"status": "ok", "frames_waited": 30})
        await client.wait_frames()
        written_data = client.writer.write.call_args[0][0].decode()
        request = json.loads(written_data.strip())
        assert request["payload"]["frames"] == 30

    async def test_returns_payload(self):
        client = mock_connected_client({"status": "ok", "frames_waited": 60})
        result = await client.wait_frames(60)
        assert result == {"status": "ok", "frames_waited": 60}


class TestCaptureScreenshot:
    async def test_sends_capture_action(self):
        path = "/tmp/test_screenshot.png"
        client = mock_connected_client({"status": "ok", "path": path, "width": 240, "height": 160})
        result = await client.capture_screenshot(path)
        written_data = client.writer.write.call_args[0][0].decode()
        request = json.loads(written_data.strip())
        assert request["payload"]["action"] == "capture_screenshot"
        assert request["payload"]["path"] == path

    async def test_returns_payload_with_dimensions(self):
        path = "/tmp/frame_001.png"
        client = mock_connected_client({"status": "ok", "path": path, "width": 240, "height": 160})
        result = await client.capture_screenshot(path)
        assert result["status"] == "ok"
        assert result["path"] == path
        assert result["width"] == 240
        assert result["height"] == 160

    async def test_returns_error_payload_on_failure(self):
        client = mock_connected_client(
            {
                "status": "error",
                "error_code": "SCREENSHOT_FAILED",
                "error_message": "emu:screenshot() not available",
            }
        )
        result = await client.capture_screenshot("/tmp/fail.png")
        assert result["status"] == "error"
        assert result["error_code"] == "SCREENSHOT_FAILED"


class TestSaveGame:
    async def test_sends_save_game_action(self):
        client = mock_connected_client({"status": "ok", "note": "save initiated"})
        result = await client.save_game()
        written_data = client.writer.write.call_args[0][0].decode()
        request = json.loads(written_data.strip())
        assert request["payload"]["action"] == "save_game"
        assert result["status"] == "ok"


class TestShutdown:
    async def test_shutdown_swallows_errors(self):
        client = MGBAClient()
        client._connected = True
        client.writer = MagicMock()
        client.writer.write = MagicMock()
        client.writer.drain = AsyncMock()
        client.reader = MagicMock()
        # Simulate connection reset during shutdown
        client.reader.readline = AsyncMock(side_effect=ConnectionResetError("closed"))
        # Should not raise
        await client.shutdown()

    async def test_shutdown_on_disconnected_client(self):
        client = MGBAClient()
        client._connected = False
        # Should not raise even if not connected
        await client.shutdown()


class TestExecuteSequence:
    async def _make_client_with_responses(self, responses: list[dict]) -> MGBAClient:
        """Create a client that yields multiple mock responses in order."""
        client = MGBAClient()
        client._connected = True
        client._message_id = 0

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        client.writer = writer

        response_bytes = [make_response(r, i + 1) for i, r in enumerate(responses)]
        reader = MagicMock()
        reader.readline = AsyncMock(side_effect=response_bytes)
        client.reader = reader
        return client

    async def test_sequence_press_button(self):
        from src.agent.models import SequenceStep

        client = await self._make_client_with_responses(
            [
                {"status": "ok", "frames_executed": 5},
            ]
        )
        steps = [SequenceStep(action="press_button", button="A", duration_frames=5)]
        results = await client.execute_sequence(steps)
        assert len(results) == 1
        assert results[0]["status"] == "ok"

    async def test_sequence_multiple_steps(self):
        from src.agent.models import SequenceStep

        client = await self._make_client_with_responses(
            [
                {"status": "ok", "frames_executed": 5},
                {"status": "ok", "frames_waited": 30},
            ]
        )
        steps = [
            SequenceStep(action="press_button", button="UP", duration_frames=5),
            SequenceStep(action="wait", wait_frames=30),
        ]
        results = await client.execute_sequence(steps)
        assert len(results) == 2

    async def test_sequence_repeat(self):
        from src.agent.models import SequenceStep

        client = await self._make_client_with_responses(
            [
                {"status": "ok", "frames_executed": 5},
                {"status": "ok", "frames_executed": 5},
            ]
        )
        steps = [SequenceStep(action="press_button", button="A", duration_frames=5)]
        results = await client.execute_sequence(steps, repeat=2)
        assert len(results) == 2

    async def test_sequence_unknown_action_raises(self):
        from src.agent.models import SequenceStep

        client = await self._make_client_with_responses([])
        steps = [SequenceStep(action="fly")]
        with pytest.raises(ValueError, match="Unknown sequence step action"):
            await client.execute_sequence(steps)


class TestNotConnected:
    async def test_send_request_raises_when_not_connected(self):
        client = MGBAClient()
        with pytest.raises(RuntimeError, match="Not connected"):
            await client.send_request("press_button", button="A")


def make_event(event_type: str, extra: dict | None = None, event_id: int = 1001) -> bytes:
    """Build a newline-terminated JSON event message as bytes."""
    payload = {"event": event_type, **(extra or {})}
    msg = {"type": "event", "id": event_id, "timestamp": "2026-01-01T00:00:00Z", "payload": payload}
    return (json.dumps(msg) + "\n").encode()


class TestEventInterleaving:
    """send_request skips event lines and queues them on _event_queue."""

    async def test_event_before_response_is_queued(self):
        """An event line arriving before the response is queued, not returned."""
        client = MGBAClient()
        client._connected = True
        client._message_id = 0

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        client.writer = writer

        reader = MagicMock()
        # First readline returns an event; second returns the real response
        reader.readline = AsyncMock(
            side_effect=[
                make_event("battle_started", event_id=1001),
                make_response({"status": "ok", "frames_executed": 5}, msg_id=1),
            ]
        )
        client.reader = reader

        result = await client.press_button("A")

        assert result["status"] == "ok"
        assert client._event_queue.qsize() == 1
        queued = await client._event_queue.get()
        assert queued["type"] == "event"
        assert queued["payload"]["event"] == "battle_started"

    async def test_multiple_events_before_response_all_queued(self):
        """Multiple event lines are all queued; response is still returned."""
        client = MGBAClient()
        client._connected = True
        client._message_id = 0

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        client.writer = writer

        reader = MagicMock()
        reader.readline = AsyncMock(
            side_effect=[
                make_event("level_up", {"slot": 1, "new_level": 6}, event_id=1001),
                make_event("pokemon_fainted", {"slot": 2}, event_id=1002),
                make_response({"status": "ok"}, msg_id=1),
            ]
        )
        client.reader = reader

        result = await client.wait_frames(30)

        assert result["status"] == "ok"
        assert client._event_queue.qsize() == 2

    async def test_no_events_queue_remains_empty(self):
        """When no events are received, the event queue stays empty."""
        client = mock_connected_client({"status": "ok", "frames_executed": 5})
        await client.press_button("A")
        assert client._event_queue.empty()


class TestReconnect:
    """MGBAClient.reconnect() retries connection and returns True/False."""

    async def test_reconnect_succeeds_on_first_attempt(self):
        client = MGBAClient()
        client._connected = False

        mock_reader = MagicMock()
        mock_writer = MagicMock()
        with patch("asyncio.open_connection", new=AsyncMock(return_value=(mock_reader, mock_writer))):
            result = await client.reconnect(retries=3, delay=0.0)

        assert result is True
        assert client._connected is True

    async def test_reconnect_returns_false_after_all_failures(self):
        client = MGBAClient()
        client._connected = False

        with patch("asyncio.open_connection", new=AsyncMock(side_effect=OSError("refused"))):
            result = await client.reconnect(retries=2, delay=0.0)

        assert result is False
        assert client._connected is False
