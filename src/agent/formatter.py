"""Observation formatting and decision parsing for CLI agent integration.

ObservationFormatter converts a game Observation into the text prompt that
the agent reads each iteration. DecisionParser extracts the agent's structured
JSON decision from its text response.
"""
import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import AgentDecision, Observation

logger = logging.getLogger(__name__)


# Tile type to grid character mapping
_TILE_CHARS: dict[str, str] = {
    "passable":      ".",
    "blocked":       "X",
    "grass":         "~",
    "water":         "W",
    "ledge_south":   "v",
    "ledge_north":   "^",
    "ledge_west":    "<",
    "ledge_east":    ">",
    "npc":           "N",
    "item":          "i",
    "rock_smash":    "r",
    "rock_strength": "R",
    "tree_cut":      "T",
    "unknown":       "?",
}

_AGENT_PROTOCOL = """\
AGENT PROTOCOL
  Navigation : The screenshot is your PRIMARY navigation guide.
               Your current tile is highlighted with a yellow border in the image.
               Known tile types are labelled in each tile's top-left corner.
               Take ONE step at a time in unknown terrain — observe after each step.
               Only batch movement on routes already confirmed passable.
  Map memory : After every move attempt call record_tile(map_id, x, y, tile_type).
               This is not optional! Build your spatial map by recording every explored tile.
               tile_type: passable blocked grass water ledge_south ledge_north
                          ledge_west ledge_east npc item rock_smash rock_strength
                          tree_cut unknown
               Before navigating call get_map_tiles(map_id) to recall known terrain.
               Prefer fully passable routes, pass grass tiles to gain EXP or catch Pokemon.
  Knowledge  : Record NPCs, items, events with record_discovery().
               Record badges, captures, evolutions with record_progress().
               Use lookup_pokemon() / lookup_move() before battles for type advantage.
  Guidance   : get_active_guidance() shows current objectives with their IDs.
               Mark done objectives: update_guidance_status(id, 'completed').\
"""


class ObservationFormatter:
    """Formats game observations into text prompts for agent."""

    def format(
        self,
        observation: "Observation",
        active_guidance: list[dict],
        relevant_knowledge: list[dict],
        map_tiles: list[dict] | None = None,
    ) -> str:
        """Produce the formatted observation text for agent.

        Args:
            observation: Current game observation including state and screenshot.
            active_guidance: Active user guidance entries from the knowledge base.
            relevant_knowledge: Relevant discoveries from the knowledge base.
            map_tiles: All recorded tiles for the current map, or None to omit
                the MAP TILES section.

        Returns:
            Multi-line string ready to be printed for agent to read.
        """
        gs = observation.game_state
        lines: list[str] = []

        # Agent protocol — always first so the agent always has it in view
        lines.append(_AGENT_PROTOCOL)
        lines.append("")

        # If screenshot is missing, warn at the top — otherwise the image block is already inline
        if observation.screenshot_path is None:
            logger.warning(
                "No screenshot available for frame %s — proceeding in degraded mode",
                observation.frame_number,
            )
            lines.append("[WARNING: No screenshot — visual context unavailable this turn]")
            lines.append("")

        lines.append("=" * 60)
        lines.append("POKEMON EMERALD — GAME STATE")
        lines.append("=" * 60)
        lines.append(f"Frame: {observation.frame_number}")
        lines.append("")

        # Player state
        lines.append("PLAYER STATE:")
        lines.append(
            f"  Location: {gs.map_name} (Map {gs.map_id}) | "
            f"X:{gs.player_x}, Y:{gs.player_y}"
        )
        if gs.badges:
            lines.append(f"  Badges ({len(gs.badges)}): {', '.join(gs.badges)}")
        else:
            lines.append("  Badges: None yet")
        lines.append(f"  In Battle: {'YES' if gs.in_battle else 'No'}")
        if map_tiles is not None:
            lines.append(self._format_neighbours(gs.player_x, gs.player_y, map_tiles))
        lines.append(f"  Can Save: {'Yes' if gs.can_save else 'No'}")
        lines.append("")

        # Party
        lines.append(f"PARTY ({gs.party_count} Pokemon):")
        if gs.party:
            for p in gs.party:
                hp_bar = self._hp_bar(p.current_hp, p.max_hp)
                species = p.species_name or p.nickname
                lines.append(
                    f"  {p.slot}. {species} [{p.nickname}] Lv.{p.level} "
                    f"HP:{p.current_hp}/{p.max_hp} {hp_bar} [{p.status}]"
                )
                if p.moves:
                    moves_str = ", ".join(m.name for m in p.moves)
                    lines.append(f"     Moves: {moves_str}")
                elif p.move_ids:
                    ids_str = ", ".join(f"#{mid}" for mid in p.move_ids)
                    lines.append(f"     Move IDs: {ids_str}")
        else:
            lines.append("  No Pokemon in party")
        lines.append("")

        # Bag and PC (extended state — fetched every 5 iterations)
        if observation.extended_state is not None:
            es = observation.extended_state
            if es.bag:
                lines.append("BAG:")
                for pocket, items in es.bag.items():
                    if items:
                        items_str = ", ".join(
                            f"{it.name} x{it.quantity}" for it in items
                        )
                        lines.append(f"  {pocket}: {items_str}")
                    else:
                        lines.append(f"  {pocket}: (empty)")
                lines.append("")

            if es.pc_boxes:
                lines.append("PC BOXES:")
                for box in es.pc_boxes:
                    mons = ", ".join(p.nickname for p in box["pokemon"])
                    lines.append(f"  Box {box['box']}: {mons}")
                lines.append("")

        # Active user guidance
        if active_guidance:
            lines.append("ACTIVE USER GUIDANCE (follow these instructions):")
            for i, g in enumerate(active_guidance[:5], 1):
                lines.append(f"  {i}. [{g['priority']}/10] {g['instruction']}")
            lines.append("")

        # Relevant knowledge
        if relevant_knowledge:
            lines.append("RELEVANT KNOWLEDGE FROM MEMORY:")
            for k in relevant_knowledge[:3]:
                lines.append(
                    f"  [{k['category']}] {k['title']}: {k['description']}"
                )
            lines.append("")

        return "\n".join(lines)

    def _format_neighbours(
        self,
        player_x: int,
        player_y: int,
        tiles: list[dict],
    ) -> str:
        """Return the four cardinal neighbour tile types as a single line.

        Unknown tiles (not yet recorded) are shown as '?'.
        """
        lookup: dict[tuple[int, int], str] = {
            (t["x"], t["y"]): t["tile_type"] for t in tiles
        }
        n = lookup.get((player_x, player_y - 1), "?")
        s = lookup.get((player_x, player_y + 1), "?")
        e = lookup.get((player_x + 1, player_y), "?")
        w = lookup.get((player_x - 1, player_y), "?")
        return f"  Neighbours: N={n}  S={s}  E={e}  W={w}"

    def _hp_bar(self, current: int, max_hp: int, width: int = 10) -> str:
        if max_hp == 0:
            return "[----------]"
        ratio = current / max_hp
        filled = round(ratio * width)
        bar = "█" * filled + "─" * (width - filled)
        return f"[{bar}]"


