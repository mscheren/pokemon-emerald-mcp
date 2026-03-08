"""Core data models for the Pokemon Emerald AI Agent.

All models are plain dataclasses with no I/O dependencies.
"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class Move:
    """Represents a single Pokemon move."""

    name: str = "???"
    type: str = "Normal"
    power: Optional[int] = None
    pp: int = 0
    max_pp: int = 0
    category: str = "status"  # physical, special, status

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Move":
        """Create a Move from a dict, applying sensible defaults."""
        return cls(
            name=data.get("name", "???"),
            type=data.get("type", "Normal"),
            power=data.get("power"),
            pp=data.get("pp", 0),
            max_pp=data.get("max_pp", 0),
            category=data.get("category", "status"),
        )


@dataclass
class PartyPokemon:
    """Represents a single Pokemon in the player's party."""

    slot: int = 0
    nickname: str = "???"
    level: int = 1
    current_hp: int = 0
    max_hp: int = 0
    attack: int = 0
    defense: int = 0
    speed: int = 0
    sp_attack: int = 0
    sp_defense: int = 0
    status: str = "healthy"
    species_id: Optional[int] = None
    species_name: Optional[str] = None
    types: list[str] = field(default_factory=list)
    moves: list[Move] = field(default_factory=list)
    move_ids: list[int] = field(default_factory=list)

    _VALID_STATUSES = {
        "healthy", "poisoned", "badly_poisoned", "paralyzed",
        "burned", "frozen", "asleep", "fainted",
    }

    def __post_init__(self) -> None:
        # Clamp level to valid GBA range
        self.level = min(max(self.level, 1), 100)
        # Clamp current_hp to [0, max_hp]
        self.current_hp = max(0, min(self.current_hp, self.max_hp))
        # Validate status — fall back to healthy if unknown
        if self.status not in self._VALID_STATUSES:
            self.status = "healthy"

    @property
    def hp_percent(self) -> float:
        """Current HP as a fraction of max HP."""
        if self.max_hp == 0:
            return 0.0
        return self.current_hp / self.max_hp

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PartyPokemon":
        """Create a PartyPokemon from a Lua-provided dict."""
        raw_moves = data.get("moves", [])
        moves: list[Move] = []
        for m in raw_moves:
            if isinstance(m, dict):
                moves.append(Move.from_dict(m))
            elif isinstance(m, str):
                # Legacy format: list of move name strings
                moves.append(Move(name=m))

        return cls(
            slot=data.get("slot", 0),
            nickname=data.get("nickname", "???"),
            level=data.get("level", 1),
            current_hp=data.get("current_hp", 0),
            max_hp=data.get("max_hp", 0),
            attack=data.get("attack", 0),
            defense=data.get("defense", 0),
            speed=data.get("speed", 0),
            sp_attack=data.get("sp_attack", 0),
            sp_defense=data.get("sp_defense", 0),
            status=data.get("status", "healthy"),
            species_id=data.get("species_id"),
            species_name=data.get("species_name"),
            types=data.get("types", []),
            moves=moves,
            move_ids=[int(m) for m in data.get("move_ids", []) if m],
        )


@dataclass
class GameState:
    """Snapshot of the current game state as read from mGBA memory."""

    frame_number: int = 0
    map_id: int = 0
    map_name: str = "Unknown"
    player_x: int = 0
    player_y: int = 0
    party_count: int = 0
    party: list[PartyPokemon] = field(default_factory=list)
    badges: list[str] = field(default_factory=list)
    in_battle: bool = False
    can_save: bool = True
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameState":
        """Create a GameState from a Lua-provided payload dict."""
        party_data = data.get("party", [])
        party = [PartyPokemon.from_dict(p) for p in party_data]
        return cls(
            frame_number=data.get("frame_number", 0),
            map_id=data.get("map_id", 0),
            map_name=data.get("map_name", "Unknown"),
            player_x=data.get("player_x", 0),
            player_y=data.get("player_y", 0),
            party_count=data.get("party_count", len(party)),
            party=party,
            badges=data.get("badges", []),
            in_battle=data.get("in_battle", False),
            can_save=data.get("can_save", True),
        )


