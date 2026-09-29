"""Unit/integration tests for src.db.database (SQLite PR history)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.db.database import Database


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(str(tmp_path / "pr_sentinel.db"))
    await database.connect()
    yield database
    await database.close()


async def test_connect_and_close(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "pr_sentinel.db"))
    await database.connect()
    await database.close()
    assert database._conn is None


async def test_record_and_retrieve(db: Database) -> None:
    for n in (1, 2, 3):
        await db.record_pr(
            n,
            f"PR {n}",
            f"https://github.com/owner/repo/pull/{n}",
            "LOW",
            f"2026-09-{n:02d}T00:00:00+00:00",
        )

    rows = await db.get_pr_history(50)

    assert len(rows) == 3
    assert [row["pr_number"] for row in rows] == [3, 2, 1]


async def test_record_pr_fields(db: Database) -> None:
    await db.record_pr(
        7,
        "Add webhook retry",
        "https://github.com/owner/repo/pull/7",
        "HIGH",
        "2026-09-28T12:00:00+00:00",
    )

    rows = await db.get_pr_history(50)

    assert len(rows) == 1
    row = rows[0]
    assert row["pr_number"] == 7
    assert row["title"] == "Add webhook retry"
    assert row["url"] == "https://github.com/owner/repo/pull/7"
    assert row["risk_level"] == "HIGH"
    assert row["timestamp"] == "2026-09-28T12:00:00+00:00"


async def test_get_pr_history_limit(db: Database) -> None:
    for n in range(1, 6):
        await db.record_pr(
            n,
            f"PR {n}",
            f"https://github.com/owner/repo/pull/{n}",
            "MED",
            f"2026-09-{n:02d}T00:00:00+00:00",
        )

    rows = await db.get_pr_history(2)

    assert len(rows) == 2
    assert [row["pr_number"] for row in rows] == [5, 4]


async def test_uses_before_connect(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "pr_sentinel.db"))

    with pytest.raises(RuntimeError, match="not connected"):
        await database.record_pr(
            1, "t", "u", "LOW", "2026-09-28T00:00:00+00:00"
        )
    with pytest.raises(RuntimeError, match="not connected"):
        await database.get_pr_history(5)
