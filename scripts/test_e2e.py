"""End-to-end test for the PR Sentinel pipeline against a live in-process server.

Starts the FastAPI webhook server in-process on ``127.0.0.1:8000`` (via
uvicorn), posts a simulated GitHub ``pull_request`` webhook, and reports the
result. Configuration is read from environment variables, exactly like
:mod:`pr_sentinel.config`.

Run from the project root::

    python scripts/test_e2e.py

Exits 0 on success, 1 on any failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
from pathlib import Path

import httpx
import uvicorn

# The package lives in src/. Ensure it is importable when run as
# ``python scripts/test_e2e.py``.
_SRC_ROOT = str(Path(__file__).resolve().parent.parent / "src")
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from pr_sentinel.config import get_settings
from pr_sentinel.webhook.server import _close_bundle, build_default_deps, create_app

HOST = "127.0.0.1"
PORT = 8000
WEBHOOK_URL = f"http://{HOST}:{PORT}/webhook/github"

# Simulated GitHub ``pull_request`` webhook payload.
PAYLOAD: dict = {
    "action": "opened",
    "pull_request": {
        "number": 1,
        "title": "Test PR: Add feature",
        "html_url": "https://github.com/test/repo/pull/1",
        "body": "Adds a new feature",
        "diff_url": "https://github.com/test/repo/pull/1.diff",
    },
}


def _sign(body: bytes, secret: str) -> str:
    """Compute the ``X-Hub-Signature-256`` header value for *body*."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def amain() -> int:
    # Step 1: read config from environment variables (same as src/config.py).
    print("[1/8] Loading configuration from environment variables...")

    try:
        settings = get_settings()
    except Exception as exc:  # pydantic ValidationError / missing vars
        print(f"FAILED: could not load settings: {exc}")
        return 1

    print(f"      github_repo        = {settings.github_repo}")
    print(f"      telegram_chat_id   = {settings.telegram_chat_id}")
    print(f"      telegram_bot_token = {'<set>' if settings.telegram_bot_token else '<missing>'}")
    print(f"      github_token       = {'<set>' if settings.github_token else '<missing>'}")
    print(f"      openai_api_key     = {'<set>' if settings.openai_api_key else '<missing>'}")
    print(f"      openai_base_url    = {settings.openai_base_url or '<unset>'}")

    # Step 2: build the service bundle and the FastAPI app.
    print("[2/8] Building the FastAPI app and service bundle...")
    deps = await build_default_deps(settings)
    app = create_app(deps)

    # Step 3: start uvicorn in-process and wait for it to come up.
    print(f"[3/8] Starting uvicorn server on {HOST}:{PORT}...")
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    print("[4/8] Waiting 2 seconds for the server to start...")
    await asyncio.sleep(2)

    try:
        # Step 4: send the simulated webhook.
        print("[5/8] Sending simulated GitHub pull_request webhook...")
        body = json.dumps(PAYLOAD).encode("utf-8")
        headers = {
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body, settings.github_webhook_secret),
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(WEBHOOK_URL, content=body, headers=headers)

        # Step 5: report the response.
        print(f"      response status: {response.status_code}")
        print(f"      response body:   {response.text}")

        # Step 6: give the async pipeline time to finish.
        print("[6/8] Waiting 5 seconds for async processing...")
        await asyncio.sleep(5)

        # Step 7: done.
        print("[7/8] Done. Check your Telegram for the notification.")

        # Step 8: shut the server down cleanly.
        print("[8/8] Shutting down the server...")
        server.should_exit = True
        await server_task
    finally:
        # Release the service bundle resources (idempotent).
        await _close_bundle(deps)

    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    _policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if _policy is not None:
        asyncio.set_event_loop_policy(_policy())
    sys.exit(main())
