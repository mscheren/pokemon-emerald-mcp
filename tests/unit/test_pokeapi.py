"""Unit tests for PokeAPIClient — happy path, cache hits, and error handling."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.pokeapi import PokeAPIClient, _rse_to_ndex, _walk_chain


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

POKEMON_PAYLOAD = {
    "name": "torchic",
    "types": [{"type": {"name": "fire"}}],
    "stats": [
        {"stat": {"name": "hp"}, "base_stat": 45},
        {"stat": {"name": "attack"}, "base_stat": 60},
    ],
    "species": {"url": "https://pokeapi.co/api/v2/pokemon-species/255/"},
}

SPECIES_PAYLOAD = {
    "evolution_chain": {"url": "https://pokeapi.co/api/v2/evolution-chain/176/"},
}

CHAIN_PAYLOAD = {
    "chain": {
        "species": {"name": "torchic"},
        "evolves_to": [{
            "species": {"name": "combusken"},
            "evolves_to": [{
                "species": {"name": "blaziken"},
                "evolves_to": [],
            }],
        }],
    }
}

MOVE_PAYLOAD = {
    "name": "tackle",
    "type": {"name": "normal"},
    "power": 40,
    "accuracy": 100,
    "pp": 35,
}

ITEM_PAYLOAD = {
    "name": "potion",
    "category": {"name": "healing"},
    "effect_entries": [
        {"language": {"name": "en"}, "effect": "Restores 20 HP."},
        {"language": {"name": "ja"}, "effect": "20 HP回復。"},
    ],
}


def _make_client(cached=None):
    """Build a PokeAPIClient with a mocked KnowledgeBase.

    cached: dict mapping cache_key → payload dict (simulates cache hits).
    """
    kb = MagicMock()
    async def _get_cache(key):
        if cached and key in cached:
            return json.dumps(cached[key])
        return None
    kb.get_pokeapi_cache = AsyncMock(side_effect=_get_cache)
    kb.set_pokeapi_cache = AsyncMock()
    return PokeAPIClient(knowledge=kb)


def _mock_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=payload)
    return resp


# ---------------------------------------------------------------------------
# get_pokemon
# ---------------------------------------------------------------------------

class TestGetPokemon:
    async def test_happy_path(self):
        client = _make_client()
        # get_pokemon(280) converts RSE internal 280 → NDex 255 before querying
        responses = {
            "https://pokeapi.co/api/v2/pokemon/255": POKEMON_PAYLOAD,
            "https://pokeapi.co/api/v2/pokemon-species/255/": SPECIES_PAYLOAD,
            "https://pokeapi.co/api/v2/evolution-chain/176/": CHAIN_PAYLOAD,
        }
        with patch.object(client._http, "get", new=AsyncMock(
            side_effect=lambda url, **kw: _mock_response(responses[url])
        )):
            result = await client.get_pokemon(280)

        assert result["name"] == "torchic"
        assert "fire" in result["types"]
        assert result["base_stats"]["hp"] == 45
        assert result["evolution_chain"] == ["torchic", "combusken", "blaziken"]

    async def test_cache_hit_skips_http(self):
        # pokemon:280 cached; species and chain still need HTTP
        client = _make_client(cached={"pokemon:280": POKEMON_PAYLOAD})
        with patch.object(client._http, "get", new=AsyncMock()) as mock_get:
            mock_get.side_effect = [
                _mock_response(SPECIES_PAYLOAD),
                _mock_response(CHAIN_PAYLOAD),
            ]
            result = await client.get_pokemon(280)
        # HTTP called only for species and chain, not for the pokemon itself
        assert mock_get.call_count == 2
        assert result["name"] == "torchic"

    async def test_network_error_returns_none(self):
        client = _make_client()
        with patch.object(client._http, "get", new=AsyncMock(side_effect=Exception("timeout"))):
            result = await client.get_pokemon(280)
        assert result is None


# ---------------------------------------------------------------------------
# get_move
# ---------------------------------------------------------------------------

class TestGetMove:
    async def test_happy_path(self):
        client = _make_client()
        with patch.object(client._http, "get", new=AsyncMock(
            return_value=_mock_response(MOVE_PAYLOAD)
        )):
            result = await client.get_move(33)

        assert result["name"] == "tackle"
        assert result["type"] == "normal"
        assert result["power"] == 40
        assert result["accuracy"] == 100
        assert result["pp"] == 35

    async def test_cache_hit(self):
        client = _make_client(cached={"move:33": MOVE_PAYLOAD})
        with patch.object(client._http, "get", new=AsyncMock()) as mock_get:
            result = await client.get_move(33)
        mock_get.assert_not_called()
        assert result["name"] == "tackle"

    async def test_network_error_returns_none(self):
        client = _make_client()
        with patch.object(client._http, "get", new=AsyncMock(side_effect=Exception("err"))):
            result = await client.get_move(33)
        assert result is None


# ---------------------------------------------------------------------------
# get_item
# ---------------------------------------------------------------------------

class TestGetItem:
    async def test_happy_path(self):
        client = _make_client()
        with patch.object(client._http, "get", new=AsyncMock(
            return_value=_mock_response(ITEM_PAYLOAD)
        )):
            result = await client.get_item(13)

        assert result["name"] == "potion"
        assert result["category"] == "healing"
        assert "20 HP" in result["effect"]

    async def test_filters_english_effect_only(self):
        client = _make_client()
        with patch.object(client._http, "get", new=AsyncMock(
            return_value=_mock_response(ITEM_PAYLOAD)
        )):
            result = await client.get_item(13)
        assert "回復" not in result["effect"]

    async def test_network_error_returns_none(self):
        client = _make_client()
        with patch.object(client._http, "get", new=AsyncMock(side_effect=Exception("err"))):
            result = await client.get_item(13)
        assert result is None


# ---------------------------------------------------------------------------
# Evolution chain helper
# ---------------------------------------------------------------------------

class TestRseToNdex:
    def test_gen1(self):
        assert _rse_to_ndex(25) == 25   # Pikachu

    def test_gen2(self):
        assert _rse_to_ndex(251) == 251  # Celebi

    def test_gen3_torchic(self):
        assert _rse_to_ndex(280) == 255  # internal 280 → NDex 255

    def test_gen3_treecko(self):
        assert _rse_to_ndex(277) == 252  # internal 277 → NDex 252

    def test_gen3_deoxys(self):
        assert _rse_to_ndex(411) == 386  # internal 411 → NDex 386


class TestWalkChain:
    def test_single(self):
        assert _walk_chain({"species": {"name": "eevee"}, "evolves_to": []}) == ["eevee"]

    def test_linear(self):
        chain = {
            "species": {"name": "torchic"},
            "evolves_to": [{"species": {"name": "combusken"}, "evolves_to": [
                {"species": {"name": "blaziken"}, "evolves_to": []}
            ]}],
        }
        assert _walk_chain(chain) == ["torchic", "combusken", "blaziken"]

    def test_empty(self):
        assert _walk_chain({}) == []
