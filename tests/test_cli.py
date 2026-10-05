"""Unit tests for pr_sentinel.cli (index-repo subcommand)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

from pr_sentinel.cli import main as cli_main

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


def _rev(repo: Path) -> str:
    return (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
    )


def _make_repo(repo: Path) -> list[tuple[str, str]]:
    """Create a temp repo: alice commits a.py 2x (second also adds b.py),
    bob commits b.py 1x. Returns ``(sha, login)`` per commit, oldest first."""
    _git(repo, "init", "-q")

    (repo / "a.py").write_text("a1\n")
    _git(repo, "add", "a.py", env=_dated(_ALICE, "2026-01-01T10:00:00+00:00"))
    _git(repo, "commit", "-q", "-m", "alice: a1", env=_dated(_ALICE, "2026-01-01T10:00:00+00:00"))
    first = (_rev(repo), "alice")

    (repo / "a.py").write_text("a2\n")
    (repo / "b.py").write_text("b1\n")
    _git(repo, "add", "a.py", "b.py", env=_dated(_ALICE, "2026-02-01T10:00:00+00:00"))
    _git(
        repo, "commit", "-q", "-m", "alice: a2, b1", env=_dated(_ALICE, "2026-02-01T10:00:00+00:00")
    )
    second = (_rev(repo), "alice")

    (repo / "b.py").write_text("b2\n")
    _git(repo, "add", "b.py", env=_dated(_BOB, "2026-03-01T10:00:00+00:00"))
    _git(repo, "commit", "-q", "-m", "bob: b2", env=_dated(_BOB, "2026-03-01T10:00:00+00:00"))
    third = (_rev(repo), "bob")

    return [first, second, third]


def _read_rows(db: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str, int]]]:
    with sqlite3.connect(db) as conn:
        commits = conn.execute("SELECT sha, author_login FROM commits").fetchall()
        expertise = conn.execute(
            "SELECT author_login, file_path, commit_count FROM file_expertise "
            "ORDER BY author_login, file_path"
        ).fetchall()
    return commits, expertise


def _clear_graph_env(monkeypatch) -> None:
    monkeypatch.delenv("REPO_PATH", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)


def test_index_repo_populates_graph(tmp_path: Path, monkeypatch, capsys) -> None:
    _clear_graph_env(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    expected = _make_repo(repo)
    db = tmp_path / "graph.db"

    exit_code = cli_main(["index-repo", "--repo-path", str(repo), "--db-path", str(db)])

    assert exit_code == 0
    summary = capsys.readouterr().out
    assert "Indexed 3 commits by 2 distinct authors" in summary
    assert "3 file_expertise rows" in summary
    assert str(db) in summary

    commits, expertise = _read_rows(db)
    assert sorted(commits) == sorted(expected)
    assert {sha for sha, _ in commits} == {sha for sha, _ in expected}
    assert expertise == [
        ("alice", "a.py", 2),
        ("alice", "b.py", 1),
        ("bob", "b.py", 1),
    ]


def test_index_repo_uses_env_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    monkeypatch.setenv("REPO_PATH", str(repo))
    db = tmp_path / "env-default.db"

    exit_code = cli_main(["index-repo", "--db-path", str(db)])

    assert exit_code == 0
    commits, expertise = _read_rows(db)
    assert len(commits) == 3
    assert len(expertise) == 3


def test_index_repo_non_git_dir(tmp_path: Path, monkeypatch, capsys) -> None:
    _clear_graph_env(monkeypatch)
    plain = tmp_path / "plain"
    plain.mkdir()

    exit_code = cli_main(
        ["index-repo", "--repo-path", str(plain), "--db-path", str(tmp_path / "g.db")]
    )

    assert exit_code != 0
    assert "not a git repository" in capsys.readouterr().err


def test_index_repo_missing_path(tmp_path: Path, monkeypatch, capsys) -> None:
    _clear_graph_env(monkeypatch)
    missing = tmp_path / "does-not-exist"

    exit_code = cli_main(
        ["index-repo", "--repo-path", str(missing), "--db-path", str(tmp_path / "g.db")]
    )

    assert exit_code != 0
    assert "does not exist" in capsys.readouterr().err


def test_index_repo_git_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    _clear_graph_env(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    exit_code = cli_main(
        ["index-repo", "--repo-path", str(repo), "--db-path", str(tmp_path / "g.db")]
    )

    assert exit_code != 0
    assert "git executable not found" in capsys.readouterr().err
