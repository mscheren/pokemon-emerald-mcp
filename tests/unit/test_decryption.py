"""Unit tests for BoxPokemon decryption integration in PartyPokemon.from_dict().

The actual XOR decryption runs in Lua. These tests verify that the Python
model correctly reads the pre-decrypted species_id and move_ids fields
that Lua provides in the get_state payload.
"""
import pytest
from src.agent.models import PartyPokemon


class TestPartyPokemonDecryptedFields:
    def _base(self, **kwargs) -> dict:
        base = {
            "slot": 1,
            "nickname": "TORCHIC",
            "level": 5,
            "current_hp": 20,
            "max_hp": 20,
            "status": "healthy",
        }
        base.update(kwargs)
        return base

    def test_species_id_populated(self):
        p = PartyPokemon.from_dict(self._base(species_id=280))
        assert p.species_id == 280

    def test_species_id_none_when_absent(self):
        p = PartyPokemon.from_dict(self._base())
        assert p.species_id is None

    def test_move_ids_populated(self):
        p = PartyPokemon.from_dict(self._base(move_ids=[33, 46, 0, 0]))
        assert p.move_ids == [33, 46]

    def test_move_ids_filters_zeros(self):
        p = PartyPokemon.from_dict(self._base(move_ids=[10, 0, 20, 0]))
        assert 0 not in p.move_ids
        assert 10 in p.move_ids
        assert 20 in p.move_ids

    def test_move_ids_empty_when_absent(self):
        p = PartyPokemon.from_dict(self._base())
        assert p.move_ids == []

    def test_move_ids_four_moves(self):
        p = PartyPokemon.from_dict(self._base(move_ids=[33, 46, 52, 98]))
        assert p.move_ids == [33, 46, 52, 98]

    def test_full_decrypted_payload(self):
        """Simulate the full payload Lua would send after decryption."""
        payload = {
            "slot": 1,
            "nickname": "TORCHIC",
            "level": 5,
            "current_hp": 20,
            "max_hp": 20,
            "attack": 60,
            "defense": 40,
            "speed": 45,
            "sp_attack": 70,
            "sp_defense": 50,
            "status": "healthy",
            "species_id": 280,
            "species_name": "Torchic",
            "types": [],
            "moves": [],
            "move_ids": [33, 46, 0, 0],
        }
        p = PartyPokemon.from_dict(payload)
        assert p.species_id == 280
        assert p.species_name == "Torchic"
        assert p.move_ids == [33, 46]
        assert p.level == 5
