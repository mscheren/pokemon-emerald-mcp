"""Unit tests for MCP server helper functions."""
import io
import pytest
from pathlib import Path
from PIL import Image as PILImage

from src.agent.mcp_server import _annotate_screenshot, _GRID_OFFSET_X, _GRID_OFFSET_Y, _TILE_PX


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
            "passable", "blocked", "grass", "water",
            "ledge_south", "ledge_north", "ledge_west", "ledge_east",
            "npc", "item", "rock_smash", "rock_strength", "tree_cut", "unknown",
        ]
        # Place each tile type within the viewport at distinct positions
        tiles = [
            {"x": 10 - 7 + i, "y": 8 - 4, "tile_type": tt, "notes": None}
            for i, tt in enumerate(tile_types)
            if i < 14
        ]
        _annotate_screenshot(path, player_x=10, player_y=8, map_tiles=tiles)

    def test_in_battle_returns_plain_image(self, tmp_path):
        path = _make_png(tmp_path)
        result = _annotate_screenshot(
            path, player_x=10, player_y=8, map_tiles=[], in_battle=True
        )
        assert isinstance(result, bytes)
        img = PILImage.open(io.BytesIO(result))
        assert img.format == "PNG"
        assert img.size == (960, 640)

    def test_in_battle_matches_plain_upscale(self, tmp_path):
        """Battle mode must not draw any overlay — pixels identical to nearest-neighbour upscale."""
        path = _make_png(tmp_path)
        battle_result = _annotate_screenshot(
            path, player_x=10, player_y=8, map_tiles=[], in_battle=True
        )
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

    def test_grid_offset_applied(self, tmp_path):
        """First grid line must be at _GRID_OFFSET_X, not at x=0."""
        path = _make_png(tmp_path)
        result = _annotate_screenshot(path, player_x=10, player_y=8, map_tiles=[])
        img = PILImage.open(io.BytesIO(result))
        pixels = img.load()
        # Pick a y that is in the middle of a tile row, not on any horizontal grid line
        mid_y = _GRID_OFFSET_Y + _TILE_PX // 2  # 96 — halfway through first tile row
        # Background pixel at x=0 (no grid line before offset)
        bg_pixel = pixels[0, mid_y]
        # Grid line pixel at x=_GRID_OFFSET_X (white blend makes it brighter)
        grid_pixel = pixels[_GRID_OFFSET_X, mid_y]
        assert sum(grid_pixel[:3]) > sum(bg_pixel[:3])
