"""Unit tests for ObservationFormatter and DecisionParser."""

from src.agent.formatter import DecisionParser, ObservationFormatter
from src.agent.models import BagItem, ExtendedState, GameState, Observation, PartyPokemon, PCPokemon


def _make_observation(screenshot_path=None) -> Observation:
    gs = GameState(
        frame_number=100,
        map_id=900,
        map_name="Littleroot Town",
        player_x=5,
        player_y=7,
        party_count=1,
        party=[
            PartyPokemon(
                slot=1,
                nickname="TORCHIC",
                level=5,
                current_hp=20,
                max_hp=20,
                status="healthy",
                species_name="Torchic",
            )
        ],
        badges=[],
        in_battle=False,
        can_save=True,
    )
    return Observation(game_state=gs, frame_number=100, screenshot_path=screenshot_path)


class TestObservationFormatter:
    def test_format_contains_map_info(self):
        obs = _make_observation()
        text = ObservationFormatter().format(obs, [], [])
        assert "Littleroot Town" in text
        assert "900" in text

    def test_format_contains_party(self):
        obs = _make_observation()
        text = ObservationFormatter().format(obs, [], [])
        assert "TORCHIC" in text
        assert "Lv.5" in text
        assert "20/20" in text

    def test_format_no_badges(self):
        obs = _make_observation()
        text = ObservationFormatter().format(obs, [], [])
        assert "None yet" in text

    def test_format_with_guidance(self):
        obs = _make_observation()
        guidance = [{"priority": 5, "instruction": "Get the first badge"}]
        text = ObservationFormatter().format(obs, guidance, [])
        assert "ACTIVE USER GUIDANCE" in text
        assert "Get the first badge" in text

    def test_format_with_knowledge(self):
        obs = _make_observation()
        knowledge = [{"category": "location", "title": "Route 101", "description": "First route"}]
        text = ObservationFormatter().format(obs, [], knowledge)
        assert "RELEVANT KNOWLEDGE" in text
        assert "Route 101" in text

    def test_format_does_not_include_decision_schema(self):
        # The JSON schema is in CLAUDE.md (ingested once), not repeated per iteration.
        obs = _make_observation()
        text = ObservationFormatter().format(obs, [], [])
        assert "YOUR DECISION" not in text
        assert "action_type" not in text

    def test_format_screenshot_no_path_in_text(self, tmp_path):
        # Screenshot is delivered inline as an MCP image block — path must not appear in text.
        png = tmp_path / "frame.png"
        png.write_bytes(b"")
        obs = _make_observation(screenshot_path=png)
        text = ObservationFormatter().format(obs, [], [])
        assert "frame.png" not in text

    def test_format_screenshot_no_heading_in_text(self, tmp_path):
        png = tmp_path / "frame.png"
        png.write_bytes(b"")
        obs = _make_observation(screenshot_path=png)
        text = ObservationFormatter().format(obs, [], [])
        assert "## CURRENT SCREENSHOT" not in text

    def test_format_screenshot_no_read_instruction(self, tmp_path):
        png = tmp_path / "frame.png"
        png.write_bytes(b"")
        obs = _make_observation(screenshot_path=png)
        text = ObservationFormatter().format(obs, [], [])
        assert "Read this file with the Read tool" not in text

    def test_format_no_screenshot_shows_warning(self):
        obs = _make_observation(screenshot_path=None)
        text = ObservationFormatter().format(obs, [], [])
        assert "No screenshot" in text

    def test_hp_bar_full(self):
        bar = ObservationFormatter()._hp_bar(20, 20)
        assert "█" * 10 in bar

    def test_hp_bar_empty(self):
        bar = ObservationFormatter()._hp_bar(0, 20)
        assert "─" * 10 in bar

    def test_hp_bar_zero_max(self):
        bar = ObservationFormatter()._hp_bar(0, 0)
        assert "----------" in bar


