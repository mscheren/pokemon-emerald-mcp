"""MCP server — thin wrapper over the MGBAClient / GameController stack.

Run with:
    uv run pokemon-mcp

Environment variables:
    MGBA_HOST      — mGBA Lua socket host (default: 127.0.0.1)
    MGBA_PORT      — mGBA Lua socket port (default: 5000)
    SCREENSHOT_DIR — screenshot output directory (default: data/screenshots)
    KB_PATH        — SQLite knowledge-base path (default: data/knowledge/pokemon_knowledge.db)
"""

import asyncio
import io
import logging
import os
import re
import signal
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from .controller import GameController
from .formatter import _TILE_CHARS, ObservationFormatter
from .knowledge import KnowledgeBase
from .mgba_client import MGBAClient
from .models import GameState, Observation, SequenceStep
from .pokeapi import PokeAPIClient

logger = logging.getLogger(__name__)

_client: MGBAClient | None = None
_kb: KnowledgeBase | None = None
_controller: GameController | None = None
_pokeapi: PokeAPIClient | None = None
_screenshot_dir: Path = Path("data/screenshots")

OBSERVATION_FILE = Path("data/current_observation.txt")
_formatter = ObservationFormatter()


def _get_client() -> MGBAClient:
    if _client is None:
        raise RuntimeError("MCP server not initialised")
    return _client


def _get_kb() -> KnowledgeBase:
    if _kb is None:
        raise RuntimeError("MCP server not initialised")
    return _kb


def _get_controller() -> GameController:
    if _controller is None:
        raise RuntimeError("MCP server not initialised")
    return _controller


def _get_pokeapi() -> PokeAPIClient:
    if _pokeapi is None:
        raise RuntimeError("MCP server not initialised")
    return _pokeapi


# GBA viewport geometry (at 4× scale: 960×640, 64 px/tile)
_TILE_PX = 64  # pixels per tile after 4× upscale
_CAM_COL = 7  # player tile column in the 15-wide viewport (0-indexed)
_CAM_ROW = 4  # player tile row in the 10-tall viewport (0-indexed)
# pokeemerald camera formula: HOFS = player_x*16 - DISPLAY_WIDTH/2 (120)
#                              VOFS = player_y*16 - DISPLAY_HEIGHT/2 (80)
# → first metatile boundary on screen at x=120−7×16=8 native (32 at 4×)
#                                        y= 80−4×16=16 native (64 at 4×)
_GRID_OFFSET_X = 0  # no horizontal offset — x=0,64,128… already aligns with tile boundaries
_GRID_OFFSET_Y = 32  # 8 native px → 32 at 4× (VOFS formula has 8px sub-tile offset)

# Per-tile-type label colors (RGB) for the screenshot overlay
_TILE_LABEL_COLORS: dict[str, tuple[int, int, int]] = {
    "passable": (0, 220, 0),
    "blocked": (220, 50, 50),
    "grass": (200, 220, 0),
    "water": (0, 150, 255),
    "ledge_south": (255, 165, 0),
    "ledge_north": (255, 165, 0),
    "ledge_west": (255, 165, 0),
    "ledge_east": (255, 165, 0),
    "npc": (0, 220, 220),
    "item": (220, 0, 220),
    "rock_smash": (160, 110, 60),
    "rock_strength": (160, 110, 60),
    "tree_cut": (60, 150, 60),
    "unknown": (180, 180, 180),
}


_BADGE_SIZE = 32  # px — coloured tile badge square (at 4× scale)


