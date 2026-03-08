"""Unit tests for src/agent/models.py."""

import pytest

from src.agent.models import (
    AgentDecision,
    GameState,
    KnowledgeEntry,
    Move,
    Observation,
    PartyPokemon,
    SequenceStep,
    UserGuidance,
)


class TestMove:
    def test_from_dict_full(self):
        data = {
            "name": "Ember",
            "type": "Fire",
            "power": 40,
            "pp": 25,
            "max_pp": 25,
            "category": "special",
        }
        m = Move.from_dict(data)
        assert m.name == "Ember"
        assert m.type == "Fire"
        assert m.power == 40
        assert m.pp == 25
        assert m.max_pp == 25
        assert m.category == "special"

    def test_from_dict_status_move_no_power(self):
        data = {"name": "Growl", "type": "Normal", "pp": 40, "max_pp": 40, "category": "status"}
        m = Move.from_dict(data)
        assert m.power is None

    def test_from_dict_defaults(self):
        m = Move.from_dict({})
        assert m.name == "???"
        assert m.type == "Normal"
        assert m.power is None
        assert m.pp == 0
        assert m.category == "status"


class TestPartyPokemon:
    BASE = {
        "slot": 1,
        "nickname": "Torchic",
        "level": 5,
        "current_hp": 20,
        "max_hp": 20,
        "attack": 10,
        "defense": 8,
        "speed": 12,
        "sp_attack": 9,
        "sp_defense": 7,
    }

    def test_from_dict_basic(self):
        p = PartyPokemon.from_dict(self.BASE)
        assert p.slot == 1
        assert p.nickname == "Torchic"
        assert p.level == 5
        assert p.current_hp == 20
        assert p.max_hp == 20
        assert p.status == "healthy"
        assert p.types == []
        assert p.moves == []

    def test_from_dict_with_types(self):
        p = PartyPokemon.from_dict({**self.BASE, "types": ["Fire"]})
        assert p.types == ["Fire"]

    def test_from_dict_dual_type(self):
        p = PartyPokemon.from_dict({**self.BASE, "types": ["Water", "Flying"]})
        assert p.types == ["Water", "Flying"]

    def test_from_dict_with_move_dicts(self):
        moves_data = [
            {"name": "Ember", "type": "Fire", "power": 40, "pp": 25, "max_pp": 25, "category": "special"},
            {"name": "Scratch", "type": "Normal", "power": 40, "pp": 35, "max_pp": 35, "category": "physical"},
        ]
        p = PartyPokemon.from_dict({**self.BASE, "moves": moves_data})
        assert len(p.moves) == 2
        assert isinstance(p.moves[0], Move)
        assert p.moves[0].name == "Ember"
        assert p.moves[0].type == "Fire"
        assert p.moves[1].name == "Scratch"

    def test_from_dict_moves_pp_on_move(self):
        moves_data = [{"name": "Ember", "type": "Fire", "power": 40, "pp": 10, "max_pp": 25, "category": "special"}]
        p = PartyPokemon.from_dict({**self.BASE, "moves": moves_data})
        assert p.moves[0].pp == 10
        assert p.moves[0].max_pp == 25

    def test_from_dict_legacy_string_moves(self):
        p = PartyPokemon.from_dict({**self.BASE, "moves": ["Ember", "Scratch"]})
        assert len(p.moves) == 2
        assert p.moves[0].name == "Ember"
        assert p.moves[0].type == "Normal"  # default when only name is known

    def test_hp_percent(self):
        p = PartyPokemon.from_dict({**self.BASE, "current_hp": 10, "max_hp": 20})
        assert p.hp_percent == 0.5

    def test_hp_percent_zero_max(self):
        p = PartyPokemon.from_dict({**self.BASE, "current_hp": 0, "max_hp": 0})
        assert p.hp_percent == 0.0

    def test_level_clamped(self):
        p = PartyPokemon.from_dict({**self.BASE, "level": 999})
        assert p.level == 100

    def test_invalid_status_defaults_to_healthy(self):
        p = PartyPokemon.from_dict({**self.BASE, "status": "on_fire"})
        assert p.status == "healthy"

    def test_current_hp_capped_at_max(self):
        p = PartyPokemon.from_dict({**self.BASE, "current_hp": 999, "max_hp": 20})
        assert p.current_hp == 20


