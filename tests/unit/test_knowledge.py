"""Unit tests for src/agent/knowledge.py.

Covers KnowledgeBase initialisation, schema creation (all 5 tables and their
indexes), and all CRUD operations: discoveries, user_guidance, strategies,
pokemon_knowledge, and progress.
"""

import pytest
import pytest_asyncio

from src.agent.knowledge import KnowledgeBase


@pytest_asyncio.fixture
async def kb(tmp_path):
    """Initialised KnowledgeBase backed by a temporary SQLite file."""
    db = KnowledgeBase(tmp_path / "test.db")
    await db.initialize()
    yield db
    await db.close()


class TestSchema:
    """Verify that initialize() creates all expected tables and indexes."""

    async def test_all_tables_exist(self, kb):
        """All tables defined in schema.sql must be present after initialize()."""
        conn = kb._require_conn()
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ) as cur:
            names = {row["name"] for row in await cur.fetchall()}
        expected = {
            "discoveries",
            "user_guidance",
            "strategies",
            "pokemon_knowledge",
            "progress",
            "pokeapi_cache",
            "map_tiles",
        }
        assert expected == names

    async def test_discoveries_indexes_exist(self, kb):
        """discoveries table must have indexes on category and map_id."""
        conn = kb._require_conn()
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='discoveries'") as cur:
            names = {row["name"] for row in await cur.fetchall()}
        assert "idx_discoveries_category" in names
        assert "idx_discoveries_map" in names

    async def test_user_guidance_index_exists(self, kb):
        """user_guidance table must have an index on status."""
        conn = kb._require_conn()
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='user_guidance'"
        ) as cur:
            names = {row["name"] for row in await cur.fetchall()}
        assert "idx_guidance_status" in names

    async def test_strategies_index_exists(self, kb):
        """strategies table must have an index on situation."""
        conn = kb._require_conn()
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='strategies'") as cur:
            names = {row["name"] for row in await cur.fetchall()}
        assert "idx_strategies_situation" in names

    async def test_pokemon_knowledge_index_exists(self, kb):
        """pokemon_knowledge table must have an index on species_id."""
        conn = kb._require_conn()
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='pokemon_knowledge'"
        ) as cur:
            names = {row["name"] for row in await cur.fetchall()}
        assert "idx_pokemon_species" in names

    async def test_progress_index_exists(self, kb):
        """progress table must have an index on event_type."""
        conn = kb._require_conn()
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='progress'") as cur:
            names = {row["name"] for row in await cur.fetchall()}
        assert "idx_progress_type" in names


class TestKnowledgeBaseInit:
    """Tests for KnowledgeBase lifecycle (initialize, close, error handling)."""

    async def test_initialize_creates_db_file(self, tmp_path):
        """initialize() must create the .db file, including any missing parent dirs."""
        db_path = tmp_path / "subdir" / "test.db"
        kb = KnowledgeBase(db_path)
        await kb.initialize()
        assert db_path.exists()
        await kb.close()

    async def test_initialize_idempotent(self, tmp_path):
        """Calling initialize() on an existing database must not raise."""
        db_path = tmp_path / "test.db"
        kb = KnowledgeBase(db_path)
        await kb.initialize()
        await kb.close()
        kb2 = KnowledgeBase(db_path)
        await kb2.initialize()
        await kb2.close()

    async def test_require_conn_raises_before_init(self, tmp_path):
        """_require_conn() must raise RuntimeError if initialize() was not called."""
        kb = KnowledgeBase(tmp_path / "test.db")
        with pytest.raises(RuntimeError, match="not initialized"):
            kb._require_conn()

    async def test_close_without_initialize_is_safe(self, tmp_path):
        """close() must be a no-op when called before initialize()."""
        kb = KnowledgeBase(tmp_path / "test.db")
        await kb.close()  # must not raise