class DecisionParser:
    """Extracts and validates AgentDecision from agent's text response."""

    VALID_ACTIONS = {"press_button", "press_buttons", "wait", "save_game", "pause"}
    VALID_BUTTONS = {
        "A", "B", "UP", "DOWN", "LEFT", "RIGHT", "START", "SELECT", "L", "R"
    }

    def parse(self, response_text: str) -> "AgentDecision":
        """Parse agent's text response into an AgentDecision.

        Extracts JSON from the response (handles optional ```json fencing),
        validates action type and params, and returns a safe fallback on any
        parse failure.

        Args:
            response_text: Raw text from the agent including the JSON decision.

        Returns:
            An AgentDecision with validated action_type and action_params.
        """
        from .models import AgentDecision

        json_str = self._extract_json(response_text)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse Agent's decision JSON: %s", e)
            logger.warning("Raw response: %.500s", response_text)
            return self._safe_fallback()

        action_type = data.get("action_type", "wait")
        if action_type not in self.VALID_ACTIONS:
            logger.warning("Invalid action_type %r, falling back to wait", action_type)
            action_type = "wait"

        params = self._validate_params(action_type, data.get("action_params", {}))

        screenshot_required = bool(data.get("screenshot_required", False))
        if not screenshot_required:
            logger.warning(
                "Decision JSON missing or false 'screenshot_required' — "
                "Agent may not have read the screenshot before deciding"
            )

        return AgentDecision(
            action_type=action_type,
            action_params=params,
            reasoning=data.get("reasoning", ""),
            knowledge_to_store=data.get("knowledge_to_store", []),
            screenshot_required=screenshot_required,
        )

    def _extract_json(self, text: str) -> str:
        """Extract the first JSON object from text, handling ```json fences."""
        # Try ```json ... ``` block first
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            return match.group(1)
        # Fall back to bracket-counting scan for arbitrary nesting
        start = text.find("{")
        if start == -1:
            return "{}"
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return "{}"

    def _validate_params(self, action_type: str, params: dict) -> dict:
        if action_type == "press_button":
            button = str(params.get("button", "A")).upper()
            if button not in self.VALID_BUTTONS:
                button = "A"
            return {
                "button": button,
                "duration_frames": max(1, int(params.get("duration_frames", 5))),
            }
        elif action_type == "press_buttons":
            buttons = [
                b.upper()
                for b in params.get("buttons", ["A"])
                if str(b).upper() in self.VALID_BUTTONS
            ]
            return {
                "buttons": buttons or ["A"],
                "duration_frames": max(1, int(params.get("duration_frames", 5))),
            }
        elif action_type == "wait":
            return {"frames": max(1, int(params.get("frames", 30)))}
        return {}

    def _safe_fallback(self) -> "AgentDecision":
        from .models import AgentDecision

        return AgentDecision(
            action_type="wait",
            action_params={"frames": 60},
            reasoning="Parse failed — waiting 1 second before retry",
        )
