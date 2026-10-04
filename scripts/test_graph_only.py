"""Standalone test for the expertise graph, independent of the webhook server.

Creates an :class:`ExpertiseGraph` backed by a temporary SQLite database,
builds it from the sample ``test-repo/`` checkout, prints the recommended
reviewers for a couple of files, and closes the graph.

Run from the project root::

    python scripts/test_graph_only.py

Exits 0 on success, 1 on any failure.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# The package lives in src/. Ensure it is importable when run as
# ``python scripts/test_graph_only.py``.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = str(Path(PROJECT_ROOT) / "src")
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

REPO_PATH = PROJECT_ROOT / "test-repo"

TARGET_FILES = ["src/main.py", "src/utils.py"]


async def amain() -> int:
    from pr_sentinel.graph.expertise import ExpertiseGraph

    print("[1/4] Creating ExpertiseGraph with a temporary database...")
    tmp = tempfile.NamedTemporaryFile(prefix="expertise_", suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    print(f"      temp db: {db_path}")

    graph = await ExpertiseGraph.create(db_path)
    try:
        print(f"[2/4] Building graph from {REPO_PATH}...")
        await graph.build_from_repo(str(REPO_PATH))

        print(f"[3/4] Recommending reviewers for {TARGET_FILES}...")
        reviewers = await graph.recommend_reviewers(TARGET_FILES)
        if reviewers:
            for i, r in enumerate(reviewers, 1):
                print(
                    f"      {i}. {r.login} ({r.name}) "
                    f"files_touched={r.files_touched} score={r.expertise_score}"
                )
        else:
            print("      (no reviewers found for the target files)")
    finally:
        print("[4/4] Closing the graph...")
        await graph.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass
    return 0


def main() -> int:
    if not REPO_PATH.is_dir():
        print(f"FAILED: test repo not found at {REPO_PATH}")
        return 1
    try:
        return asyncio.run(amain())
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
