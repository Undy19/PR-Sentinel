"""Unit tests for src.graph.expertise (git-history expertise graph)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from src.graph.expertise import ExpertiseGraph

_ALICE = {
    "GIT_AUTHOR_NAME": "Alice",
    "GIT_AUTHOR_EMAIL": "alice@example.com",
    "GIT_COMMITTER_NAME": "Alice",
    "GIT_COMMITTER_EMAIL": "alice@example.com",
}
_BOB = {
    "GIT_AUTHOR_NAME": "Bob",
    "GIT_AUTHOR_EMAIL": "bob@example.com",
    "GIT_COMMITTER_NAME": "Bob",
    "GIT_COMMITTER_EMAIL": "bob@example.com",
}


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    full_env = os.environ | (env or {})
    subprocess.run(
        ["git", *args],
        cwd=repo,
        env=full_env,
        check=True,
        capture_output=True,
    )


def _dated(author: dict[str, str], date: str) -> dict[str, str]:
    return {
        **author,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
    }


def _make_repo(repo: Path) -> None:
    """Create a temp repo where alice commits to a.py 3x (last one recent)
    and bob commits 1x (old). Alice should therefore score highest."""
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("v1\n")
    _git(repo, "add", "a.py", env=_dated(_BOB, "2026-01-01T10:00:00+00:00"))
    _git(repo, "commit", "-q", "-m", "bob: initial", env=_dated(_BOB, "2026-01-01T10:00:00+00:00"))

    for version, date in (
        ("v2\n", "2026-02-01T10:00:00+00:00"),
        ("v3\n", "2026-03-01T10:00:00+00:00"),
        ("v4\n", "2026-08-01T10:00:00+00:00"),
    ):
        (repo / "a.py").write_text(version)
        _git(repo, "add", "a.py", env=_dated(_ALICE, date))
        _git(repo, "commit", "-q", "-m", f"alice: {version.strip()}", env=_dated(_ALICE, date))


async def test_create_and_close(tmp_path: Path) -> None:
    graph = await ExpertiseGraph.create(str(tmp_path / "expertise.db"))
    assert graph._conn is not None
    await graph.close()
    assert graph._conn is None


async def test_recommend_no_data(tmp_path: Path) -> None:
    graph = await ExpertiseGraph.create(str(tmp_path / "expertise.db"))
    try:
        assert await graph.recommend_reviewers(["file.py"]) == []
    finally:
        await graph.close()


async def test_build_from_repo_and_recommend(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)

    graph = await ExpertiseGraph.create(str(tmp_path / "expertise.db"))
    try:
        await graph.build_from_repo(str(repo))
        reviewers = await graph.recommend_reviewers(["a.py"])

        assert len(reviewers) == 2
        # More commits + more recent activity => higher score.
        assert [r.login for r in reviewers] == ["alice", "bob"]
        assert reviewers[0].expertise_score > reviewers[1].expertise_score
        assert reviewers[0].name == "Alice"
        assert reviewers[0].files_touched == 1
    finally:
        await graph.close()


async def test_recommend_max_reviewers(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)

    graph = await ExpertiseGraph.create(str(tmp_path / "expertise.db"))
    try:
        await graph.build_from_repo(str(repo))
        reviewers = await graph.recommend_reviewers(["a.py"], max_reviewers=1)

        assert len(reviewers) == 1
        assert reviewers[0].login == "alice"
    finally:
        await graph.close()
