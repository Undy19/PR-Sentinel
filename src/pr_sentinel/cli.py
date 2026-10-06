"""Command-line interface for PR Sentinel.

Provides the ``index-repo`` subcommand, which rebuilds the SQLite
expertise graph (``commits`` + ``file_expertise``) from a repository's
git history::

    python -m pr_sentinel.cli index-repo [--repo-path PATH] [--db-path PATH]

Only ``REPO_PATH`` and ``DATABASE_PATH`` are read from the environment;
no other settings (bot tokens, etc.) are required.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

import aiosqlite

from pr_sentinel.graph.expertise import ExpertiseGraph

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class CliError(RuntimeError):
    """User-facing CLI failure; ``str(exc)`` is printed to stderr."""


def _env_or(env_var: str, fallback: str) -> str:
    """Return *env_var* from the environment, or *fallback* if unset/empty."""
    return os.environ.get(env_var) or fallback


def _require_git() -> None:
    """Fail early if the git executable is not on PATH."""
    if shutil.which("git") is None:
        raise CliError("git executable not found; is git installed?")


def _validate_repo(repo: Path) -> None:
    """Fail early with a clear message on an unusable repo path."""
    if not repo.exists():
        raise CliError(f"repo path does not exist: {repo}")
    if not (repo / ".git").exists():
        raise CliError(f"not a git repository: {repo}")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``pr-sentinel`` CLI."""
    parser = argparse.ArgumentParser(
        prog="pr-sentinel",
        description="PR Sentinel: score PR risk and recommend reviewers.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser(
        "index-repo",
        help="Rebuild the expertise graph from a repository's git history",
        description=(
            "Rebuild the `commits` and `file_expertise` tables in the "
            "SQLite expertise graph from `git log --name-only`."
        ),
    )
    index_parser.add_argument(
        "--repo-path",
        default=None,
        metavar="PATH",
        help="path to the git repository (default: $REPO_PATH, else '.')",
    )
    index_parser.add_argument(
        "--db-path",
        default=None,
        metavar="PATH",
        help="path to the SQLite database (default: $DATABASE_PATH, else 'pr_sentinel.db')",
    )
    return parser


async def _index_repo(repo: str, db_path: str) -> tuple[int, int, int]:
    """Build the graph, then return ``(commits, distinct authors, file_expertise rows)``."""
    graph = await ExpertiseGraph.create(db_path)
    try:
        await graph.build_from_repo(repo)
    finally:
        await graph.close()

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*), COUNT(DISTINCT author_login) FROM commits")
        row = await cursor.fetchone()
        commits = int(row[0]) if row else 0
        authors = int(row[1]) if row else 0
        cursor = await conn.execute("SELECT COUNT(*) FROM file_expertise")
        row = await cursor.fetchone()
        expertise_rows = int(row[0]) if row else 0
    return commits, authors, expertise_rows


def _run_index(args: argparse.Namespace) -> int:
    """Execute the ``index-repo`` subcommand; returns the process exit code."""
    repo = Path(args.repo_path) if args.repo_path is not None else Path(_env_or("REPO_PATH", "."))
    db_path = (
        Path(args.db_path)
        if args.db_path is not None
        else Path(_env_or("DATABASE_PATH", "pr_sentinel.db"))
    )

    _require_git()
    _validate_repo(repo)

    try:
        commits, authors, expertise_rows = asyncio.run(_index_repo(str(repo), str(db_path)))
    except (OSError, aiosqlite.Error, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Indexed {commits} commits by {authors} distinct authors; "
        f"{expertise_rows} file_expertise rows in {db_path}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "index-repo":
            return _run_index(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    _policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if _policy is not None:
        asyncio.set_event_loop_policy(_policy())
    sys.exit(main())