class TestGameState:
    def test_from_dict_empty(self):
        gs = GameState.from_dict({})
        assert gs.frame_number == 0
        assert gs.map_id == 0
        assert gs.party == []
        assert gs.badges == []
        assert gs.in_battle is False

    def test_from_dict_with_party(self):
        data = {
            "frame_number": 100,
            "map_id": 1,
            "player_x": 5,
            "player_y": 10,
            "party_count": 1,
            "party": [
                {
                    "slot": 1,
                    "nickname": "Torchic",
                    "level": 5,
                    "current_hp": 20,
                    "max_hp": 20,
                    "attack": 10,
                    "defense": 8,
                    "speed": 12,
                    "sp_attack": 9,
                    "sp_defense": 7,
                    "types": ["Fire"],
                }
            ],
            "badges": ["Stone Badge"],
            "in_battle": True,
            "can_save": False,
        }
        gs = GameState.from_dict(data)
        assert gs.frame_number == 100
        assert gs.map_id == 1
        assert gs.party_count == 1
        assert len(gs.party) == 1
        assert gs.party[0].nickname == "Torchic"
        assert gs.party[0].types == ["Fire"]
        assert gs.badges == ["Stone Badge"]
        assert gs.in_battle is True
        assert gs.can_save is False


class TestSequenceStep:
    def test_from_dict_press_button(self):
        s = SequenceStep.from_dict({"action": "press_button", "button": "UP", "duration_frames": 16})
        assert s.action == "press_button"
        assert s.button == "UP"
        assert s.duration_frames == 16
        s.validate()  # should not raise

    def test_from_dict_press_buttons(self):
        s = SequenceStep.from_dict({"action": "press_buttons", "buttons": ["A", "B"], "duration_frames": 8})
        assert s.buttons == ["A", "B"]
        s.validate()

    def test_from_dict_wait(self):
        s = SequenceStep.from_dict({"action": "wait", "wait_frames": 30})
        assert s.wait_frames == 30
        s.validate()

    def test_from_dict_defaults(self):
        s = SequenceStep.from_dict({"action": "wait"})
        assert s.duration_frames == 8
        assert s.wait_frames == 0

    def test_validate_invalid_action(self):
        s = SequenceStep(action="fly")
        with pytest.raises(ValueError, match="Invalid sequence step action"):
            s.validate()

    def test_validate_press_button_missing_button(self):
        s = SequenceStep(action="press_button")
        with pytest.raises(ValueError, match="requires a 'button'"):
            s.validate()

    def test_validate_press_buttons_empty_list(self):
        s = SequenceStep(action="press_buttons")
        with pytest.raises(ValueError, match="non-empty 'buttons'"):
            s.validate()


class TestAgentDecision:
    def test_validate_valid(self):
        d = AgentDecision(action_type="press_button", action_params={"button": "A"})
        d.validate()  # should not raise

    def test_validate_invalid(self):
        d = AgentDecision(action_type="fly", action_params={})
        with pytest.raises(ValueError):
            d.validate()

    def test_validate_press_sequence_valid(self):
        d = AgentDecision(
            action_type="press_sequence",
            action_params={
                "sequence": [
                    {"action": "press_button", "button": "UP", "duration_frames": 16},
                    {"action": "wait", "wait_frames": 4},
                ]
            },
        )
        d.validate()  # should not raise

    def test_validate_press_sequence_empty(self):
        d = AgentDecision(action_type="press_sequence", action_params={"sequence": []})
        with pytest.raises(ValueError, match="non-empty 'sequence'"):
            d.validate()

    def test_validate_press_sequence_bad_step(self):
        d = AgentDecision(
            action_type="press_sequence",
            action_params={"sequence": [{"action": "fly"}]},
        )
        with pytest.raises(ValueError, match="sequence step 0"):
            d.validate()

    def test_knowledge_to_store_default_empty(self):
        d = AgentDecision(action_type="wait", action_params={})
        assert d.knowledge_to_store == []
