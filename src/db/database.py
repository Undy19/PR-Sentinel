"""SQLite persistence layer for PR history (aiosqlite)."""

from __future__ import annotations

import logging
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pr_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_number INTEGER,
    title TEXT,
    url TEXT,
    risk_level TEXT,
    timestamp TEXT
);
"""


class Database:
    """Thin async wrapper over a single SQLite connection.

    A single aiosqlite connection is safe for concurrent async use:
    aiosqlite serializes statement execution through its worker thread.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the database and create the schema if missing (idempotent)."""
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected; call `await connect()` first")
        return self._conn

    async def record_pr(
        self, pr_number: int, title: str, url: str, risk_level: str, timestamp: str
    ) -> None:
        """Append one reviewed PR to the history table."""
        conn = self._require_conn()
        await conn.execute(
            "INSERT INTO pr_history (pr_number, title, url, risk_level, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (pr_number, title, url, risk_level, timestamp),
        )
        await conn.commit()

    async def get_pr_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent history rows, newest first."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT id, pr_number, title, url, risk_level, timestamp "
            "FROM pr_history ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "pr_number": row[1],
                "title": row[2],
                "url": row[3],
                "risk_level": row[4],
                "timestamp": row[5],
            }
            for row in rows
        ]

    async def close(self) -> None:
        """Close the connection (idempotent)."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
