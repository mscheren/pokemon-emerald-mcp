"""Database setup script for the Pokemon AI Agent knowledge base.

Creates the SQLite database at ``data/knowledge/pokemon_knowledge.db`` and
applies the full schema. Safe to run multiple times — all statements use
``IF NOT EXISTS``.

Usage::

    uv run python scripts/setup_db.py

After running you can inspect the result with::

    sqlite3 data/knowledge/pokemon_knowledge.db .schema
"""
import asyncio
import sys
from pathlib import Path

# Ensure the project root is on sys.path when run directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.knowledge import KnowledgeBase  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "knowledge" / "pokemon_knowledge.db"


async def main() -> None:
    """Initialise the knowledge base and print a summary of the schema."""
    print(f"Initialising knowledge base at: {DB_PATH}")
    kb = KnowledgeBase(DB_PATH)
    await kb.initialize()

    conn = kb._require_conn()

    # Print tables
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ) as cur:
        tables = [row["name"] for row in await cur.fetchall()]
    print(f"\nTables ({len(tables)}):")
    for t in tables:
        print(f"  {t}")

    # Print indexes
    async with conn.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY tbl_name, name"
    ) as cur:
        indexes = [(row["name"], row["tbl_name"]) for row in await cur.fetchall()]
    print(f"\nIndexes ({len(indexes)}):")
    for name, tbl in indexes:
        print(f"  {name}  (on {tbl})")

    await kb.close()
    print(f"\nDone. Inspect with: sqlite3 {DB_PATH} .schema")


if __name__ == "__main__":
    asyncio.run(main())
