"""FastAPI webhook server for GitHub ``pull_request`` events.

The module exposes a ready-to-run ``app`` (``uvicorn src.webhook.server:app``)
built by :func:`create_app`. Services are injected via :class:`WebhookDeps`
and stored on ``app.state``; tests may replace ``app.state.deps`` with fakes
and drive the endpoints through ``httpx.ASGITransport`` without starting a
server or constructing any real services.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request

from src.analyzer.risk import analyze_pr
from src.bot.bot import PRBot
from src.config import Settings, get_settings
from src.db.database import Database
from src.graph.expertise import ExpertiseGraph
from src.notifications.composer import NotificationComposer

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
HTTP_TIMEOUT = 30.0
PROCESSED_ACTIONS = frozenset({"opened", "synchronize"})


@dataclass(frozen=True)
class WebhookDeps:
    """Service bundle injected into the webhook endpoints."""

    settings: Settings
    bot: PRBot
    graph: ExpertiseGraph
    composer: NotificationComposer
    github: httpx.AsyncClient
    db: Database


def _verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """Verify an ``X-Hub-Signature-256`` header (``sha256=<hex digest>``)."""
    if signature is None or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


async def _fetch_pr_diff_and_files(
    client: httpx.AsyncClient, repo: str, pr_number: int
) -> tuple[str, list[str]]:
    """Fetch the PR unified diff (text) and the list of changed file paths."""
    diff_resp = await client.get(
        f"/repos/{repo}/pulls/{pr_number}",
        headers={"Accept": "application/vnd.github.v3.diff"},
    )
    diff_resp.raise_for_status()
    files_resp = await client.get(f"/repos/{repo}/pulls/{pr_number}/files")
    files_resp.raise_for_status()
    raw: object = files_resp.json()
    items = raw if isinstance(raw, list) else []
    files: list[str] = [
        entry["filename"]
        for entry in items
        if isinstance(entry, dict) and isinstance(entry.get("filename"), str)
    ]
    return diff_resp.text, files


def create_github_client(token: str) -> httpx.AsyncClient:
    """Create an authenticated async client for the GitHub REST API."""
    return httpx.AsyncClient(
        base_url=GITHUB_API_BASE,
        timeout=HTTP_TIMEOUT,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        },
    )


async def build_default_deps(settings: Settings) -> WebhookDeps:
    """Construct the full service bundle from settings.

    Used when the app runs standalone (``uvicorn src.webhook.server:app``)
    and no pre-built deps were injected.
    """
    db = Database(settings.database_path)
    await db.connect()
    graph = await ExpertiseGraph.create(settings.database_path)
    return WebhookDeps(
        settings=settings,
        bot=PRBot(settings.telegram_bot_token),
        graph=graph,
        composer=NotificationComposer(),
        github=create_github_client(settings.github_token),
        db=db,
    )


async def _close_bundle(deps: WebhookDeps) -> None:
    """Release all resources held by a service bundle (each step idempotent)."""
    await deps.github.aclose()
    await deps.graph.close()
    await deps.db.close()
    await deps.bot.bot.session.close()


async def get_deps(request: Request) -> WebhookDeps:
    """FastAPI dependency: resolve the service bundle for this app instance.

    Prefers ``app.state.deps`` (set by :func:`create_app` or by tests);
    otherwise builds and caches the default bundle on first use.
    """
    state = request.app.state
    deps: WebhookDeps | None = getattr(state, "deps", None)
    if deps is None:
        deps = await build_default_deps(get_settings())
        state.deps = deps
    return deps


def create_app(deps: WebhookDeps | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        deps: Pre-built service bundle (dependency injection for tests and
            for :mod:`src.main`, which owns the bot lifecycle). When omitted,
            the bundle is built from environment settings at startup.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if deps is None:
            app.state.deps = await build_default_deps(get_settings())
            app.state.deps_owned = True
        else:
            app.state.deps = deps
        yield
        if getattr(app.state, "deps_owned", False):
            await _close_bundle(app.state.deps)

    app = FastAPI(title="PR Sentinel", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "healthy"}

    @app.post("/webhook/github")
    async def github_webhook(
        request: Request, deps: Annotated[WebhookDeps, Depends(get_deps)]
    ) -> dict[str, str]:
        """Handle a GitHub ``pull_request`` webhook event."""
        body = await request.body()

        if request.headers.get("X-GitHub-Event") != "pull_request":
            raise HTTPException(
                status_code=400, detail="expected X-GitHub-Event: pull_request"
            )
        if not _verify_signature(
            body, request.headers.get("X-Hub-Signature-256"), deps.settings.github_token
        ):
            raise HTTPException(status_code=401, detail="invalid X-Hub-Signature-256")

        try:
            payload: Any = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="payload is not valid JSON") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="payload must be a JSON object")

        action = payload.get("action")
        if action not in PROCESSED_ACTIONS:
            raise HTTPException(status_code=400, detail=f"action {action!r} is not processed")

        pr = payload.get("pull_request")
        if not isinstance(pr, dict):
            raise HTTPException(status_code=400, detail="missing pull_request object")
        pr_number = pr.get("number")
        title = pr.get("title")
        url = pr.get("html_url")
        if (
            not isinstance(pr_number, int)
            or isinstance(pr_number, bool)
            or not isinstance(title, str)
            or not isinstance(url, str)
        ):
            raise HTTPException(
                status_code=400, detail="pull_request is missing number/title/html_url"
            )

        try:
            diff, files = await _fetch_pr_diff_and_files(
                deps.github, deps.settings.github_repo, pr_number
            )
        except httpx.HTTPError as exc:
            logger.error("GitHub API request failed for PR %s: %s", pr_number, exc)
            raise HTTPException(status_code=502, detail="failed to fetch PR from GitHub") from exc

        risk = await analyze_pr(
            diff=diff,
            pr_title=title,
            pr_body=pr.get("body") or "",
            model=deps.settings.openai_model,
        )
        reviewers = await deps.graph.recommend_reviewers(files)
        message = deps.composer.compose(pr_title=title, pr_url=url, risk=risk, reviewers=reviewers)
        await deps.bot.send_notification(deps.settings.telegram_chat_id, message)
        await deps.db.record_pr(
            pr_number, title, url, risk.level, datetime.now(UTC).isoformat()
        )
        logger.info("notified about PR %s (risk=%s)", pr_number, risk.level)
        return {"status": "ok"}

    return app


app = create_app()
