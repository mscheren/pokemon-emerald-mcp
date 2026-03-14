"""PokeAPI HTTP client with SQLite caching via KnowledgeBase.

All results are cached in the discoveries table under category="pokeapi"
so each resource is fetched at most once per session (and across sessions).
Network errors are swallowed and logged — they never crash the agent loop.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .knowledge import KnowledgeBase

BASE_URL = "https://pokeapi.co/api/v2"
logger = logging.getLogger(__name__)


class PokeAPIClient:
    """Async PokeAPI client with transparent SQLite caching."""

    def __init__(self, knowledge: "KnowledgeBase") -> None:
        self._kb = knowledge
        self._http = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def _cached_get(self, url: str, cache_key: str) -> dict | None:
        """Fetch a URL with cache-aside using the dedicated PokeAPI cache table.

        Returns None on network or HTTP errors without raising.
        """
        cached = await self._kb.get_pokeapi_cache(cache_key)
        if cached is not None:
            return json.loads(cached)

        try:
            r = await self._http.get(url)
            r.raise_for_status()
            data: dict[str, Any] = r.json()
        except Exception as e:
            logger.warning("PokeAPI fetch failed for %s: %s", url, e)
            return None

        try:
            await self._kb.set_pokeapi_cache(cache_key, json.dumps(data))
        except Exception as e:
            logger.warning("Failed to cache PokeAPI response: %s", e)

        return data

    async def get_pokemon(self, species_id: int) -> dict | None:
        """Fetch Pokemon data: name, types, base stats, and evolution chain.

        Args:
            species_id: RSE internal species ID. Gen 3 Pokemon use internal IDs
                that are National Dex + 25 (e.g. Torchic = 280 internal = 255 NDex).
                This method converts to National Dex before querying PokeAPI.

        Returns:
            Dict with keys ``name``, ``types``, ``base_stats``, ``evolution_chain``,
            or None on failure.
        """
        ndex = _rse_to_ndex(species_id)
        pokemon_data = await self._cached_get(
            f"{BASE_URL}/pokemon/{ndex}",
            f"pokemon:{species_id}",
        )
        if not pokemon_data:
            return None

        name = pokemon_data.get("name", "unknown")
        types = [t["type"]["name"] for t in pokemon_data.get("types", [])]
        base_stats = {s["stat"]["name"]: s["base_stat"] for s in pokemon_data.get("stats", [])}

        # Fetch species for evolution chain URL
        evolution_chain: list[str] = []
        species_url = (pokemon_data.get("species") or {}).get("url")
        if species_url:
            species_data = await self._cached_get(species_url, f"pokemon-species:{ndex}")
            if species_data:
                chain_url = (species_data.get("evolution_chain") or {}).get("url")
                if chain_url:
                    chain_id = chain_url.rstrip("/").split("/")[-1]
                    chain_data = await self._cached_get(chain_url, f"evolution-chain:{chain_id}")
                    if chain_data:
                        evolution_chain = _walk_chain(chain_data.get("chain", {}))

        return {
            "name": name,
            "types": types,
            "base_stats": base_stats,
            "evolution_chain": evolution_chain,
        }

    async def get_move(self, move_id: int) -> dict | None:
        """Fetch move data: name, type, power, accuracy, PP.

        Args:
            move_id: Move ID as returned by mGBA memory decryption.

        Returns:
            Dict with keys ``name``, ``type``, ``power``, ``accuracy``, ``pp``,
            or None on failure.
        """
        data = await self._cached_get(f"{BASE_URL}/move/{move_id}", f"move:{move_id}")
        if not data:
            return None
        return {
            "name": data.get("name", "unknown"),
            "type": (data.get("type") or {}).get("name", "unknown"),
            "power": data.get("power"),
            "accuracy": data.get("accuracy"),
            "pp": data.get("pp"),
        }

    async def get_item(self, item_id: int) -> dict | None:
        """Fetch item data: name, category, and English effect description.

        Args:
            item_id: Item ID as returned by the bag reader.

        Returns:
            Dict with keys ``name``, ``category``, ``effect``, or None on failure.
        """
        data = await self._cached_get(f"{BASE_URL}/item/{item_id}", f"item:{item_id}")
        if not data:
            return None
        effect = ""
        for entry in data.get("effect_entries", []):
            if (entry.get("language") or {}).get("name") == "en":
                effect = entry.get("effect", "")
                break
        return {
            "name": data.get("name", "unknown"),
            "category": (data.get("category") or {}).get("name", "unknown"),
            "effect": effect,
        }


def _rse_to_ndex(species_id: int) -> int:
    """Convert an RSE internal species ID to the National Dex number.

    Gen 1-2 Pokemon share the same ID in both systems (1–251).
    Gen 3 Pokemon have internal IDs = National Dex + 25 (277–411).
    """
    if species_id >= 277:
        return species_id - 25
    return species_id


def _walk_chain(node: dict) -> list[str]:
    """Recursively flatten an evolution chain node into an ordered species name list."""
    if not node:
        return []
    name = (node.get("species") or {}).get("name", "")
    result = [name] if name else []
    for evolution in node.get("evolves_to", []):
        result.extend(_walk_chain(evolution))
    return result