@dataclass
class Observation:
    """A single agent observation combining game state and screenshot."""

    game_state: GameState
    frame_number: int
    screenshot_path: Optional[Path] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    observation_id: Optional[int] = None
    extended_state: Optional["ExtendedState"] = None


@dataclass
class SequenceStep:
    """One step in a multi-action sequence issued by the agent."""

    action: str = "wait"
    button: Optional[str] = None
    buttons: list[str] = field(default_factory=list)
    duration_frames: int = 8
    wait_frames: int = 0

    _VALID_ACTIONS = {"press_button", "press_buttons", "wait"}

    def validate(self) -> None:
        """Raise ValueError if the step is malformed."""
        if self.action not in self._VALID_ACTIONS:
            raise ValueError(
                f"Invalid sequence step action: {self.action!r}. "
                f"Must be one of {sorted(self._VALID_ACTIONS)}"
            )
        if self.action == "press_button" and not self.button:
            raise ValueError("press_button step requires a 'button' field")
        if self.action == "press_buttons" and not self.buttons:
            raise ValueError("press_buttons step requires a non-empty 'buttons' list")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SequenceStep":
        return cls(
            action=data.get("action", "wait"),
            button=data.get("button"),
            buttons=data.get("buttons", []),
            duration_frames=data.get("duration_frames", 8),
            wait_frames=data.get("wait_frames", 0),
        )


@dataclass
class AgentDecision:
    """A decision produced by the agent for the current observation."""

    action_type: str
    action_params: dict[str, Any]
    reasoning: str = ""
    knowledge_to_store: list[dict[str, Any]] = field(default_factory=list)
    screenshot_required: bool = False

    _VALID_ACTIONS = {
        "press_button",
        "press_buttons",
        "press_sequence",
        "wait",
        "save_game",
        "pause",
    }

    def validate(self) -> None:
        """Raise ValueError if the decision is malformed."""
        if self.action_type not in self._VALID_ACTIONS:
            raise ValueError(
                f"Invalid action_type: {self.action_type!r}. "
                f"Must be one of {sorted(self._VALID_ACTIONS)}"
            )
        if self.action_type == "press_sequence":
            seq = self.action_params.get("sequence", [])
            if not seq:
                raise ValueError("press_sequence requires a non-empty 'sequence' list")
            for i, raw_step in enumerate(seq):
                step = SequenceStep.from_dict(raw_step)
                try:
                    step.validate()
                except ValueError as e:
                    raise ValueError(f"Invalid sequence step {i}: {e}") from e


@dataclass
class KnowledgeEntry:
    """A piece of knowledge the agent has learned and wants to remember."""

    category: str
    title: str
    description: str
    map_id: Optional[int] = None
    x_coord: Optional[int] = None
    y_coord: Optional[int] = None


@dataclass
class UserGuidance:
    """An instruction or hint provided by the human operator."""

    instruction: str
    context: str = ""
    status: str = "active"
    priority: int = 0


@dataclass
class BagItem:
    """A single item slot in the player's bag."""

    item_id: int
    name: str
    quantity: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BagItem":
        return cls(
            item_id=data["item_id"],
            name=data.get("name", f"Item#{data['item_id']}"),
            quantity=data.get("quantity", 1),
        )


@dataclass
class PCPokemon:
    """A Pokemon stored in a PC box (unencrypted fields only)."""

    box: int
    slot: int
    nickname: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PCPokemon":
        return cls(
            box=data["box"],
            slot=data["slot"],
            nickname=data.get("nickname", "???"),
        )


@dataclass
class ExtendedState:
    """Bag and PC box data returned by the get_extended_state action."""

    bag: dict[str, list[BagItem]]  # pocket name → items
    pc_boxes: list[dict]           # [{box, pokemon:[PCPokemon]}]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtendedState":
        bag: dict[str, list[BagItem]] = {}
        for pocket, items in data.get("bag", {}).items():
            bag[pocket] = [BagItem.from_dict(i) for i in items]
        pc_boxes_raw = data.get("pc_boxes", [])
        pc_boxes = [
            {
                "box": b["box"],
                "pokemon": [PCPokemon.from_dict(p) for p in b.get("pokemon", [])],
            }
            for b in pc_boxes_raw
        ]
        return cls(bag=bag, pc_boxes=pc_boxes)
