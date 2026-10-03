"""FastAPI webhook server for GitHub ``pull_request`` events.

The module exposes a ready-to-run ``app`` (``uvicorn src.webhook.server:app``)
built by :func:`create_app`. Services are injected via :class:`WebhookDeps`
and stored on ``app.state``. Accepted webhooks are placed on a bounded
``app.state.queue`` consumed by a single background worker task
(``app.state.worker``). Tests may replace ``app.state.deps`` with fakes and
drive the endpoints through ``httpx.ASGITransport`` without starting a
server or constructing any real services.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

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
MAX_BODY_BYTES = 5 * 1024 * 1024
QUEUE_MAX_SIZE = 100
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


@dataclass(frozen=True)
class WorkItem:
    """A single accepted webhook, processed by the background worker."""

    pr_number: int
    title: str
    url: str
    body: str
    language: str
    model: str
    api_key: str | None
    base_url: str | None


def _verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """Verify an ``X-Hub-Signature-256`` header (``sha256=<hex digest>``)."""
    if signature is None or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


def _webhook_secret(settings: Settings) -> str:
    """Return the HMAC key used to verify ``X-Hub-Signature-256``.

    Prefers the dedicated :attr:`Settings.github_webhook_secret`. When it is
    unset, falls back to the GitHub API token so deployments that predate the
    dedicated secret keep working (SEC-02 recommends setting a separate one).
    """
    return settings.github_webhook_secret or settings.github_token


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


async def _process_work_item(deps: WebhookDeps, item: WorkItem) -> None:
    """Run the full analysis/notification pipeline for one accepted item."""
    diff, files = await _fetch_pr_diff_and_files(
        deps.github, deps.settings.github_repo, item.pr_number
    )
    risk = await analyze_pr(
        diff=diff,
        pr_title=item.title,
        pr_body=item.body,
        language=item.language,
        model=item.model,
        api_key=item.api_key,
        base_url=item.base_url,
    )
    reviewers = await deps.graph.recommend_reviewers(files)
    message = deps.composer.compose(
        pr_title=item.title,
        pr_url=item.url,
        risk=risk,
        reviewers=reviewers,
        language=item.language,
    )
    await deps.bot.send_notification(deps.settings.telegram_chat_id, message)
    await deps.db.record_pr(
        item.pr_number, item.title, item.url, risk.level, datetime.now(UTC).isoformat()
    )
    logger.info("notified about PR %s (risk=%s)", item.pr_number, risk.level)


async def _queue_worker(deps: WebhookDeps, queue: asyncio.Queue[WorkItem]) -> None:
    """Consume the work queue sequentially until the task is cancelled."""
    while True:
        item = await queue.get()
        try:
            await _process_work_item(deps, item)
        except Exception:
            logger.exception("failed to process PR %s", item.pr_number)
        finally:
            queue.task_done()


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
        if app.state.deps.settings.github_webhook_secret is None:
            logger.warning(
                "GITHUB_WEBHOOK_SECRET is not set; verifying X-Hub-Signature-256 "
                "against GITHUB_TOKEN (SEC-02 recommends a dedicated secret)"
            )
        app.state.queue = asyncio.Queue[WorkItem](maxsize=QUEUE_MAX_SIZE)
        app.state.worker = asyncio.create_task(_queue_worker(app.state.deps, app.state.queue))
        yield
        worker: asyncio.Task[None] = app.state.worker
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        if getattr(app.state, "deps_owned", False):
            await _close_bundle(app.state.deps)

    app = FastAPI(title="PR Sentinel", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "healthy"}

    @app.post("/webhook/github", status_code=202, response_model=None)
    async def github_webhook(
        request: Request, deps: Annotated[WebhookDeps, Depends(get_deps)]
    ) -> dict[str, str] | JSONResponse:
        """Handle a GitHub ``pull_request`` webhook event."""
        # Reject oversized bodies before reading them (SEC-03).
        content_length = request.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="request body too large")
            except ValueError:
                pass  # unparsable Content-Length: fall through and read as before

        body = await request.body()

        if request.headers.get("X-GitHub-Event") != "pull_request":
            raise HTTPException(status_code=400, detail="expected X-GitHub-Event: pull_request")
        if not _verify_signature(
            body,
            request.headers.get("X-Hub-Signature-256"),
            _webhook_secret(deps.settings),
        ):
            raise HTTPException(status_code=401, detail="invalid X-Hub-Signature-256")

        # Replay protection: skip deliveries we have already seen (SEC-09).
        delivery_id = request.headers.get("X-GitHub-Delivery")
        if delivery_id:
            if await deps.db.is_delivery_seen(delivery_id):
                logger.info("skipping duplicate delivery %s", delivery_id)
                return JSONResponse({"status": "duplicate"}, status_code=200)
            await deps.db.mark_delivery_seen(delivery_id)

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

        item = WorkItem(
            pr_number=pr_number,
            title=title,
            url=url,
            body=pr.get("body") or "",
            language=deps.settings.notification_language,
            model=deps.settings.openai_model,
            api_key=deps.settings.openai_api_key,
            base_url=deps.settings.openai_base_url,
        )
        queue: asyncio.Queue[WorkItem] = request.app.state.queue
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            raise HTTPException(status_code=503, detail="busy, try again later") from None
        logger.info("accepted PR %s for background analysis", pr_number)
        return {"status": "accepted"}

    return app


app = create_app()