class TestDiscoveries:
    """Tests for the discoveries table CRUD operations."""

    async def test_record_returns_row_id(self, kb):
        """record_discovery() must return the auto-incremented row id."""
        row_id = await kb.record_discovery("location", "Rustboro City", "First gym city")
        assert row_id == 1

    async def test_record_increments_id(self, kb):
        """Successive inserts must return strictly increasing row ids."""
        id1 = await kb.record_discovery("location", "Rustboro City", "First gym city")
        id2 = await kb.record_discovery("item", "Potion", "Restores 20 HP")
        assert id2 == id1 + 1

    async def test_record_with_coords(self, kb):
        """record_discovery() must accept optional map_id, x, and y arguments."""
        row_id = await kb.record_discovery(
            "location",
            "Rustboro City",
            "First gym city",
            map_id=42,
            x=10,
            y=15,
        )
        assert row_id is not None

    async def test_record_with_metadata(self, kb):
        """record_discovery() must accept an optional metadata dict."""
        row_id = await kb.record_discovery(
            "npc",
            "Professor Birch",
            "Starting NPC",
            metadata={"quest": "starter_choice"},
        )
        assert row_id is not None

    async def test_get_relevant_knowledge_match_title(self, kb):
        """get_relevant_knowledge() must return rows whose title matches the keyword."""
        await kb.record_discovery("location", "Rustboro City", "First gym city")
        results = await kb.get_relevant_knowledge("Rustboro")
        assert len(results) == 1
        assert results[0]["title"] == "Rustboro City"

    async def test_get_relevant_knowledge_match_description(self, kb):
        """get_relevant_knowledge() must return rows whose description matches the keyword."""
        await kb.record_discovery("mechanic", "EV Training", "Defeat Pokemon for EVs")
        results = await kb.get_relevant_knowledge("Pokemon for EVs")
        assert len(results) == 1

    async def test_get_relevant_knowledge_no_match(self, kb):
        """get_relevant_knowledge() must return an empty list when nothing matches."""
        await kb.record_discovery("location", "Rustboro City", "First gym city")
        results = await kb.get_relevant_knowledge("Slateport")
        assert results == []

    async def test_get_relevant_knowledge_respects_limit(self, kb):
        """get_relevant_knowledge() must honour the limit parameter."""
        for i in range(5):
            await kb.record_discovery("location", f"City {i}", f"Description {i}")
        results = await kb.get_relevant_knowledge("City", limit=2)
        assert len(results) == 2

    async def test_get_relevant_knowledge_default_limit(self, kb):
        """get_relevant_knowledge() must default to returning at most 3 results."""
        for i in range(5):
            await kb.record_discovery("location", f"City {i}", f"Description {i}")
        results = await kb.get_relevant_knowledge("City")
        assert len(results) == 3


class TestUserGuidance:
    """Tests for the user_guidance table CRUD operations."""

    async def test_add_and_retrieve(self, kb):
        """add_user_guidance() and get_active_guidance() must round-trip correctly."""
        gid = await kb.add_user_guidance("Catch a Ralts", priority=5)
        assert gid == 1
        active = await kb.get_active_guidance()
        assert len(active) == 1
        assert active[0]["instruction"] == "Catch a Ralts"
        assert active[0]["priority"] == 5

    async def test_add_with_context(self, kb):
        """add_user_guidance() must persist the context field."""
        await kb.add_user_guidance("Get to Rustboro", context="For Roxanne gym")
        active = await kb.get_active_guidance()
        assert active[0]["context"] == "For Roxanne gym"

    async def test_priority_ordering(self, kb):
        """get_active_guidance() must return higher-priority rows first."""
        await kb.add_user_guidance("Low priority task", priority=0)
        await kb.add_user_guidance("High priority task", priority=10)
        active = await kb.get_active_guidance()
        assert active[0]["instruction"] == "High priority task"
        assert active[1]["instruction"] == "Low priority task"

    async def test_update_status_completed(self, kb):
        """Guidance marked 'completed' must not appear in get_active_guidance()."""
        gid = await kb.add_user_guidance("Catch a Ralts")
        await kb.update_guidance_status(gid, "completed")
        assert await kb.get_active_guidance() == []

    async def test_update_status_superseded(self, kb):
        """Guidance marked 'superseded' must not appear in get_active_guidance()."""
        gid = await kb.add_user_guidance("Old task")
        await kb.update_guidance_status(gid, "superseded")
        assert await kb.get_active_guidance() == []

    async def test_only_active_returned(self, kb):
        """get_active_guidance() must exclude completed entries and include active ones."""
        await kb.add_user_guidance("Active task")
        gid2 = await kb.add_user_guidance("Completed task")
        await kb.update_guidance_status(gid2, "completed")
        active = await kb.get_active_guidance()
        assert len(active) == 1
        assert active[0]["instruction"] == "Active task"

    async def test_empty_guidance(self, kb):
        """get_active_guidance() must return an empty list when no guidance exists."""
        assert await kb.get_active_guidance() == []


