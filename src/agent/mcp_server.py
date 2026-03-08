"""MCP server — thin wrapper over the MGBAClient / GameController stack.

Run with:
    uv run pokemon-mcp

Environment variables:
    MGBA_HOST      — mGBA Lua socket host (default: 127.0.0.1)
    MGBA_PORT      — mGBA Lua socket port (default: 5000)
    SCREENSHOT_DIR — screenshot output directory (default: data/screenshots)
    KB_PATH        — SQLite knowledge-base path (default: data/knowledge/pokemon_knowledge.db)
"""

import io
import logging
import os
import signal
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage

from .controller import GameController
from .formatter import ObservationFormatter
from .knowledge import KnowledgeBase
from .mgba_client import MGBAClient
from .models import GameState, Observation, SequenceStep

logger = logging.getLogger(__name__)

_client: MGBAClient | None = None
_kb: KnowledgeBase | None = None
_controller: GameController | None = None
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


def _scale_screenshot(path: Path, scale: int = 4) -> bytes:
    """Return PNG bytes for *path* scaled up by *scale* using nearest-neighbour.

    GBA native resolution is 240×160 — very hard to interpret at 1:1.
    4× gives 960×640 which is clearly readable while preserving pixel art edges.
    """
    with PILImage.open(path) as img:
        w, h = img.size
        scaled = img.resize((w * scale, h * scale), PILImage.NEAREST)
        buf = io.BytesIO()
        scaled.save(buf, format="PNG")
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(server):
    global _client, _kb, _controller, _screenshot_dir

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

    logger.info("MCP server ready")
    yield

    if _client:
        await _client.disconnect()
    if _kb:
        await _kb.close()


mcp = FastMCP("pokemon-agent", lifespan=_lifespan)


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
          - ``{"type": "text",  ...}`` — formatted game state
          - ``{"type": "image", ...}`` — base64 PNG screenshot (omitted on capture failure)
    """
    state_data = await _get_client().request_state()
    game_state = GameState.from_dict(state_data)

    guidance: list[dict] = []
    knowledge: list[dict] = []
    try:
        guidance = await _get_kb().get_active_guidance()
        map_key = str(game_state.map_id) if game_state.map_id else "general"
        knowledge = await _get_kb().get_relevant_knowledge(map_key, limit=3)
    except Exception:
        pass

    # Capture screenshot via Lua emu:screenshot()
    # GameController.request_screenshot() writes frame_XXXXXXXX.png and returns
    # its Path, raising RuntimeError on Lua-side failure.
    screenshot_bytes: bytes | None = None
    screenshot_path: Path | None = None
    try:
        screenshot_path = await _get_controller().request_screenshot(game_state.frame_number)
        screenshot_bytes = _scale_screenshot(screenshot_path)
    except Exception as exc:
        logger.warning("Screenshot capture failed: %s", exc)

    observation = Observation(
        game_state=game_state,
        frame_number=game_state.frame_number,
        screenshot_path=screenshot_path,
    )
    obs_text = _formatter.format(observation, guidance, knowledge)
    OBSERVATION_FILE.write_text(obs_text)

    content: list = [obs_text]
    if screenshot_bytes:
        content.append(Image(data=screenshot_bytes, format="png"))
    return content


@mcp.tool()
async def get_observation() -> str:
    """Return the current game observation as text (text-only, no screenshot).

    Prefer ``observe()`` which returns both the game state and a screenshot in
    one unified multimodal call. Use ``get_observation`` only when you want
    the text state without an image (e.g. for fast status checks).
    """
    state_data = await _get_client().request_state()
    game_state = GameState.from_dict(state_data)

    screenshot_path = None

    guidance: list[dict] = []
    knowledge: list[dict] = []
    try:
        guidance = await _get_kb().get_active_guidance()
        map_key = str(game_state.map_id) if game_state.map_id else "general"
        knowledge = await _get_kb().get_relevant_knowledge(map_key, limit=3)
    except Exception:
        pass

    observation = Observation(
        game_state=game_state,
        frame_number=game_state.frame_number,
        screenshot_path=screenshot_path,
    )
    obs_text = _formatter.format(observation, guidance, knowledge)
    OBSERVATION_FILE.write_text(obs_text)
    return obs_text


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
    """Search the knowledge base for relevant entries.

    Args:
        query: Keyword to search for in discovery titles and descriptions.
        limit: Maximum number of results to return (default 5).
    """
    return await _get_kb().get_relevant_knowledge(query, limit=limit)


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


if __name__ == "__main__":
    main()