class TestDecisionParser:
    def _parser(self):
        return DecisionParser()

    def test_parse_fenced_json(self):
        response = '```json\n{"reasoning": "go north", "action_type": "press_button", "action_params": {"button": "UP", "duration_frames": 8}, "knowledge_to_store": []}\n```'
        decision = self._parser().parse(response)
        assert decision.action_type == "press_button"
        assert decision.action_params["button"] == "UP"
        assert decision.reasoning == "go north"

    def test_parse_bare_json(self):
        response = (
            '{"action_type": "wait", "action_params": {"frames": 60}, "reasoning": "rest", "knowledge_to_store": []}'
        )
        decision = self._parser().parse(response)
        assert decision.action_type == "wait"
        assert decision.action_params["frames"] == 60

    def test_parse_invalid_json_returns_wait(self):
        # No { in text → _extract_json returns "{}" → valid JSON, default wait
        decision = self._parser().parse("not json at all")
        assert decision.action_type == "wait"
        assert decision.action_params.get("frames", 0) > 0

    def test_parse_invalid_action_type_falls_back(self):
        response = '{"action_type": "fly_to_pallet", "action_params": {}, "reasoning": ""}'
        decision = self._parser().parse(response)
        assert decision.action_type == "wait"

    def test_parse_invalid_button_defaults_to_a(self):
        response = '{"action_type": "press_button", "action_params": {"button": "INVALID"}, "reasoning": ""}'
        decision = self._parser().parse(response)
        assert decision.action_params["button"] == "A"

    def test_parse_press_buttons_filters_invalid(self):
        response = (
            '{"action_type": "press_buttons", "action_params": {"buttons": ["UP", "INVALID", "A"]}, "reasoning": ""}'
        )
        decision = self._parser().parse(response)
        assert "INVALID" not in decision.action_params["buttons"]
        assert "UP" in decision.action_params["buttons"]

    def test_parse_knowledge_to_store(self):
        response = '{"action_type": "wait", "action_params": {"frames": 30}, "reasoning": "r", "knowledge_to_store": [{"category": "location", "title": "Test", "description": "desc"}]}'
        decision = self._parser().parse(response)
        assert len(decision.knowledge_to_store) == 1
        assert decision.knowledge_to_store[0]["title"] == "Test"

    def test_parse_empty_response(self):
        decision = self._parser().parse("")
        assert decision.action_type == "wait"

    def test_valid_actions_set(self):
        for action in ("press_button", "press_buttons", "wait", "save_game", "pause"):
            assert action in DecisionParser.VALID_ACTIONS

    def test_valid_buttons_set(self):
        for btn in ("A", "B", "UP", "DOWN", "LEFT", "RIGHT", "START", "SELECT", "L", "R"):
            assert btn in DecisionParser.VALID_BUTTONS

    def test_screenshot_required_true_sets_field(self):
        response = '{"action_type": "wait", "action_params": {"frames": 30}, "reasoning": "r", "knowledge_to_store": [], "screenshot_required": true}'
        decision = self._parser().parse(response)
        assert decision.screenshot_required is True

    def test_screenshot_required_missing_logs_warning(self, caplog):
        import logging

        response = (
            '{"action_type": "wait", "action_params": {"frames": 30}, "reasoning": "r", "knowledge_to_store": []}'
        )
        with caplog.at_level(logging.WARNING, logger="src.agent.formatter"):
            decision = self._parser().parse(response)
        assert decision.screenshot_required is False
        assert "screenshot_required" in caplog.text

    def test_screenshot_required_false_logs_warning(self, caplog):
        import logging

        response = '{"action_type": "wait", "action_params": {"frames": 30}, "reasoning": "r", "knowledge_to_store": [], "screenshot_required": false}'
        with caplog.at_level(logging.WARNING, logger="src.agent.formatter"):
            decision = self._parser().parse(response)
        assert decision.screenshot_required is False
        assert "screenshot_required" in caplog.text


class TestFormatterExtendedState:
    def _obs_with_extended(self, extended_state):
        gs = GameState(frame_number=1, map_id=0, map_name="X", player_x=0, player_y=0)
        return Observation(game_state=gs, frame_number=1, extended_state=extended_state)

    def test_bag_section_shown(self):
        es = ExtendedState(
            bag={"pokeballs": [BagItem(item_id=4, name="Poke Ball", quantity=5)]},
            pc_boxes=[],
        )
        text = ObservationFormatter().format(self._obs_with_extended(es), [], [])
        assert "BAG:" in text
        assert "Poke Ball x5" in text

    def test_empty_pocket_shown(self):
        es = ExtendedState(bag={"key_items": []}, pc_boxes=[])
        text = ObservationFormatter().format(self._obs_with_extended(es), [], [])
        assert "key_items: (empty)" in text

    def test_pc_boxes_section_shown(self):
        es = ExtendedState(
            bag={},
            pc_boxes=[{"box": 1, "pokemon": [PCPokemon(box=1, slot=1, nickname="ZIGZAGOON")]}],
        )
        text = ObservationFormatter().format(self._obs_with_extended(es), [], [])
        assert "PC BOXES:" in text
        assert "ZIGZAGOON" in text

    def test_no_extended_state_no_bag_section(self):
        obs = _make_observation()
        text = ObservationFormatter().format(obs, [], [])
        assert "BAG:" not in text
        assert "PC BOXES:" not in text


class TestPlayerStateSection:
    """Tests for the PLAYER STATE section of the formatted observation."""

    def test_format_includes_instructions(self):
        obs = _make_observation()
        text = ObservationFormatter().format(obs, [], [])
        assert "IMPORTANT INSTRUCTIONS" in text

    def test_no_neighbours_line(self):
        obs = _make_observation()
        text = ObservationFormatter().format(obs, [], [])
        assert "Neighbours:" not in text

    def test_no_map_tiles_ascii_grid(self):
        obs = _make_observation()
        text = ObservationFormatter().format(obs, [], [])
        assert "MAP TILES" not in text