class TestStrategies:
    """Tests for the strategies table CRUD and search operations."""

    async def test_record_and_search(self, kb):
        """record_strategy() and search_strategies() must round-trip correctly."""
        await kb.record_strategy("wild battle", "use type advantage", "won quickly", effectiveness=4)
        results = await kb.search_strategies("wild")
        assert len(results) == 1
        assert results[0]["situation"] == "wild battle"
        assert results[0]["effectiveness"] == 4

    async def test_search_by_approach(self, kb):
        """search_strategies() must match on the approach field as well as situation."""
        await kb.record_strategy("any battle", "use status moves", "worked well")
        results = await kb.search_strategies("status moves")
        assert len(results) == 1

    async def test_search_no_match(self, kb):
        """search_strategies() must return an empty list when nothing matches."""
        await kb.record_strategy("wild battle", "use type advantage")
        assert await kb.search_strategies("gym") == []

    async def test_search_ordered_by_effectiveness(self, kb):
        """search_strategies() must return higher-effectiveness rows first."""
        await kb.record_strategy("battle", "approach A", effectiveness=2)
        await kb.record_strategy("battle", "approach B", effectiveness=5)
        results = await kb.search_strategies("battle")
        assert results[0]["approach"] == "approach B"

    async def test_search_respects_limit(self, kb):
        """search_strategies() must honour the limit parameter."""
        for i in range(10):
            await kb.record_strategy(f"situation {i}", f"approach {i}")
        assert len(await kb.search_strategies("situation", limit=3)) == 3

    async def test_default_effectiveness_zero(self, kb):
        """record_strategy() must default effectiveness to 0 when not supplied."""
        await kb.record_strategy("wild battle", "run away")
        results = await kb.search_strategies("run away")
        assert results[0]["effectiveness"] == 0


class TestPokemonKnowledge:
    """Tests for the pokemon_knowledge table upsert and retrieval."""

    async def test_record_and_retrieve(self, kb):
        """record_pokemon() must persist all fields; get_pokemon_knowledge() must return them."""
        await kb.record_pokemon(255, "Torchic", "Fire", None, "Starter Pokemon")
        result = await kb.get_pokemon_knowledge(255)
        assert result is not None
        assert result["species_name"] == "Torchic"
        assert result["type_primary"] == "Fire"
        assert result["type_secondary"] is None
        assert result["notes"] == "Starter Pokemon"

    async def test_upsert_updates_notes(self, kb):
        """A second record_pokemon() call with new notes must overwrite the old notes."""
        await kb.record_pokemon(255, "Torchic", "Fire")
        await kb.record_pokemon(255, "Torchic", "Fire", notes="Seen again")
        result = await kb.get_pokemon_knowledge(255)
        assert result["notes"] == "Seen again"

    async def test_upsert_preserves_notes_when_none(self, kb):
        """A record_pokemon() call with notes=None must not overwrite existing notes."""
        await kb.record_pokemon(255, "Torchic", "Fire", notes="Original note")
        await kb.record_pokemon(255, "Torchic", "Fire", notes=None)
        result = await kb.get_pokemon_knowledge(255)
        assert result["notes"] == "Original note"

    async def test_dual_type(self, kb):
        """record_pokemon() must persist a secondary type."""
        await kb.record_pokemon(6, "Charizard", "Fire", "Flying")
        result = await kb.get_pokemon_knowledge(6)
        assert result["type_secondary"] == "Flying"

    async def test_get_unknown_species(self, kb):
        """get_pokemon_knowledge() must return None for a species not yet recorded."""
        assert await kb.get_pokemon_knowledge(999) is None