def _annotate_screenshot(
    path: Path,
    player_x: int,
    player_y: int,
    map_tiles: list[dict],
    scale: int = 4,
    in_battle: bool = False,
) -> bytes:
    """Scale screenshot 4× and overlay tile grid, player marker, and known tile badges.

    When ``in_battle`` is True the function returns the plain upscaled image with no
    overlay (grid lines and tile markers are meaningless during battle).

    Otherwise draws:
    - Semi-transparent grid lines at metatile boundaries (offset by camera formula)
    - Yellow border around the player's tile
    - 32×32 coloured badge in the top-left of each recorded tile, with centered label

    Args:
        path: Path to the raw GBA screenshot PNG (240×160).
        player_x: Player X tile coordinate in game space.
        player_y: Player Y tile coordinate in game space.
        map_tiles: Recorded tiles for the current map (from KnowledgeBase).
        scale: Upscale factor (default 4 → 960×640).
        in_battle: When True, skip all overlay and return plain upscaled image.

    Returns:
        PNG bytes of the annotated (or plain) image.
    """
    with PILImage.open(path) as src:
        w, h = src.size
        img = src.resize((w * scale, h * scale), PILImage.NEAREST)

    if in_battle:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    img = img.convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")
    W, H = img.size  # 960 × 640

    # Grid lines — faint white, semi-transparent, starting at camera offset
    grid_color = (255, 255, 255, 60)
    x = _GRID_OFFSET_X
    while x <= W:
        draw.line([(x, 0), (x, H - 1)], fill=grid_color, width=1)
        x += _TILE_PX
    y = _GRID_OFFSET_Y
    while y <= H:
        draw.line([(0, y), (W - 1, y)], fill=grid_color, width=1)
        y += _TILE_PX

    # Player tile — yellow border
    px0 = _GRID_OFFSET_X + _CAM_COL * _TILE_PX
    py0 = _GRID_OFFSET_Y + _CAM_ROW * _TILE_PX
    draw.rectangle(
        [px0, py0, px0 + _TILE_PX - 1, py0 + _TILE_PX - 1],
        outline=(255, 220, 0, 230),
        width=3,
    )

    # Known tile badges — 32×32 coloured square with centered label
    try:
        font = ImageFont.load_default(size=20)
    except TypeError:
        font = ImageFont.load_default()

    for tile in map_tiles:
        vx = tile["x"] - (player_x - _CAM_COL)
        vy = tile["y"] - (player_y - _CAM_ROW)
        if not (0 <= vx < 15 and 0 <= vy < 10):
            continue
        char = _TILE_CHARS.get(tile["tile_type"], "?")
        rgb = _TILE_LABEL_COLORS.get(tile["tile_type"], (180, 180, 180))
        tx = _GRID_OFFSET_X + vx * _TILE_PX
        ty = _GRID_OFFSET_Y + vy * _TILE_PX

        # Coloured backing
        draw.rectangle(
            [tx, ty, tx + _BADGE_SIZE - 1, ty + _BADGE_SIZE - 1],
            fill=(*rgb, 200),
        )
        # Dark inner rectangle for text contrast
        draw.rectangle(
            [tx + 2, ty + 2, tx + _BADGE_SIZE - 3, ty + _BADGE_SIZE - 3],
            fill=(0, 0, 0, 120),
        )
        # Centered label
        try:
            bbox = draw.textbbox((0, 0), char, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            text_x = tx + (_BADGE_SIZE - tw) // 2 - bbox[0]
            text_y = ty + (_BADGE_SIZE - th) // 2 - bbox[1]
        except AttributeError:
            text_x = tx + (_BADGE_SIZE - 12) // 2
            text_y = ty + (_BADGE_SIZE - 14) // 2
        draw.text((text_x, text_y), char, fill=(255, 255, 255, 255), font=font)

    img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(server):
    global _client, _kb, _controller, _screenshot_dir, _pokeapi

    host = os.environ.get("MGBA_HOST", "127.0.0.1")
    port = int(os.environ.get("MGBA_PORT", "5000"))
    _screenshot_dir = Path(os.environ.get("SCREENSHOT_DIR", "data/screenshots"))
    _screenshot_dir.mkdir(parents=True, exist_ok=True)
    OBSERVATION_FILE.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Connecting to mGBA at %s:%d", host, port)
    _client = MGBAClient(host=host, port=port)
    await _client.connect(retries=5, delay=2.0)

    kb_path = Path(os.environ.get("KB_PATH", "data/knowledge/pokemon_knowledge.db"))
    kb_path.parent.mkdir(parents=True, exist_ok=True)
    _kb = KnowledgeBase(kb_path)
    await _kb.initialize()

    _controller = GameController(_client, _screenshot_dir)
    _pokeapi = PokeAPIClient(_kb)

    logger.info("MCP server ready")
    yield

    if _pokeapi:
        await _pokeapi.close()
    if _client:
        await _client.disconnect()
    if _kb:
        await _kb.close()


mcp = FastMCP("pokemon-agent", lifespan=_lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _auto_record_passable(
    kb: "KnowledgeBase",
    game_state: "GameState",
    map_tiles: list[dict],
) -> list[dict]:
    """Record the player's current tile as passable when it is not yet known.

    Skipped when in battle or when map_id is 0 (unknown map).
    Never overwrites an existing annotation.

    Returns the (possibly refreshed) map_tiles list.
    """
    if not game_state.map_id or game_state.in_battle:
        return map_tiles
    known = {(t["x"], t["y"]) for t in map_tiles}
    if (game_state.player_x, game_state.player_y) not in known:
        await kb.record_tile(
            map_id=game_state.map_id,
            x=game_state.player_x,
            y=game_state.player_y,
            tile_type="passable",
        )
        map_tiles = await kb.get_map_tiles(game_state.map_id)
    return map_tiles


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def observe() -> list:
    """Return the current game state plus a screenshot as a unified multimodal observation.

    Primary perception tool — returns structured text state and the current on-screen
    image in one call so every decision is grounded in what is actually on screen.

    Returns:
        A list with one or two content blocks:
        - ``{"type": "image", ...}`` — annotated PNG screenshot (omitted on capture failure)
        - ``{"type": "text",  ...}`` — formatted game state
    """
    state_data = await _get_client().request_state()
    game_state = GameState.from_dict(state_data)

    guidance: list[dict] = []
    knowledge: list[dict] = []
    map_tiles: list[dict] = []
    try:
        guidance = await _get_kb().get_active_guidance()
        map_key = str(game_state.map_id) if game_state.map_id else "general"
        knowledge = await _get_kb().get_relevant_knowledge(map_key, limit=3)
        map_tiles = await _get_kb().get_map_tiles(game_state.map_id)
    except Exception:
        pass
    try:
        map_tiles = await _auto_record_passable(_get_kb(), game_state, map_tiles)
    except Exception:
        logger.warning("auto_record_passable failed", exc_info=True)

    # Capture screenshot via Lua emu:screenshot()
    # GameController.request_screenshot() writes frame_XXXXXXXX.png and returns
    # its Path, raising RuntimeError on Lua-side failure.
    screenshot_bytes: bytes | None = None
    screenshot_path: Path | None = None
    try:
        screenshot_path = await _get_controller().request_screenshot(game_state.frame_number)
        screenshot_bytes = _annotate_screenshot(
            screenshot_path,
            game_state.player_x,
            game_state.player_y,
            map_tiles,
            in_battle=game_state.in_battle,
        )
    except Exception as exc:
        logger.warning("Screenshot capture failed: %s", exc)

    observation = Observation(
        game_state=game_state,
        frame_number=game_state.frame_number,
        screenshot_path=screenshot_path,
    )
    obs_text = _formatter.format(observation, guidance, knowledge)
    OBSERVATION_FILE.write_text(obs_text)

    content: list = []
    if screenshot_bytes:
        content.append(Image(data=screenshot_bytes, format="png"))
    content.append(obs_text)
    return content


@mcp.tool()
async def get_extended_state() -> dict:
    """Return state including bag items and PC box occupancy.

    Prefer when deliberating on what to do or during long-term planning.
    """
    return await _get_client().request_extended_state()


@mcp.tool()
async def press_button(button: str, duration_frames: int = 8) -> dict:
    """Press a single GBA button for the given number of frames.

    Args:
        button: One of A, B, UP, DOWN, LEFT, RIGHT, START, SELECT, L, R.
        duration_frames: How many emulated frames to hold the button.
    """
    return await _get_client().press_button(button, duration_frames)


@mcp.tool()
async def press_buttons(buttons: list[str], duration_frames: int = 8) -> dict:
    """Press multiple GBA buttons simultaneously for the given number of frames.

    Args:
        buttons: List of button names to hold at the same time.
        duration_frames: How many emulated frames to hold the buttons.
    """
    return await _get_client().press_buttons(buttons, duration_frames)


@mcp.tool()
async def wait(frames: int = 60) -> dict:
    """Pause input for the given number of emulated frames (~60 fps).

    Args:
        frames: Number of frames to idle (60 ≈ 1 second).
    """
    return await _get_client().wait_frames(frames)


@mcp.tool()
async def execute_sequence(steps: list[dict]) -> list:
    """Run a multi-step input sequence.

    Each step dict must have an ``action`` field (``press_button``,
    ``press_buttons``, or ``wait``) plus the relevant parameters.

    Args:
        steps: Ordered list of step dicts, e.g.
            [{"action": "press_button", "button": "UP", "duration_frames": 16},
             {"action": "wait", "wait_frames": 20}]
    """
    parsed: list[SequenceStep] = []
    for raw in steps:
        step = SequenceStep.from_dict(raw)
        step.validate()
        parsed.append(step)
    return await _get_client().execute_sequence(parsed)


@mcp.tool()
async def query_knowledge(query: str, limit: int = 5) -> list:
    """Search the knowledge base for relevant entries by keyword.

    Args:
        query: Keyword to search for in discovery titles and descriptions.
        limit: Maximum number of results to return (default 5).
    """
    return await _get_kb().get_relevant_knowledge(query, limit=limit)


@mcp.tool()
async def search_strategies(keyword: str, limit: int = 5) -> list:
    """Search recorded strategies by keyword.

    Args:
        keyword: Substring to match against situation or approach fields.
        limit: Maximum number of results to return (default 5).

    Returns:
        List of dicts with ``situation``, ``approach``, ``outcome``,
        ``effectiveness``, ordered by effectiveness then recency.
    """
    return await _get_kb().search_strategies(keyword, limit=limit)


@mcp.tool()
async def get_pokemon_info(species_id: int) -> dict | None:
    """Retrieve stored knowledge for a Pokemon species.

    Args:
        species_id: National Pokédex number.

    Returns:
        Dict with ``species_id``, ``species_name``, ``type_primary``,
        ``type_secondary``, ``notes``, ``first_encountered``, ``last_seen``,
        or ``None`` if the species has never been recorded.
    """
    return await _get_kb().get_pokemon_knowledge(species_id)


@mcp.tool()
async def get_active_guidance() -> list:
    """Return all active guidance instructions.

    Guidance is also injected automatically into every ``observe()`` response,
    but this tool lets you query it explicitly — for example to retrieve the
    ``id`` needed to call ``update_guidance_status``.

    Returns:
        List of dicts with ``id``, ``instruction``, ``context``, ``priority``,
        ``timestamp``, ordered by priority then recency.
    """
    return await _get_kb().get_active_guidance()


@mcp.tool()
async def get_progress_summary() -> dict:
    """Return a summary of all recorded game progress.

    Returns:
        Dict with keys:
        - ``badges`` — list of badge names in chronological order
        - ``captures`` — total number of Pokemon captured
        - ``milestones`` — total number of milestone events
        - ``evolutions`` — total number of evolutions recorded
    """
    return await _get_kb().get_progress_summary()


@mcp.tool()
async def record_tile(
    map_id: int,
    x: int,
    y: int,
    tile_type: str,
    notes: str | None = None,
) -> dict:
    """Record what is known about a map tile.

    Call this after every movement attempt to build a persistent spatial map.
    Re-recording the same (map_id, x, y) with a different tile_type overwrites
    the previous value.

    Args:
        map_id: Map identifier (from the current game state).
        x: Tile X coordinate.
        y: Tile Y coordinate.
        tile_type: One of ``passable``, ``blocked``, ``ledge_south``,
            ``ledge_north``, ``ledge_west``, ``ledge_east``, ``grass``,
            ``water``, ``npc``, ``item``, ``rock_smash``, ``rock_strength``,
            ``tree_cut``, or ``unknown``.
        notes: Optional annotation, e.g. NPC name or item name.

    Returns:
        Dict confirming the recorded tile.
    """
    await _get_kb().record_tile(map_id=map_id, x=x, y=y, tile_type=tile_type, notes=notes)
    return {"map_id": map_id, "x": x, "y": y, "tile_type": tile_type}


@mcp.tool()
async def get_map_tiles(map_id: int) -> list:
    """Return all recorded tiles for a map.

    Use this when entering a map to recall previously explored terrain before
    deciding a route. The current observation already shows nearby tiles
    in the MAP TILES section; call this tool when you need the full picture.

    Args:
        map_id: Map identifier to query (from the current game state).

    Returns:
        List of dicts with ``x``, ``y``, ``tile_type``, ``notes``.
    """
    return await _get_kb().get_map_tiles(map_id)


@mcp.tool()
async def lookup_pokemon(species_id: int) -> dict | None:
    """Look up a Pokemon species on PokeAPI (cached).

    Fetches types, base stats, and evolution chain. Results are cached
    in the local knowledge base so repeated calls are instant.

    Args:
        species_id: National Pokédex number (e.g. 255 for Torchic).

    Returns:
        Dict with ``name``, ``types`` (list), ``base_stats`` (dict),
        ``evolution_chain`` (list of names), or ``None`` on network failure.
    """
    return await _get_pokeapi().get_pokemon(species_id)


@mcp.tool()
async def lookup_move(move_id: int) -> dict | None:
    """Look up a move on PokeAPI (cached).

    Args:
        move_id: Move ID as stored in the game's memory (e.g. 10 for Scratch).

    Returns:
        Dict with ``name``, ``type``, ``power``, ``accuracy``, ``pp``,
        or ``None`` on network failure.
    """
    return await _get_pokeapi().get_move(move_id)


@mcp.tool()
async def lookup_item(item_id: int) -> dict | None:
    """Look up an item on PokeAPI (cached).

    Args:
        item_id: Item ID as stored in the game's memory.

    Returns:
        Dict with ``name``, ``category``, ``effect``,
        or ``None`` on network failure.
    """
    return await _get_pokeapi().get_item(item_id)


@mcp.tool()
async def record_discovery(
    category: str,
    title: str,
    description: str,
    map_id: int | None = None,
    x: int | None = None,
    y: int | None = None,
    metadata: dict | None = None,
) -> dict:
    """Record a game discovery in the knowledge base.

    Use this whenever the agent learns something worth remembering across
    sessions: a hidden item location, an NPC's purpose, a type weakness,
    a map layout detail, etc.

    Args:
        category: One of ``location``, ``item``, ``npc``, ``mechanic``,
            ``strategy``, or ``pokemon``.
        title: Short label (e.g. ``"Hidden Potion in Petalburg Woods"``).
        description: Full description of the discovery.
        map_id: Map ID where the discovery was made, if applicable.
        x: Tile X coordinate, if applicable.
        y: Tile Y coordinate, if applicable.
        metadata: Optional dict of extra structured data.

    Returns:
        Dict with ``id`` of the newly inserted row.
    """
    row_id = await _get_kb().record_discovery(
        category=category,
        title=title,
        description=description,
        map_id=map_id,
        x=x,
        y=y,
        metadata=metadata,
    )
    return {"id": row_id}


@mcp.tool()
async def record_progress(
    event_type: str,
    event_name: str,
    details: str = "",
) -> dict:
    """Record a game progress milestone.

    Call this when a significant event occurs: earning a badge, catching a
    Pokemon, an evolution, or any other milestone.

    Args:
        event_type: One of ``badge``, ``capture``, ``milestone``, or
            ``evolution``.
        event_name: Name of the event (e.g. ``"Stone Badge"``, ``"Ralts"``).
        details: Optional extra context about the event.

    Returns:
        Dict with ``id`` of the newly inserted row.
    """
    row_id = await _get_kb().record_progress(
        event_type=event_type,
        event_name=event_name,
        details=details,
    )
    return {"id": row_id}


@mcp.tool()
async def record_strategy(
    situation: str,
    approach: str,
    outcome: str = "",
    effectiveness: int = 0,
) -> dict:
    """Record a battle or exploration strategy.

    Use this to persist what worked (or didn't) so the agent can recall
    effective approaches in similar situations later.

    Args:
        situation: Description of the game situation (e.g. ``"wild Geodude battle"``).
        approach: Strategy applied (e.g. ``"used Torchic Ember for 2x damage"``).
        outcome: What happened as a result.
        effectiveness: Rating 0–5 (0 = unknown, 5 = very effective).

    Returns:
        Dict with ``id`` of the newly inserted row.
    """
    row_id = await _get_kb().record_strategy(
        situation=situation,
        approach=approach,
        outcome=outcome,
        effectiveness=effectiveness,
    )
    return {"id": row_id}


@mcp.tool()
async def record_pokemon(
    species_id: int,
    species_name: str,
    type_primary: str | None = None,
    type_secondary: str | None = None,
    notes: str | None = None,
) -> dict:
    """Store or update knowledge about a Pokemon species.

    Call this when the agent encounters a new species or learns something
    new about one (type, moveset, habitat, evolution, etc.).

    Args:
        species_id: National Pokédex number.
        species_name: Species name (e.g. ``"Mudkip"``).
        type_primary: Primary elemental type (e.g. ``"Water"``).
        type_secondary: Secondary type, or ``None`` if single-type.
        notes: Free-text notes to attach or replace.

    Returns:
        Dict with ``species_id`` confirming the upsert.
    """
    await _get_kb().record_pokemon(
        species_id=species_id,
        species_name=species_name,
        type_primary=type_primary,
        type_secondary=type_secondary,
        notes=notes,
    )
    return {"species_id": species_id}


@mcp.tool()
async def add_guidance(
    instruction: str,
    context: str = "",
    priority: int = 0,
) -> dict:
    """Add a user guidance instruction to the knowledge base.

    Stores a new active instruction that will be surfaced in every
    subsequent ``observe()`` call until completed or superseded.

    Args:
        instruction: The instruction text (e.g. ``"Head to Rustboro City"``).
        context: Optional supplementary context for the instruction.
        priority: Higher values surface first (0 = normal, 10 = urgent).

    Returns:
        Dict with ``id`` of the newly inserted row.
    """
    row_id = await _get_kb().add_user_guidance(
        instruction=instruction,
        context=context,
        priority=priority,
    )
    return {"id": row_id}


@mcp.tool()
async def update_guidance_status(guidance_id: int, status: str) -> dict:
    """Update the status of a guidance entry.

    Call this when an instruction has been carried out or is no longer
    relevant, so it stops appearing in future observations.

    Args:
        guidance_id: The ``id`` returned when the guidance was created.
        status: One of ``active``, ``completed``, or ``superseded``.

    Returns:
        Dict confirming the update.
    """
    await _get_kb().update_guidance_status(guidance_id=guidance_id, status=status)
    return {"guidance_id": guidance_id, "status": status}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point: start the FastMCP server using stdio transport."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp.run()


def admin_stop() -> None:
    """CLI entry point: stop any running pokemon-mcp session.

    Sends SIGTERM to every running ``pokemon-mcp`` process so the lifespan
    shutdown runs cleanly (disconnects from mGBA, closes the knowledge base).
    The agent will restart the server automatically on the next tool call.

    Usage::

        uv run pokemon-mcp-admin
    """
    result = subprocess.run(
        ["pgrep", "-f", "pokemon-mcp"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids = [int(p) for p in result.stdout.strip().split() if p.strip()]
    own_pid = os.getpid()
    pids = [p for p in pids if p != own_pid]

    if not pids:
        print("No running pokemon-mcp session found.")
        return

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Stopped pokemon-mcp session (PID {pid}).")
        except ProcessLookupError:
            print(f"PID {pid} already gone.")

    print("MCP server will restart automatically on the next tool call.")


def admin_screenshot() -> None:
    """CLI entry point: save the current annotated screenshot.

    Reads player state from ``data/current_observation.txt`` (written by the
    running MCP server) and the latest screenshot from SCREENSHOT_DIR, so no
    mGBA connection is required — works even when a Claude MCP session is live.

    Respects the same environment variables as the MCP server:
        SCREENSHOT_DIR (default: data/screenshots)
        KB_PATH        (default: data/knowledge/knowledge.db)
        OBS_FILE       (default: data/current_observation.txt)

    Usage::

        uv run pokemon-mcp-screenshot
    """

    # Load .env from project root (no-op if absent; real env vars take precedence)
    _env_file = Path(__file__).parent.parent.parent / ".env"
    if _env_file.exists():
        for _line in _env_file.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

    def _parse_obs(obs_path: Path) -> tuple[int, int, int, bool]:
        """Return (player_x, player_y, map_id, in_battle) from observation txt."""
        text = obs_path.read_text()
        loc = re.search(r"Location:.*\(Map (\d+)\) \| X:(\d+), Y:(\d+)", text)
        if not loc:
            raise ValueError(f"Cannot parse location from {obs_path}")
        map_id = int(loc.group(1))
        player_x = int(loc.group(2))
        player_y = int(loc.group(3))
        in_battle = bool(re.search(r"In Battle: Yes", text))
        return player_x, player_y, map_id, in_battle

    async def _run() -> None:
        screenshot_dir = Path(os.environ.get("SCREENSHOT_DIR", "data/screenshots"))
        kb_path = Path(os.environ.get("KB_PATH", "data/knowledge/knowledge.db"))
        obs_path = Path(os.environ.get("OBS_FILE", "data/current_observation.txt"))

        player_x, player_y, map_id, in_battle = _parse_obs(obs_path)

        kb = KnowledgeBase(kb_path)
        await kb.initialize()
        map_tiles = await kb.get_map_tiles(map_id)
        await kb.close()

        pngs = sorted(screenshot_dir.glob("*.png"), key=lambda p: p.stat().st_mtime)
        if not pngs:
            raise FileNotFoundError(f"No screenshots in {screenshot_dir}")
        screenshot_path = pngs[-1]

        result = _annotate_screenshot(
            screenshot_path,
            player_x,
            player_y,
            map_tiles,
            in_battle=in_battle,
        )

        out = Path("annotated_preview.png")
        out.write_bytes(result)
        print(f"Saved {out}  (Map {map_id} | x={player_x}, y={player_y} | {len(map_tiles)} tiles)")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
