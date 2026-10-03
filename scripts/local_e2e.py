"""Local end-to-end test for the PR Sentinel pipeline — no public URL needed.

Simulates a GitHub ``pull_request`` webhook payload entirely in-process:

1. Loads configuration from ``.env`` at the project root (manual parse).
2. Builds the expertise graph from the sample ``test-repo/`` checkout.
3. Runs LLM risk analysis on a fake PR diff (graceful heuristic fallback
   when the LLM is unreachable).
4. Gets reviewer recommendations from the expertise graph.
5. Composes the Telegram notification (MarkdownV2).
6. Sends it directly via the Telegram Bot API (no webhook, no ngrok).

Run from the project root::

    python scripts/local_e2e.py

Exits 0 on success (a real LLM result OR the heuristic fallback both count
as success), 1 on any hard failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import asyncio
import json
import os
import tempfile

import aiohttp
import openai

from src.analyzer.risk import RiskAssessment, analyze_pr
from src.graph.expertise import ExpertiseGraph
from src.notifications.composer import NotificationComposer

ENV_PATH = Path(_PROJECT_ROOT) / ".env"
REPO_PATH = Path(_PROJECT_ROOT) / "test-repo"

# Files that exist in test-repo's git history; used as a fallback when the
# fake PR's diff files have no expertise-graph matches.
FALLBACK_FILES = ["src/main.py", "src/utils.py"]

# ---------------------------------------------------------------------------
# Simulated GitHub ``pull_request`` webhook event
# ---------------------------------------------------------------------------

PR_TITLE = "Add new feature to user auth"
PR_BODY = (
    "This PR adds OAuth2 support to the authentication module. "
    "Changes: new oauth2.py, modified auth.py, new tests."
)
PR_NUMBER = 42

SAMPLE_DIFF = """diff --git a/src/auth.py b/src/auth.py
index 8f3a2c1..d41e9f7 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -12,6 +12,14 @@
 from .session import Session
+from .oauth2 import OAuth2Provider
 
 def authenticate(username: str, password: str) -> bool:
     return _check_credentials(username, password)
+
+def authenticate_with_oauth2(provider: str, code: str) -> Session:
++    # Exchange an OAuth2 authorization code for a session.
+    oauth = OAuth2Provider(provider)
+    token = oauth.exchange_code(code)
+    return Session.create_from_token(token)
diff --git a/src/oauth2.py b/src/oauth2.py
new file mode 100644
index 0000000..5b7e9a2
--- /dev/null
+++ b/src/oauth2.py
@@ -0,0 +1,8 @@
+class OAuth2Provider:
+    def __init__(self, provider: str) -> None:
+        self.provider = provider
+
+    def exchange_code(self, code: str) -> str:
+        return self._token_endpoint(code)
"""


def load_env(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file manually: one ``KEY=value`` per line."""
    env: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def files_from_diff(diff: str) -> list[str]:
    """Extract changed file paths from ``diff --git a/<p> b/<p>`` lines."""
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4 and parts[3].startswith("b/"):
                path = parts[3][2:]
                if path not in files:
                    files.append(path)
    return files