class TestProgress:
    """Tests for the progress table recording and summary aggregation."""

    async def test_record_badge(self, kb):
        """record_progress('badge', ...) must appear in get_progress_summary()['badges']."""
        await kb.record_progress("badge", "Stone Badge")
        summary = await kb.get_progress_summary()
        assert "Stone Badge" in summary["badges"]

    async def test_record_capture(self, kb):
        """record_progress('capture', ...) must increment the captures counter."""
        await kb.record_progress("capture", "Ralts")
        assert (await kb.get_progress_summary())["captures"] == 1

    async def test_record_milestone(self, kb):
        """record_progress('milestone', ...) must increment the milestones counter."""
        await kb.record_progress("milestone", "Reached Rustboro")
        assert (await kb.get_progress_summary())["milestones"] == 1

    async def test_record_evolution(self, kb):
        """record_progress('evolution', ...) must increment the evolutions counter."""
        await kb.record_progress("evolution", "Torchic evolved to Combusken")
        assert (await kb.get_progress_summary())["evolutions"] == 1

    async def test_full_summary(self, kb):
        """get_progress_summary() must correctly aggregate all event types."""
        await kb.record_progress("badge", "Stone Badge")
        await kb.record_progress("badge", "Knuckle Badge")
        await kb.record_progress("capture", "Ralts")
        await kb.record_progress("capture", "Zigzagoon")
        await kb.record_progress("milestone", "Reached Rustboro")
        await kb.record_progress("evolution", "Torchic evolved")
        summary = await kb.get_progress_summary()
        assert summary["badges"] == ["Stone Badge", "Knuckle Badge"]
        assert summary["captures"] == 2
        assert summary["milestones"] == 1
        assert summary["evolutions"] == 1

    async def test_empty_summary(self, kb):
        """get_progress_summary() must return zeroed counters when no events exist."""
        assert await kb.get_progress_summary() == {"badges": [], "captures": 0, "milestones": 0, "evolutions": 0}

    async def test_record_returns_row_id(self, kb):
        """record_progress() must return the auto-incremented row id."""
        assert await kb.record_progress("badge", "Stone Badge") == 1


class TestMapTiles:
    """Tests for record_tile() and get_map_tiles()."""

    async def test_record_tile_inserts(self, kb):
        """record_tile() stores a tile retrievable via get_map_tiles()."""
        await kb.record_tile(map_id=1800, x=5, y=3, tile_type="passable", notes="open path")
        tiles = await kb.get_map_tiles(1800)
        assert len(tiles) == 1
        assert tiles[0]["x"] == 5
        assert tiles[0]["y"] == 3
        assert tiles[0]["tile_type"] == "passable"
        assert tiles[0]["notes"] == "open path"

    async def test_record_tile_upserts(self, kb):
        """Re-recording the same (map_id, x, y) replaces the tile_type."""
        await kb.record_tile(map_id=1800, x=5, y=3, tile_type="unknown")
        await kb.record_tile(map_id=1800, x=5, y=3, tile_type="grass")
        tiles = await kb.get_map_tiles(1800)
        assert len(tiles) == 1
        assert tiles[0]["tile_type"] == "grass"

    async def test_get_map_tiles_empty(self, kb):
        """get_map_tiles() returns [] for a map with no recorded tiles."""
        assert await kb.get_map_tiles(9999) == []

    async def test_get_map_tiles_filters_by_map_id(self, kb):
        """Tiles from map A must not appear when querying map B."""
        await kb.record_tile(map_id=1800, x=1, y=1, tile_type="passable")
        await kb.record_tile(map_id=900, x=2, y=2, tile_type="blocked")
        tiles_1800 = await kb.get_map_tiles(1800)
        tiles_900 = await kb.get_map_tiles(900)
        assert all(t["x"] == 1 and t["y"] == 1 for t in tiles_1800)
        assert all(t["x"] == 2 and t["y"] == 2 for t in tiles_900)
