"""End-to-end validation: live mGBA memory → species ID → PokeAPI enrichment.

Usage (with mGBA running and a save loaded):
    uv run python scripts/validate_pokeapi.py

Expected output (Torchic example):
    In-game species ID: 280
    Pokemon: torchic  |  Types: fire  |  Evo: torchic → combusken → blaziken
    hp:45  attack:60  defense:40  sp-atk:70  sp-def:50  speed:45
    Move 1 (ID 33): tackle  |  normal  |  Pow:40  Acc:100  PP:35
    Move 2 (ID 46): growl   |  normal  |  Pow:—   Acc:100  PP:40
    [Cache] pokemon:280 already stored — no HTTP request
"""
import asyncio
import sys
from pathlib import Path

# Allow running from project root or scripts/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.knowledge import KnowledgeBase
from src.agent.mgba_client import MGBAClient
from src.agent.models import GameState
from src.agent.pokeapi import PokeAPIClient

KB_PATH = Path("data/knowledge.db")


async def main() -> None:
    kb = KnowledgeBase(KB_PATH)
    await kb.initialize()
    api = PokeAPIClient(kb)
    client = MGBAClient()

    try:
        await client.connect(retries=3, delay=1.0)
    except ConnectionError as e:
        print(f"[ERROR] Cannot connect to mGBA: {e}")
        print("        Start mGBA with the Lua script running first.")
        return

    try:
        state_data = await client.request_state()
        game_state = GameState.from_dict(state_data)

        if not game_state.party:
            print("[ERROR] No Pokemon in party — load a save with at least one party member.")
            return

        lead = game_state.party[0]
        species_id = lead.species_id
        move_ids = [m for m in lead.move_ids if m]

        print(f"\nIn-game species ID: {species_id}")
        print(f"Nickname: {lead.nickname}  |  Level: {lead.level}  |  HP: {lead.current_hp}/{lead.max_hp}")

        if not species_id:
            print("[WARN] species_id is None — decryption may have failed.")
        else:
            poke = await api.get_pokemon(species_id)
            if poke:
                evo = " → ".join(poke["evolution_chain"]) or "—"
                types = ", ".join(poke["types"])
                print(f"Pokemon: {poke['name']}  |  Types: {types}  |  Evo: {evo}")
                stats = poke["base_stats"]
                stat_str = "  ".join(f"{k}:{v}" for k, v in stats.items())
                print(f"Base stats: {stat_str}")
            else:
                print("[WARN] PokeAPI lookup failed for species", species_id)

        for i, move_id in enumerate(move_ids, 1):
            move = await api.get_move(move_id)
            if move:
                power = move["power"] if move["power"] is not None else "—"
                print(f"Move {i} (ID {move_id}): {move['name']}  |  {move['type']}  |  Pow:{power}  Acc:{move['accuracy']}  PP:{move['pp']}")

        # Second call — should be cache hit (no HTTP)
        print(f"\n[Cache] Checking pokemon:{species_id} cache...")
        if species_id:
            cached = await kb.get_pokeapi_cache(f"pokemon:{species_id}")
            print(f"[Cache] pokemon:{species_id} {'already stored — no HTTP request' if cached else 'NOT in cache — check DB'}")

    finally:
        await client.disconnect()
        await api.close()
        await kb.close()


if __name__ == "__main__":
    asyncio.run(main())