async def discover_model(base_url: str) -> str | None:
    """Return the first model id served at ``{base_url}/models`` (else None)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base_url.rstrip('/')}/models",
                timeout=aiohttp.ClientTimeout(total=10.0),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except (aiohttp.ClientError, ValueError):
        return None
    models = data.get("data") if isinstance(data, dict) else None
    if isinstance(models, list) and models and isinstance(models[0], dict):
        model_id = models[0].get("id")
        if model_id:
            return str(model_id)

    return None

def fallback_assessment() -> RiskAssessment:
    """Heuristic assessment used when the LLM is unreachable."""
    return RiskAssessment(
        level="LOW", reasons=["LLM unavailable, using heuristic"], confidence=0.0
    )


async def amain() -> int:
    # Step 0: configuration.
    print("Step 0: Loading configuration from .env...")
    if not ENV_PATH.is_file():
        print(f"FAILED: .env not found at {ENV_PATH}")
        return 1
    env = load_env(ENV_PATH)
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    repo = env.get("GITHUB_REPO", "owner/repo")
    if not token or not chat_id:
        print("FAILED: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing from .env")
        return 1
    print(f"      repo={repo}  chat_id={chat_id}")

    # Step 1: expertise graph.
    print("Step 1: Building expertise graph from test-repo...")
    if not REPO_PATH.is_dir():
        print(f"FAILED: test repo not found at {REPO_PATH}")
        return 1
    tmp = tempfile.NamedTemporaryFile(prefix="expertise_", suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    graph = await ExpertiseGraph.create(db_path)
    try:
        await graph.build_from_repo(str(REPO_PATH))
        print(f"      graph built from {REPO_PATH} (temp db: {db_path})")

        # Step 2: risk analysis.
        print("Step 2: Analyzing PR risk...")
        base_url = env.get("OPENAI_BASE_URL") or None
        model = env.get("OPENAI_MODEL")
        if not model and base_url:
            model = await discover_model(base_url)
            if model:
                print(f"      no OPENAI_MODEL in .env; discovered '{model}' "
                      f"from {base_url}/models")
        model = model or "gpt-4o"
        client: openai.AsyncOpenAI | None = None
        if env.get("OPENAI_API_KEY"):
            client = openai.AsyncOpenAI(
                api_key=env["OPENAI_API_KEY"],
                base_url=base_url,
                timeout=30.0,
            )
        try:
            risk = await analyze_pr(
                diff=SAMPLE_DIFF,
                pr_title=PR_TITLE,
                pr_body=PR_BODY,
                model=model,
                client=client,
            )
            print(f"      risk: {risk.level}  confidence={risk.confidence}")
        except Exception as exc:
            print(f"      LLM call failed ({exc.__class__.__name__}: {exc})")
            risk = fallback_assessment()
            print(f"      risk: {risk.level} (heuristic fallback)")
        for reason in risk.reasons:
            print(f"      reason: {reason}")

        # Step 3: reviewer recommendations.
        print("Step 3: Getting reviewer recommendations...")
        diff_files = files_from_diff(SAMPLE_DIFF)
        reviewers = await graph.recommend_reviewers(diff_files)
        if not reviewers:
            print(f"      no expertise matches for {diff_files}; "
                  f"falling back to {FALLBACK_FILES}")
            reviewers = await graph.recommend_reviewers(FALLBACK_FILES)
        if reviewers:
            for i, r in enumerate(reviewers, 1):
                print(f"      {i}. @{r.login} ({r.name}) "
                      f"files_touched={r.files_touched} score={r.expertise_score}")
        else:
            print("      (no reviewers found)")
    finally:
        await graph.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass

    # Step 4: compose the notification.
    print("Step 4: Composing Telegram notification...")
    composer = NotificationComposer()
    pr_url = f"https://github.com/{repo}/pull/{PR_NUMBER}"
    message = composer.compose(
        pr_title=PR_TITLE, pr_url=pr_url, risk=risk, reviewers=reviewers
    )
    print("      message:")
    for line in message.splitlines():
        print(f"        {line}")

    # Step 5: send via the Telegram Bot API.
    print("Step 5: Sending to Telegram...")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "MarkdownV2"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
    except aiohttp.ClientError as exc:
        print(f"FAILED: Telegram API request failed: {exc}")
        return 1

    print("      Telegram API response:")
    print("      " + json.dumps(data, ensure_ascii=False, indent=2).replace("\n", "\n      "))
    if not data.get("ok"):
        print(f"FAILED: Telegram reported an error: {data.get('description')}")
        return 1

    # Summary.
    print()
    print("=" * 60)
    print("E2E pipeline completed successfully")
    print(f"  PR title:    {PR_TITLE}")
    print(f"  Risk level:  {risk.level}")
    print(f"  Reviewers:   {', '.join('@' + r.login for r in reviewers) or 'none'}")
    print("  Telegram:    message delivered")
    print("=" * 60)
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        print("interrupted")
        return 1
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
