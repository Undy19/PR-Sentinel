"""Expertise graph: parse git history into SQLite and recommend reviewers.

The graph maps ``author_login -> file_path`` to commit counts and last
commit timestamps, derived from ``git log --name-only``. Reviewer
recommendations score candidates by frequency of touches on the
requested files (40%) and recency with a 90-day half-life (60%).
"""

from __future__ import annotations

import asyncio
import logging
import math
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Self

import aiosqlite

logger = logging.getLogger(__name__)

RECENCY_HALF_LIFE_DAYS = 90.0
_LN2 = math.log(2.0)
_SECONDS_PER_DAY = 86_400.0

_HEX_DIGITS = frozenset("0123456789abcdef")


@dataclass
class Reviewer:
    """A recommended reviewer derived from commit history."""

    login: str
    name: str
    files_touched: int
    expertise_score: float


def _parse_timestamp(raw: str) -> str:
    """Normalize an ISO-8601 timestamp to UTC.

    Storing everything in UTC keeps lexicographic ordering equivalent
    to chronological ordering regardless of the original offsets.
    """
    try:
        dt = datetime.fromisoformat(raw.strip())
    except ValueError:
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _to_datetime(raw: str) -> datetime | None:
    """Parse a stored (UTC ISO) timestamp; ``None`` if unparseable."""
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class ExpertiseGraph:
    """SQLite-backed expertise graph built from a repository's git history.

    The constructor is ``async`` per the project spec, so prefer the
    :meth:`create` factory, which awaits the connection setup::

        graph = await ExpertiseGraph.create("expertise.db")
        await graph.build_from_repo("/path/to/repo")
        reviewers = await graph.recommend_reviewers(["src/app.py"])
        await graph.close()

    A bare ``ExpertiseGraph(path)`` call leaves the instance
    unconnected (its ``__init__`` coroutine is not auto-awaited).
    """

    _conn: aiosqlite.Connection | None = None

    def __new__(cls, db_path: str) -> Self:
        instance = super().__new__(cls)
        instance._db_path = db_path
        instance._conn = None
        return instance

    async def __init__(self, db_path: str) -> None:  # type: ignore[misc]
        """Open the SQLite database and create the schema if needed."""
        self._db_path = db_path
        self._conn = await aiosqlite.connect(db_path)
        await self._create_tables()

    @classmethod
    async def create(cls, db_path: str) -> ExpertiseGraph:
        """Construct and connect an :class:`ExpertiseGraph`."""
        instance = cls.__new__(cls, db_path)
        await instance.__init__(db_path)  # type: ignore[misc]
        return instance

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError(
                "ExpertiseGraph is not connected; use `await ExpertiseGraph.create(path)`"
            )
        return self._conn

    async def _create_tables(self) -> None:
        conn = self._require_conn()
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS commits (
                sha TEXT PRIMARY KEY,
                author TEXT NOT NULL,
                author_login TEXT NOT NULL,
                files TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS file_expertise (
                author_login TEXT NOT NULL,
                file_path TEXT NOT NULL,
                commit_count INTEGER NOT NULL DEFAULT 0,
                last_commit_ts TEXT NOT NULL,
                PRIMARY KEY (author_login, file_path)
            );
            CREATE INDEX IF NOT EXISTS idx_file_expertise_path
                ON file_expertise (file_path);
            """
        )
        await conn.commit()

    async def build_from_repo(self, repo_path: str) -> None:
        """Parse the repository's git history and rebuild the graph.

        Runs ``git log --format=%H|%an|%ae|%aI --name-only`` in
        ``repo_path`` and repopulates both tables (previous rows are
        replaced, since the graph is fully derived from git history).
        """
        conn = self._require_conn()

        loop = asyncio.get_running_loop()

        def _run_git() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                [
                    "git",
                    "log",
                    "--format=%H|%an|%ae|%aI",
                    "--name-only",
                ],
                cwd=repo_path,
                capture_output=True,
                check=False,
            )

        try:
            proc = await loop.run_in_executor(None, _run_git)
        except FileNotFoundError as exc:
            raise RuntimeError("git executable not found; is git installed?") from exc

        if proc.returncode != 0:
            raise RuntimeError(
                f"git log failed in {repo_path!r} (exit {proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace').strip()}"
            )

        stdout = proc.stdout

        commits = self._parse_git_log(stdout.decode("utf-8", "replace"))
        if not commits:
            logger.warning("No commits found in %r; graph left empty", repo_path)
            await conn.execute("DELETE FROM commits")
            await conn.execute("DELETE FROM file_expertise")
            await conn.commit()
            return

        await conn.execute("DELETE FROM commits")
        await conn.execute("DELETE FROM file_expertise")

        commit_rows = [
            (sha, author, login, ",".join(files), ts) for sha, author, login, ts, files in commits
        ]
        await conn.executemany(
            "INSERT OR IGNORE INTO commits (sha, author, author_login, files, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            commit_rows,
        )

        upsert_sql = (
            "INSERT INTO file_expertise (author_login, file_path, commit_count, last_commit_ts) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT (author_login, file_path) DO UPDATE SET "
            "commit_count = file_expertise.commit_count + 1, "
            "last_commit_ts = MAX(file_expertise.last_commit_ts, excluded.last_commit_ts)"
        )
        file_row_count = 0
        for _sha, _author, login, ts, files in commits:
            for file_path in files:
                await conn.execute(upsert_sql, (login, file_path, ts))
                file_row_count += 1

        await conn.commit()
        logger.info(
            "Built expertise graph from %r: %d commits, %d file-expertise rows",
            repo_path,
            len(commit_rows),
            file_row_count,
        )

    @staticmethod
    def _parse_git_log(output: str) -> list[tuple[str, str, str, str, list[str]]]:
        """Parse ``git log --name-only`` output.

        Returns ``(sha, author, author_login, timestamp, files)`` tuples
        in the order git emitted them (newest first). The login is the
        local part of the author email, falling back to the author name.
        """
        commits: list[tuple[str, str, str, str, list[str]]] = []
        current: tuple[str, str, str, str, list[str]] | None = None

        for line in output.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4 and len(parts[0]) == 40 and all(c in _HEX_DIGITS for c in parts[0]):
                if current is not None:
                    commits.append(current)
                sha, author, email, ts = parts
                login = email.split("@", 1)[0].strip() or author.strip()
                current = (sha, author.strip(), login, _parse_timestamp(ts), [])
            elif current is not None and line.strip():
                current[4].append(line.strip())

        if current is not None:
            commits.append(current)
        return commits

    async def recommend_reviewers(self, files: list[str], max_reviewers: int = 2) -> list[Reviewer]:
        """Recommend the top reviewers for the given file paths.

        Score per author: ``(commit_count / max_commits) * 0.4 +
        recency_factor * 0.6`` where ``commit_count`` is the author's
        total commits on the requested files, ``max_commits`` is the
        largest such total among candidates, and ``recency_factor``
        decays the author's most recent relevant commit with a
        90-day half-life.
        """
        conn = self._require_conn()
        if not files or max_reviewers <= 0:
            return []

        placeholders = ", ".join("?" for _ in files)
        cursor = await conn.execute(
            f"SELECT author_login, file_path, commit_count, last_commit_ts "
            f"FROM file_expertise WHERE file_path IN ({placeholders})",
            files,
        )
        rows = await cursor.fetchall()
        if not rows:
            return []

        totals: dict[str, int] = {}
        latest: dict[str, datetime] = {}
        file_sets: dict[str, set[str]] = {}
        for login, file_path, commit_count, ts_raw in rows:
            totals[login] = totals.get(login, 0) + commit_count
            file_sets.setdefault(login, set()).add(file_path)
            ts = _to_datetime(ts_raw)
            if ts is not None and (login not in latest or ts > latest[login]):
                latest[login] = ts

        max_commits = max(totals.values())
        now = datetime.now(UTC)

        candidates: list[Reviewer] = []
        for login, total in totals.items():
            frequency_factor = total / max_commits if max_commits > 0 else 0.0
            latest_ts = latest.get(login)
            if latest_ts is not None:
                age_days = max(0.0, (now - latest_ts).total_seconds() / _SECONDS_PER_DAY)
                recency_factor = math.exp(-_LN2 * age_days / RECENCY_HALF_LIFE_DAYS)
            else:
                recency_factor = 0.0
            score = min(1.0, max(0.0, frequency_factor * 0.4 + recency_factor * 0.6))
            candidates.append(
                Reviewer(
                    login=login,
                    name=await self._author_name(login),
                    files_touched=len(file_sets[login]),
                    expertise_score=round(score, 4),
                )
            )

        candidates.sort(key=lambda r: r.expertise_score, reverse=True)
        return candidates[:max_reviewers]

    async def _author_name(self, login: str) -> str:
        """Best-known display name for a login (from their latest commit)."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT author FROM commits WHERE author_login = ? ORDER BY timestamp DESC LIMIT 1",
            (login,),
        )
        row = await cursor.fetchone()
        return row[0] if row else login

    async def close(self) -> None:
        """Close the database connection (idempotent)."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
