"""Integration test: expertise graph on the tracked ``test-repo`` fixture.

Charter acceptance: "Expertise graph builds correctly on a repository with
>=50 commits and >=3 authors". ``test-repo/`` (57 commits, 3 authors) is the
real git repository used as the fixture.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pr_sentinel.graph.expertise import ExpertiseGraph

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_REPO = _REPO_ROOT / "test-repo"

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not (_TEST_REPO / ".git").is_dir(), reason="test-repo fixture missing")
async def test_graph_on_real_repo_50_commits_3_authors(tmp_path: Path) -> None:
    graph = await ExpertiseGraph.create(str(tmp_path / "expertise.db"))
    try:
        await graph.build_from_repo(str(_TEST_REPO))

        conn = sqlite3.connect(tmp_path / "expertise.db")
        try:
            commits = conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
            expertise_rows = conn.execute("SELECT COUNT(*) FROM file_expertise").fetchone()[0]
            authors = conn.execute("SELECT COUNT(DISTINCT author_login) FROM commits").fetchone()[0]
        finally:
            conn.close()

        assert commits >= 50
        assert authors >= 3
        assert expertise_rows > 0

        reviewers = await graph.recommend_reviewers(["src/main.py", "src/utils.py", "config.py"])
        assert reviewers, "expected at least one recommended reviewer"
        assert len(reviewers) <= 2
        assert {r.login for r in reviewers} <= {"alice", "bob", "carol"}
        assert reviewers[0].expertise_score >= reviewers[-1].expertise_score
    finally:
        await graph.close()
