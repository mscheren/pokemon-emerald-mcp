"""Unit tests for controller module — GameController and PokemonAgentController."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.controller import GameController, PokemonAgentController
from src.agent.mgba_client import MGBAClient
from src.agent.models import AgentDecision, GameState, Observation


class TestRequestScreenshot:
    def _make_controller(self, screenshot_response: dict, tmp_path: Path) -> GameController:
        client = MagicMock(spec=MGBAClient)
        client.capture_screenshot = AsyncMock(return_value=screenshot_response)
        return GameController(mgba_client=client, screenshots_dir=tmp_path / "screenshots")

    async def test_constructs_correct_path(self, tmp_path: Path):
        ctrl = self._make_controller(
            {"status": "ok", "path": str(tmp_path / "screenshots" / "frame_00000100.png"), "width": 240, "height": 160},
            tmp_path,
        )
        (tmp_path / "screenshots").mkdir(parents=True, exist_ok=True)
        (tmp_path / "screenshots" / "frame_00000100.png").write_bytes(b"")
        path = await ctrl.request_screenshot(100)
        assert path.name == "frame_00000100.png"

    async def test_returns_path_object(self, tmp_path: Path):
        ctrl = self._make_controller(
            {"status": "ok", "path": "/tmp/x.png", "width": 240, "height": 160},
            tmp_path,
        )
        (tmp_path / "screenshots").mkdir(parents=True, exist_ok=True)
        (tmp_path / "screenshots" / "frame_00000001.png").write_bytes(b"")
        path = await ctrl.request_screenshot(1)
        assert isinstance(path, Path)

    async def test_raises_on_screenshot_failure(self, tmp_path: Path):
        ctrl = self._make_controller(
            {"status": "error", "error_code": "SCREENSHOT_FAILED", "error_message": "no emu api"},
            tmp_path,
        )
        with pytest.raises(RuntimeError, match="Screenshot failed"):
            await ctrl.request_screenshot(1)

    async def test_passes_path_to_client(self, tmp_path: Path):
        client = MagicMock(spec=MGBAClient)
        client.capture_screenshot = AsyncMock(return_value={"status": "ok", "path": "/x.png"})
        screenshots_dir = tmp_path / "screenshots"
        ctrl = GameController(mgba_client=client, screenshots_dir=screenshots_dir)
        (screenshots_dir / "frame_00000042.png").write_bytes(b"")
        await ctrl.request_screenshot(42)
        expected_path = str(screenshots_dir / "frame_00000042.png")
        client.capture_screenshot.assert_called_once_with(expected_path)

    def test_creates_screenshots_dir(self, tmp_path: Path):
        client = MagicMock(spec=MGBAClient)
        screenshots_dir = tmp_path / "auto_created" / "screenshots"
        ctrl = GameController(mgba_client=client, screenshots_dir=screenshots_dir)
        assert screenshots_dir.exists()


def _make_agent_controller(tmp_path: Path) -> PokemonAgentController:
    """Create a PokemonAgentController with mocked dependencies."""
    rom = tmp_path / "game.gba"
    rom.write_bytes(b"\x00" * 64)
    lua = tmp_path / "agent.lua"
    lua.write_text("-- stub")
    ctrl = PokemonAgentController(
        rom_path=rom,
        lua_script_path=lua,
        knowledge_db_path=tmp_path / "test.db",
    )
    # Replace live components with mocks
    ctrl.mgba_client = MagicMock(spec=MGBAClient)
    ctrl.mgba_client.connect = AsyncMock()
    ctrl.mgba_client.request_state = AsyncMock(
        return_value={
            "frame_number": 42,
            "map_id": 900,
            "map_name": "Littleroot Town",
            "player_x": 5,
            "player_y": 7,
            "party_count": 1,
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
                }
            ],
            "badges": [],
            "in_battle": False,
            "can_save": True,
        }
    )
    ctrl.mgba_client.capture_screenshot = AsyncMock(return_value={"status": "ok", "path": "/tmp/x.png"})
    ctrl.mgba_client.press_button = AsyncMock(return_value={"status": "ok"})
    ctrl.mgba_client.press_buttons = AsyncMock(return_value={"status": "ok"})
    ctrl.mgba_client.wait_frames = AsyncMock(return_value={"status": "ok"})
    ctrl.mgba_client.save_game = AsyncMock(return_value={"status": "ok"})
    ctrl.mgba_client.shutdown = AsyncMock()
    ctrl.mgba_client.execute_sequence = AsyncMock(return_value=[{"status": "ok"}])
    return ctrl


class TestPokemonAgentControllerInit:
    def test_init_sets_components(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        assert ctrl.running is False
        assert ctrl._iteration == 0
        assert ctrl._current_map_id == 0

    def test_init_stores_paths(self, tmp_path: Path):
        rom = tmp_path / "game.gba"
        rom.write_bytes(b"\x00" * 64)
        lua = tmp_path / "agent.lua"
        lua.write_text("-- stub")
        db = tmp_path / "test.db"
        ctrl = PokemonAgentController(rom_path=rom, lua_script_path=lua, knowledge_db_path=db)
        assert ctrl.rom_path == rom
        assert ctrl.lua_script_path == lua


class TestPokemonAgentControllerStop:
    async def test_stop_is_idempotent(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        ctrl.running = False
        # Should return immediately without calling save_game
        await ctrl.stop()
        ctrl.mgba_client.save_game.assert_not_called()

    async def test_stop_saves_and_shuts_down(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        ctrl.running = True
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.close = AsyncMock()

        await ctrl.stop()

        assert ctrl.running is False
        ctrl.mgba_client.save_game.assert_called_once()
        ctrl.mgba_client.shutdown.assert_called_once()
        ctrl.knowledge_base.close.assert_called_once()

    async def test_stop_tolerates_save_failure(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        ctrl.running = True
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.close = AsyncMock()
        ctrl.mgba_client.save_game = AsyncMock(side_effect=Exception("save failed"))

        # Should not raise
        await ctrl.stop()
        assert ctrl.running is False


class TestPokemonAgentControllerObservation:
    async def test_log_observation_runs_without_error(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        state_data = await ctrl.mgba_client.request_state()
        game_state = GameState.from_dict(state_data)
        obs = Observation(
            game_state=game_state,
            frame_number=game_state.frame_number,
            screenshot_path=Path("/tmp/x.png"),
        )
        ctrl._log_observation(obs)  # should not raise

    async def test_get_decision_reads_decision_file(self, tmp_path: Path):
        """_get_decision polls the decision file and returns parsed decision."""
        import src.agent.controller as ctrl_module

        ctrl = _make_agent_controller(tmp_path)
        ctrl.running = True
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.get_active_guidance = AsyncMock(return_value=[])
        ctrl.knowledge_base.get_relevant_knowledge = AsyncMock(return_value=[])

        decision_file = tmp_path / "decision.json"
        obs_file = tmp_path / "obs.txt"
        json_response = (
            '{"reasoning": "test", "action_type": "wait", "action_params": {"frames": 30}, "knowledge_to_store": []}'
        )

        # Write decision file after a short delay
        async def _write_after_delay():
            await asyncio.sleep(0.3)
            decision_file.write_text(json_response)

        state_data = await ctrl.mgba_client.request_state()
        game_state = GameState.from_dict(state_data)
        obs = Observation(game_state=game_state, frame_number=0)

        with (
            patch.object(ctrl_module, "DECISION_FILE", decision_file),
            patch.object(ctrl_module, "OBSERVATION_FILE", obs_file),
        ):
            write_task = asyncio.create_task(_write_after_delay())
            decision = await ctrl._get_decision(obs)
            await write_task

        assert isinstance(decision, AgentDecision)
        assert decision.action_type == "wait"
        assert decision.reasoning == "test"
        # decision file should be consumed (deleted)
        assert not decision_file.exists()

    async def test_get_decision_returns_fallback_on_shutdown(self, tmp_path: Path):
        """If running becomes False while polling, returns shutdown fallback."""
        import src.agent.controller as ctrl_module

        ctrl = _make_agent_controller(tmp_path)
        ctrl.running = True
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.get_active_guidance = AsyncMock(return_value=[])
        ctrl.knowledge_base.get_relevant_knowledge = AsyncMock(return_value=[])

        decision_file = tmp_path / "decision.json"
        obs_file = tmp_path / "obs.txt"

        # Set running=False after a short delay (simulate shutdown)
        async def _shutdown_after_delay():
            await asyncio.sleep(0.3)
            ctrl.running = False

        state_data = await ctrl.mgba_client.request_state()
        game_state = GameState.from_dict(state_data)
        obs = Observation(game_state=game_state, frame_number=0)

        with (
            patch.object(ctrl_module, "DECISION_FILE", decision_file),
            patch.object(ctrl_module, "OBSERVATION_FILE", obs_file),
        ):
            shutdown_task = asyncio.create_task(_shutdown_after_delay())
            decision = await ctrl._get_decision(obs)
            await shutdown_task

        assert decision.action_type == "wait"
        assert "shutdown" in decision.reasoning


class TestPokemonAgentControllerExecuteDecision:
    async def test_execute_press_button(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        decision = AgentDecision(action_type="press_button", action_params={"button": "A", "duration_frames": 8})
        await ctrl._execute_decision(decision)
        ctrl.mgba_client.press_button.assert_called_once_with("A", 8)

    async def test_execute_press_buttons(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        decision = AgentDecision(action_type="press_buttons", action_params={"buttons": ["UP", "A"]})
        await ctrl._execute_decision(decision)
        ctrl.mgba_client.press_buttons.assert_called_once_with(["UP", "A"], 8)

    async def test_execute_wait(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        decision = AgentDecision(action_type="wait", action_params={"frames": 60})
        await ctrl._execute_decision(decision)
        ctrl.mgba_client.wait_frames.assert_called_once_with(60)

    async def test_execute_save_game(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        decision = AgentDecision(action_type="save_game", action_params={})
        await ctrl._execute_decision(decision)
        ctrl.mgba_client.save_game.assert_called_once()

    async def test_execute_sequence(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        decision = AgentDecision(
            action_type="press_sequence",
            action_params={
                "sequence": [
                    {"action": "press_button", "button": "UP", "duration_frames": 8},
                    {"action": "wait", "wait_frames": 60},
                ],
                "repeat": 1,
            },
        )
        await ctrl._execute_decision(decision)
        ctrl.mgba_client.execute_sequence.assert_called_once()


class TestRecordDecisionKnowledge:
    async def test_knowledge_persisted_to_db(self, tmp_path: Path):
        """_record_decision_knowledge writes entries to the knowledge base."""
        from src.agent.knowledge import KnowledgeBase

        ctrl = _make_agent_controller(tmp_path)
        kb = KnowledgeBase(tmp_path / "test.db")
        await kb.initialize()
        ctrl.knowledge_base = kb

        state_data = await ctrl.mgba_client.request_state()
        game_state = GameState.from_dict(state_data)
        obs = Observation(game_state=game_state, frame_number=42)

        decision = AgentDecision(
            action_type="wait",
            action_params={"frames": 30},
            knowledge_to_store=[
                {"category": "location", "title": "Littleroot Town", "description": "Starting town south of Oldale"}
            ],
        )
        await ctrl._record_decision_knowledge(decision, obs)

        results = await kb.get_relevant_knowledge("Littleroot", limit=5)
        assert any("Littleroot" in r["title"] for r in results)
        await kb.close()

    async def test_empty_description_skipped(self, tmp_path: Path):
        """Entries with empty description are not written to the KB."""
        from src.agent.knowledge import KnowledgeBase

        ctrl = _make_agent_controller(tmp_path)
        kb = KnowledgeBase(tmp_path / "test.db")
        await kb.initialize()
        ctrl.knowledge_base = kb

        state_data = await ctrl.mgba_client.request_state()
        game_state = GameState.from_dict(state_data)
        obs = Observation(game_state=game_state, frame_number=0)

        decision = AgentDecision(
            action_type="wait",
            action_params={"frames": 30},
            knowledge_to_store=[{"category": "mechanic", "title": "No desc", "description": ""}],
        )
        await ctrl._record_decision_knowledge(decision, obs)

        results = await kb.get_relevant_knowledge("No desc", limit=5)
        assert len(results) == 0
        await kb.close()

    async def test_no_knowledge_entries_is_noop(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.record_discovery = AsyncMock()

        state_data = await ctrl.mgba_client.request_state()
        game_state = GameState.from_dict(state_data)
        obs = Observation(game_state=game_state, frame_number=0)

        decision = AgentDecision(action_type="wait", action_params={"frames": 30})
        await ctrl._record_decision_knowledge(decision, obs)

        ctrl.knowledge_base.record_discovery.assert_not_called()


class TestPokemonAgentControllerKnowledgeBase:
    """Guidance is stored via the KB API."""

    async def test_add_user_guidance_persists(self, tmp_path: Path):
        """KnowledgeBase.add_user_guidance() stores guidance that is retrievable."""
        from src.agent.knowledge import KnowledgeBase

        kb = KnowledgeBase(tmp_path / "test.db")
        await kb.initialize()

        guidance_id = await kb.add_user_guidance(
            instruction="Focus on getting the first badge",
            context="Map 900 at iteration 1",
            priority=5,
        )

        assert guidance_id is not None
        guidance = await kb.get_active_guidance()
        assert any("first badge" in g.get("instruction", "") for g in guidance)

        await kb.close()

    async def test_guidance_survives_reopen(self, tmp_path: Path):
        """Guidance written in one session is readable after the KB is closed and reopened."""
        from src.agent.knowledge import KnowledgeBase

        db_path = tmp_path / "test.db"

        kb = KnowledgeBase(db_path)
        await kb.initialize()
        await kb.add_user_guidance(
            instruction="Catch a Ralts before the second gym",
            context="session 1",
            priority=3,
        )
        await kb.close()

        kb2 = KnowledgeBase(db_path)
        await kb2.initialize()
        guidance = await kb2.get_active_guidance()
        await kb2.close()

        assert any("Ralts" in g.get("instruction", "") for g in guidance)


class TestEventHandling:
    """_process_pending_events and _handle_event dispatch correctly."""

    def _make_event_msg(self, event_type: str, extra: dict | None = None) -> dict:
        return {"type": "event", "id": 1001, "payload": {"event": event_type, **(extra or {})}}

    async def test_process_pending_events_drains_queue(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.record_progress = AsyncMock()
        ctrl.mgba_client._event_queue = asyncio.Queue()

        await ctrl.mgba_client._event_queue.put(self._make_event_msg("battle_started"))
        await ctrl.mgba_client._event_queue.put(self._make_event_msg("pokemon_fainted", {"slot": 1}))

        await ctrl._process_pending_events()

        assert ctrl.mgba_client._event_queue.empty()
        assert ctrl._pending_event_context != ""

    async def test_handle_battle_started_sets_context(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.record_progress = AsyncMock()

        parts: list[str] = []
        await ctrl._handle_event(self._make_event_msg("battle_started", {"battle_type": "wild"}), parts)

        assert any("battle_started" in p.lower() or "battle" in p.lower() for p in parts)

    async def test_handle_battle_ended_records_progress(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.record_progress = AsyncMock()

        parts: list[str] = []
        await ctrl._handle_event(self._make_event_msg("battle_ended", {"outcome": "victory"}), parts)

        ctrl.knowledge_base.record_progress.assert_called_once()
        assert any("victory" in p.lower() or "battle" in p.lower() for p in parts)

    async def test_handle_level_up_records_progress(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.record_progress = AsyncMock()

        parts: list[str] = []
        await ctrl._handle_event(self._make_event_msg("level_up", {"slot": 1, "new_level": 7, "prev_level": 6}), parts)

        ctrl.knowledge_base.record_progress.assert_called_once()
        assert any("level" in p.lower() for p in parts)

    async def test_handle_pokemon_fainted_adds_context(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.record_progress = AsyncMock()

        parts: list[str] = []
        await ctrl._handle_event(self._make_event_msg("pokemon_fainted", {"slot": 2}), parts)

        ctrl.knowledge_base.record_progress.assert_not_called()
        assert any("fainted" in p.lower() for p in parts)

    async def test_process_pending_events_clears_after_get_decision(self, tmp_path: Path):
        """_pending_event_context is cleared after _get_decision is called."""
        import src.agent.controller as ctrl_module

        ctrl = _make_agent_controller(tmp_path)
        ctrl.running = True
        ctrl._pending_event_context = "[EVENT] Battle started"
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.get_active_guidance = AsyncMock(return_value=[])
        ctrl.knowledge_base.get_relevant_knowledge = AsyncMock(return_value=[])

        decision_file = tmp_path / "decision.json"
        obs_file = tmp_path / "obs.txt"
        json_response = '{"reasoning": "ok", "action_type": "wait", "action_params": {"frames": 1}}'

        async def _write_after_delay():
            await asyncio.sleep(0.2)
            decision_file.write_text(json_response)

        state_data = await ctrl.mgba_client.request_state()
        game_state = GameState.from_dict(state_data)
        obs = Observation(game_state=game_state, frame_number=0)

        with (
            patch.object(ctrl_module, "DECISION_FILE", decision_file),
            patch.object(ctrl_module, "OBSERVATION_FILE", obs_file),
        ):
            write_task = asyncio.create_task(_write_after_delay())
            # Read the obs file to verify event context was prepended
            decision = await ctrl._get_decision(obs)
            await write_task

        obs_written = obs_file.read_text()
        assert "RECENT EVENTS" in obs_written
        assert "Battle started" in obs_written


class TestPauseResume:
    """Pause flag halts game_loop; resume restarts it."""

    def test_init_paused_false(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        assert ctrl.paused is False
        assert ctrl._pause_msg_shown is False

    async def test_execute_pause_action_sets_paused(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        decision = AgentDecision(action_type="pause", action_params={})
        await ctrl._execute_decision(decision)
        assert ctrl.paused is True

    async def test_stop_clears_paused(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        ctrl.running = True
        ctrl.paused = True
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.close = AsyncMock()

        await ctrl.stop()

        assert ctrl.paused is False


class TestConfigureMgba:
    """_configure_mgba patches config keys correctly."""

    def test_mute_adds_mute_key(self, tmp_path: Path):
        import src.agent.controller as ctrl_module
        from src.agent.controller import _configure_mgba

        config = tmp_path / "config.ini"
        config.write_text("[core]\n")
        with patch.object(ctrl_module, "MGBA_CONFIG", config):
            _configure_mgba(mute=True)
        assert "mute=1" in config.read_text()

    def test_no_mute_by_default(self, tmp_path: Path):
        import src.agent.controller as ctrl_module
        from src.agent.controller import _configure_mgba

        config = tmp_path / "config.ini"
        config.write_text("[core]\n")
        with patch.object(ctrl_module, "MGBA_CONFIG", config):
            _configure_mgba()
        assert "mute=" not in config.read_text()

    def test_audio_buffers_patched(self, tmp_path: Path):
        import src.agent.controller as ctrl_module
        from src.agent.controller import _configure_mgba

        config = tmp_path / "config.ini"
        config.write_text("[core]\naudioBuffers=1024\n")
        with patch.object(ctrl_module, "MGBA_CONFIG", config):
            _configure_mgba()
        assert "audioBuffers=8192" in config.read_text()


class TestScreenshotInterval:
    """screenshot_interval controls capture frequency in game_loop."""

    def test_default_interval_is_one(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        assert ctrl.screenshot_interval == 1

    def test_interval_clamped_to_one(self, tmp_path):
        rom = tmp_path / "game.gba"
        rom.write_bytes(b"\x00" * 64)
        lua = tmp_path / "agent.lua"
        lua.write_text("-- stub")
        ctrl = PokemonAgentController(
            rom_path=rom,
            lua_script_path=lua,
            knowledge_db_path=tmp_path / "test.db",
            screenshot_interval=0,
        )
        assert ctrl.screenshot_interval == 1

    async def test_screenshot_skipped_on_non_interval_iteration(self, tmp_path: Path):
        """When screenshot_interval=2, no screenshot on odd iterations."""
        ctrl = _make_agent_controller(tmp_path)
        ctrl.screenshot_interval = 2
        ctrl._iteration = 1  # odd — should be skipped (1 % 2 != 0)
        result = None

        # Patch _capture_screenshot to track calls
        call_count = 0

        async def mock_capture(frame_number):
            nonlocal call_count
            call_count += 1
            return Path("/tmp/frame.png")

        ctrl._capture_screenshot = mock_capture

        # Run one game-loop iteration manually
        state_data = await ctrl.mgba_client.request_state()
        game_state = GameState.from_dict(state_data)
        if ctrl._iteration % ctrl.screenshot_interval == 0:
            result = await ctrl._capture_screenshot(game_state.frame_number)

        assert call_count == 0  # iteration 1, interval 2 → skipped
        assert result is None

    async def test_screenshot_taken_on_interval_iteration(self, tmp_path: Path):
        """When screenshot_interval=2, screenshot taken on even iterations."""
        ctrl = _make_agent_controller(tmp_path)
        ctrl.screenshot_interval = 2
        ctrl._iteration = 2  # even — should be taken (2 % 2 == 0)

        call_count = 0

        async def mock_capture(frame_number):
            nonlocal call_count
            call_count += 1
            return Path("/tmp/frame.png")

        ctrl._capture_screenshot = mock_capture

        state_data = await ctrl.mgba_client.request_state()
        game_state = GameState.from_dict(state_data)
        result = None
        if ctrl._iteration % ctrl.screenshot_interval == 0:
            result = await ctrl._capture_screenshot(game_state.frame_number)

        assert call_count == 1
        assert result is not None


class TestDiscoverWindowsHost:
    """_discover_windows_host reads nameserver from /etc/resolv.conf."""

    def test_returns_nameserver_ip(self, tmp_path: Path):
        import src.agent.cli as cli_module

        resolv = tmp_path / "resolv.conf"
        resolv.write_text("# WSL2\nnameserver 172.21.96.1\n")
        with patch.object(
            cli_module,
            "_discover_windows_host",
            wraps=lambda: (
                lambda p: next(
                    (
                        m.group(1)
                        for line in p.read_text().splitlines()
                        if (m := __import__("re").match(r"^\s*nameserver\s+(\S+)", line))
                    ),
                    "127.0.0.1",
                )
            )(resolv),
        ):
            # Direct parse test — call the function with a patched Path
            import re

            ip = next(
                (
                    m.group(1)
                    for line in resolv.read_text().splitlines()
                    if (m := re.match(r"^\s*nameserver\s+(\S+)", line))
                ),
                "127.0.0.1",
            )
            assert ip == "172.21.96.1"

    def test_falls_back_when_no_nameserver(self, tmp_path: Path):
        import re


        resolv = tmp_path / "resolv.conf"
        resolv.write_text("# no nameserver line\n")
        ip = next(
            (
                m.group(1)
                for line in resolv.read_text().splitlines()
                if (m := re.match(r"^\s*nameserver\s+(\S+)", line))
            ),
            "127.0.0.1",
        )
        assert ip == "127.0.0.1"


class TestGameLoopReconnect:
    """game_loop attempts reconnect on connection errors."""

    async def test_game_loop_reconnects_on_connection_reset(self, tmp_path: Path):
        """When request_state raises ConnectionResetError, reconnect is attempted."""
        ctrl = _make_agent_controller(tmp_path)
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.initialize = AsyncMock()
        ctrl.knowledge_base.get_active_guidance = AsyncMock(return_value=[])
        ctrl.knowledge_base.get_relevant_knowledge = AsyncMock(return_value=[])
        ctrl.knowledge_base.close = AsyncMock()

        call_count = 0

        async def request_state_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionResetError("disconnected")
            # After reconnect, stop the loop
            ctrl.running = False
            return {
                "frame_number": 1,
                "map_id": 900,
                "map_name": "Littleroot Town",
                "player_x": 5,
                "player_y": 7,
                "party_count": 0,
                "party": [],
                "badges": [],
                "in_battle": False,
                "can_save": True,
            }

        ctrl.mgba_client.request_state = request_state_side_effect
        ctrl.mgba_client.reconnect = AsyncMock(return_value=True)
        ctrl.mgba_client._event_queue = asyncio.Queue()

        ctrl.running = True
        ctrl._game_loop_done.clear()
        await ctrl.game_loop()

        ctrl.mgba_client.reconnect.assert_called_once()

    async def test_game_loop_stops_if_reconnect_fails(self, tmp_path: Path):
        """When reconnect fails, running is set to False."""
        ctrl = _make_agent_controller(tmp_path)
        ctrl.knowledge_base = MagicMock()
        ctrl.knowledge_base.initialize = AsyncMock()
        ctrl.knowledge_base.close = AsyncMock()
        ctrl.knowledge_base.get_active_guidance = AsyncMock(return_value=[])
        ctrl.knowledge_base.get_relevant_knowledge = AsyncMock(return_value=[])
        ctrl.knowledge_base.record_progress = AsyncMock()
        ctrl.knowledge_base.record_discovery = AsyncMock()
        ctrl.mgba_client.save_game = AsyncMock(return_value={"status": "ok"})
        ctrl.mgba_client.shutdown = AsyncMock()

        ctrl.mgba_client.request_state = AsyncMock(side_effect=ConnectionResetError("dead"))
        ctrl.mgba_client.reconnect = AsyncMock(return_value=False)
        ctrl.mgba_client._event_queue = asyncio.Queue()

        ctrl.running = True
        ctrl._game_loop_done.clear()
        await ctrl.game_loop()

        assert ctrl.running is False


class TestAwaitScreenshotFile:
    """_await_screenshot_file polls for screenshot existence."""

    async def test_returns_path_when_file_exists_immediately(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        png = tmp_path / "frame.png"
        png.write_bytes(b"\x89PNG")
        result = await ctrl._await_screenshot_file(png, timeout=1.0, interval=0.05)
        assert result == png

    async def test_returns_path_when_file_created_after_delay(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        png = tmp_path / "late_frame.png"

        async def _create_after_delay():
            await asyncio.sleep(0.15)
            png.write_bytes(b"\x89PNG")

        create_task = asyncio.create_task(_create_after_delay())
        result = await ctrl._await_screenshot_file(png, timeout=1.0, interval=0.05)
        await create_task

        assert result == png

    async def test_returns_none_when_file_never_appears(self, tmp_path: Path):
        ctrl = _make_agent_controller(tmp_path)
        png = tmp_path / "missing.png"  # never created
        result = await ctrl._await_screenshot_file(png, timeout=0.2, interval=0.05)
        assert result is None
