"""Async SQLite knowledge base for the Pokemon AI agent.

Stores game discoveries, user guidance, strategies, Pokemon knowledge,
and progress milestones accumulated during gameplay.
"""
import json
import logging
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class KnowledgeBase:
    """Async SQLite-backed knowledge store for the Pokemon agent.

    Wraps an ``aiosqlite`` connection and exposes typed async methods for
    all CRUD operations. A single ``KnowledgeBase`` instance is shared by
    the controller for the lifetime of a session.

    Attributes:
        db_path: Filesystem path to the SQLite database file.
    """

    def __init__(self, db_path: Path) -> None:
        """Initialise the KnowledgeBase without opening a connection.

        Args:
            db_path: Path to the SQLite ``.db`` file. The parent directory
                is created automatically by :meth:`initialize`.
        """
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Create the database file and apply the schema.

        This method is idempotent — every table and index uses
        ``IF NOT EXISTS``, so repeated calls are safe.

        Raises:
            FileNotFoundError: If :data:`SCHEMA_PATH` does not exist.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        schema_sql = SCHEMA_PATH.read_text()
        await self._conn.executescript(schema_sql)
        await self._conn.commit()
        logger.info("Knowledge base initialized at %s", self.db_path)

    async def close(self) -> None:
        """Close the SQLite connection gracefully."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Knowledge base closed")

    def _require_conn(self) -> aiosqlite.Connection:
        """Return the active connection or raise if not initialized.

        Returns:
            The active ``aiosqlite.Connection``.

        Raises:
            RuntimeError: If :meth:`initialize` has not been called.
        """
        if not self._conn:
            raise RuntimeError(
                "KnowledgeBase not initialized. Call initialize() first."
            )
        return self._conn

    # ------------------------------------------------------------------ #
    # Discoveries                                                          #
    # ------------------------------------------------------------------ #

    async def record_discovery(
        self,
        category: str,
        title: str,
        description: str,
        map_id: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> int:
        """Insert a new game discovery into the knowledge base.

        Args:
            category: Semantic category — one of ``location``, ``item``,
                ``npc``, ``mechanic``, ``strategy``, or ``pokemon``.
            title: Short human-readable label for the discovery.
            description: Full description of what was learned.
            map_id: Map identifier where the discovery was made, or ``None``.
            x: Tile X coordinate of discovery, or ``None``.
            y: Tile Y coordinate of discovery, or ``None``.
            metadata: Optional dict of extra structured data; stored as a
                JSON string in the ``metadata`` column.

        Returns:
            The ``id`` (row ID) of the newly inserted row.
        """
        conn = self._require_conn()
        meta_str = json.dumps(metadata) if metadata else None
        async with conn.execute(
            """INSERT INTO discoveries
                   (category, title, description, map_id, x_coord, y_coord, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (category, title, description, map_id, x, y, meta_str),
        ) as cursor:
            await conn.commit()
            return cursor.lastrowid

    async def get_relevant_knowledge(
        self, context: str, limit: int = 3
    ) -> list[dict]:
        """Search discoveries by keyword in title or description.

        Args:
            context: Substring to match (case-insensitive ``LIKE`` search).
            limit: Maximum number of results to return.

        Returns:
            List of dicts with keys ``category``, ``title``, and
            ``description``, ordered by most recent first.
        """
        conn = self._require_conn()
        async with conn.execute(
            """SELECT category, title, description
               FROM discoveries
               WHERE title LIKE ? OR description LIKE ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (f"%{context}%", f"%{context}%", limit),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    # ------------------------------------------------------------------ #
    # PokeAPI Cache                                                        #
    # ------------------------------------------------------------------ #

    async def get_pokeapi_cache(self, cache_key: str) -> Optional[str]:
        """Return cached JSON string for cache_key, or None on miss."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT data FROM pokeapi_cache WHERE cache_key = ?", (cache_key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["data"] if row else None

    async def set_pokeapi_cache(self, cache_key: str, data: str) -> None:
        """Insert or replace a JSON string in the PokeAPI cache."""
        conn = self._require_conn()
        await conn.execute(
            "INSERT OR REPLACE INTO pokeapi_cache (cache_key, data) VALUES (?, ?)",
            (cache_key, data),
        )
        await conn.commit()

    # ------------------------------------------------------------------ #
    # User Guidance                                                        #
    # ------------------------------------------------------------------ #

    async def add_user_guidance(
        self,
        instruction: str,
        context: str = "",
        priority: int = 0,
    ) -> int:
        """Persist a new user guidance instruction.

        Args:
            instruction: Raw instruction text (e.g. ``"Catch a Ralts"``).
            context: Optional supplementary context for the instruction.
            priority: Numeric priority; higher values surface first (0 = normal).

        Returns:
            The ``id`` of the newly inserted row.
        """
        conn = self._require_conn()
        async with conn.execute(
            """INSERT INTO user_guidance (instruction, context, status, priority)
               VALUES (?, ?, 'active', ?)""",
            (instruction, context, priority),
        ) as cursor:
            await conn.commit()
            return cursor.lastrowid

    async def get_active_guidance(self) -> list[dict]:
        """Return all active user guidance, sorted by priority then recency.

        Returns:
            List of dicts with keys ``id``, ``instruction``, ``context``,
            ``priority``, and ``timestamp``.
        """
        conn = self._require_conn()
        async with conn.execute(
            """SELECT id, instruction, context, priority, timestamp
               FROM user_guidance
               WHERE status = 'active'
               ORDER BY priority DESC, timestamp DESC"""
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def update_guidance_status(self, guidance_id: int, status: str) -> None:
        """Update the lifecycle status of a guidance entry.

        Args:
            guidance_id: Primary key of the row to update.
            status: New status — one of ``active``, ``completed``, or
                ``superseded``.
        """
        conn = self._require_conn()
        await conn.execute(
            "UPDATE user_guidance SET status = ? WHERE id = ?",
            (status, guidance_id),
        )
        await conn.commit()

    # ------------------------------------------------------------------ #
    # Strategies                                                           #
    # ------------------------------------------------------------------ #

    async def record_strategy(
        self,
        situation: str,
        approach: str,
        outcome: str = "",
        effectiveness: int = 0,
    ) -> int:
        """Record a battle or exploration strategy.

        Args:
            situation: Description of the game situation (e.g. ``"wild battle"``).
            approach: Strategy applied (e.g. ``"use type advantage"``).
            outcome: What happened as a result of using this strategy.
            effectiveness: Rating 0–5 (0 = unknown, 5 = very effective).

        Returns:
            The ``id`` of the newly inserted row.
        """
        conn = self._require_conn()
        async with conn.execute(
            """INSERT INTO strategies (situation, approach, outcome, effectiveness)
               VALUES (?, ?, ?, ?)""",
            (situation, approach, outcome, effectiveness),
        ) as cursor:
            await conn.commit()
            return cursor.lastrowid

    async def search_strategies(self, keyword: str, limit: int = 5) -> list[dict]:
        """Search strategies by keyword in situation or approach fields.

        Args:
            keyword: Substring to match against ``situation`` or ``approach``.
            limit: Maximum number of results.

        Returns:
            List of dicts ordered by effectiveness descending, then recency.
        """
        conn = self._require_conn()
        async with conn.execute(
            """SELECT situation, approach, outcome, effectiveness
               FROM strategies
               WHERE situation LIKE ? OR approach LIKE ?
               ORDER BY effectiveness DESC, timestamp DESC
               LIMIT ?""",
            (f"%{keyword}%", f"%{keyword}%", limit),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    # ------------------------------------------------------------------ #
    # Pokemon Knowledge                                                    #
    # ------------------------------------------------------------------ #

    async def record_pokemon(
        self,
        species_id: int,
        species_name: str,
        type_primary: Optional[str] = None,
        type_secondary: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Upsert a Pokemon species entry (insert or update ``last_seen``).

        If the species already exists, only ``last_seen`` is updated and
        ``notes`` is replaced only when a non-``None`` value is provided.

        Args:
            species_id: National Pokédex number.
            species_name: Human-readable species name (e.g. ``"Torchic"``).
            type_primary: Primary elemental type, or ``None`` if unknown.
            type_secondary: Secondary elemental type, or ``None``.
            notes: Free-text notes, or ``None`` to preserve existing notes.
        """
        conn = self._require_conn()
        await conn.execute(
            """INSERT INTO pokemon_knowledge
                   (species_id, species_name, type_primary, type_secondary, notes, last_seen)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(species_id) DO UPDATE SET
                   last_seen = CURRENT_TIMESTAMP,
                   notes = COALESCE(excluded.notes, notes)""",
            (species_id, species_name, type_primary, type_secondary, notes),
        )
        await conn.commit()

    async def get_pokemon_knowledge(self, species_id: int) -> Optional[dict]:
        """Retrieve stored knowledge for a specific Pokemon species.

        Args:
            species_id: National Pokédex number to look up.

        Returns:
            Dict of all columns for the matching row, or ``None`` if the
            species has not been recorded yet.
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT * FROM pokemon_knowledge WHERE species_id = ?", (species_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # Progress                                                             #
    # ------------------------------------------------------------------ #

    async def record_progress(
        self,
        event_type: str,
        event_name: str,
        details: str = "",
    ) -> int:
        """Record a game progress milestone.

        Args:
            event_type: One of ``badge``, ``capture``, ``milestone``, or
                ``evolution``.
            event_name: Name of the event (e.g. ``"Stone Badge"``).
            details: Optional extra detail about the event.

        Returns:
            The ``id`` of the newly inserted row.
        """
        conn = self._require_conn()
        async with conn.execute(
            "INSERT INTO progress (event_type, event_name, details) VALUES (?, ?, ?)",
            (event_type, event_name, details),
        ) as cursor:
            await conn.commit()
            return cursor.lastrowid

    async def get_progress_summary(self) -> dict:
        """Summarise all recorded progress events by type.

        Returns:
            Dict with keys:

            - ``badges`` — list of badge event names in chronological order
            - ``captures`` — total capture count
            - ``milestones`` — total milestone count
            - ``evolutions`` — total evolution count
        """
        conn = self._require_conn()
        summary: dict = {
            "badges": [],
            "captures": 0,
            "milestones": 0,
            "evolutions": 0,
        }
        async with conn.execute(
            "SELECT event_type, event_name FROM progress ORDER BY timestamp"
        ) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                et = row["event_type"]
                if et == "badge":
                    summary["badges"].append(row["event_name"])
                elif et == "capture":
                    summary["captures"] += 1
                elif et == "milestone":
                    summary["milestones"] += 1
                elif et == "evolution":
                    summary["evolutions"] += 1
        return summary
