"""Unit tests for MCP server helper functions."""

import io
from pathlib import Path

import pytest
from PIL import Image as PILImage

from src.agent.knowledge import KnowledgeBase
from src.agent.mcp_server import _GRID_OFFSET_Y, _TILE_PX, _annotate_screenshot, _auto_record_passable
from src.agent.models import GameState


def _make_png(tmp_path: Path, width: int = 240, height: int = 160) -> Path:
    """Write a solid-colour GBA-sized PNG and return its path."""
    img = PILImage.new("RGB", (width, height), color=(100, 150, 80))
    path = tmp_path / "frame_test.png"
    img.save(path, format="PNG")
    return path


class TestAnnotateScreenshot:
    def test_returns_valid_png_bytes(self, tmp_path):
        path = _make_png(tmp_path)
        result = _annotate_screenshot(path, player_x=10, player_y=8, map_tiles=[])
        assert isinstance(result, bytes)
        # PIL must be able to open it as a PNG
        img = PILImage.open(io.BytesIO(result))
        assert img.format == "PNG"

    def test_output_is_4x_scaled(self, tmp_path):
        path = _make_png(tmp_path)
        result = _annotate_screenshot(path, player_x=10, player_y=8, map_tiles=[])
        img = PILImage.open(io.BytesIO(result))
        assert img.size == (960, 640)

    def test_empty_tiles_no_exception(self, tmp_path):
        path = _make_png(tmp_path)
        # Must not raise even with no recorded tiles
        _annotate_screenshot(path, player_x=0, player_y=0, map_tiles=[])

    def test_out_of_viewport_tiles_ignored(self, tmp_path):
        path = _make_png(tmp_path)
        # Tile far outside the viewport should not cause an error
        tiles = [{"x": 999, "y": 999, "tile_type": "passable", "notes": None}]
        _annotate_screenshot(path, player_x=10, player_y=8, map_tiles=tiles)

    def test_all_tile_types_accepted(self, tmp_path):
        path = _make_png(tmp_path)
        tile_types = [
            "passable",
            "blocked",
            "grass",
            "water",
            "ledge_south",
            "ledge_north",
            "ledge_west",
            "ledge_east",
            "npc",
            "item",
            "rock_smash",
            "rock_strength",
            "tree_cut",
            "unknown",
        ]
        # Place each tile type within the viewport at distinct positions
        tiles = [
            {"x": 10 - 7 + i, "y": 8 - 4, "tile_type": tt, "notes": None} for i, tt in enumerate(tile_types) if i < 14
        ]
        _annotate_screenshot(path, player_x=10, player_y=8, map_tiles=tiles)

    def test_in_battle_returns_plain_image(self, tmp_path):
        path = _make_png(tmp_path)
        result = _annotate_screenshot(path, player_x=10, player_y=8, map_tiles=[], in_battle=True)
        assert isinstance(result, bytes)
        img = PILImage.open(io.BytesIO(result))
        assert img.format == "PNG"
        assert img.size == (960, 640)

    def test_in_battle_matches_plain_upscale(self, tmp_path):
        """Battle mode must not draw any overlay — pixels identical to nearest-neighbour upscale."""
        path = _make_png(tmp_path)
        battle_result = _annotate_screenshot(path, player_x=10, player_y=8, map_tiles=[], in_battle=True)
        # Generate plain upscale for comparison
        with PILImage.open(path) as src:
            plain = src.resize((960, 640), PILImage.NEAREST)
        plain_buf = io.BytesIO()
        plain.save(plain_buf, format="PNG")
        assert battle_result == plain_buf.getvalue()

    def test_tile_badge_rendered(self, tmp_path):
        """A tile at the player's position produces a visible difference vs. no-tile image."""
        path = _make_png(tmp_path)
        tiles = [{"x": 10, "y": 8, "tile_type": "blocked", "notes": None}]
        result_tile = _annotate_screenshot(path, player_x=10, player_y=8, map_tiles=tiles)
        result_none = _annotate_screenshot(path, player_x=10, player_y=8, map_tiles=[])
        img_tile = PILImage.open(io.BytesIO(result_tile))
        img_none = PILImage.open(io.BytesIO(result_none))
        from PIL import ImageChops

        diff = ImageChops.difference(img_tile, img_none)
        assert diff.getbbox() is not None  # images differ somewhere

    def test_grid_y_offset_applied(self, tmp_path):
        """First horizontal grid line must be at y=_GRID_OFFSET_Y, not y=0."""
        path = _make_png(tmp_path)
        result = _annotate_screenshot(path, player_x=10, player_y=8, map_tiles=[])
        img = PILImage.open(io.BytesIO(result))
        pixels = img.load()
        # _GRID_OFFSET_X=0, so vertical lines are at x=0,64,128…
        # Pick x=32 (midpoint of first tile column) — not on any vertical grid line
        mid_x = _TILE_PX // 2  # 32
        # y=1 is before the first horizontal grid line (_GRID_OFFSET_Y=32)
        bg_pixel = pixels[mid_x, 1]
        # y=_GRID_OFFSET_Y is exactly on the first horizontal grid line → brighter
        grid_pixel = pixels[mid_x, _GRID_OFFSET_Y]
        assert sum(grid_pixel[:3]) > sum(bg_pixel[:3])


@pytest.fixture
async def kb(tmp_path):
    """Temporary KnowledgeBase backed by a real SQLite file."""
    db = KnowledgeBase(tmp_path / "test.db")
    await db.initialize()
    yield db
    await db.close()


def _gs(map_id=100, player_x=5, player_y=8, in_battle=False) -> GameState:
    return GameState(map_id=map_id, player_x=player_x, player_y=player_y, in_battle=in_battle)


class TestAutoRecordPassable:
    @pytest.mark.asyncio
    async def test_records_tile_when_unknown(self, kb):
        tiles = await _auto_record_passable(kb, _gs(), [])
        assert any(t["x"] == 5 and t["y"] == 8 and t["tile_type"] == "passable" for t in tiles)

    @pytest.mark.asyncio
    async def test_returns_updated_tile_list(self, kb):
        result = await _auto_record_passable(kb, _gs(), [])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_skips_when_in_battle(self, kb):
        tiles = await _auto_record_passable(kb, _gs(in_battle=True), [])
        assert tiles == []

    @pytest.mark.asyncio
    async def test_skips_when_map_id_zero(self, kb):
        tiles = await _auto_record_passable(kb, _gs(map_id=0), [])
        assert tiles == []

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_tile(self, kb):
        existing = [{"x": 5, "y": 8, "tile_type": "blocked", "notes": None}]
        await kb.record_tile(map_id=100, x=5, y=8, tile_type="blocked")
        tiles = await _auto_record_passable(kb, _gs(), existing)
        # tile_type must remain "blocked"
        match = next(t for t in tiles if t["x"] == 5 and t["y"] == 8)
        assert match["tile_type"] == "blocked"

    @pytest.mark.asyncio
    async def test_persists_to_db(self, kb):
        await _auto_record_passable(kb, _gs(), [])
        stored = await kb.get_map_tiles(100)
        assert any(t["x"] == 5 and t["y"] == 8 for t in stored)
