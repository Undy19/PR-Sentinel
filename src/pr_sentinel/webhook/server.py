"""FastAPI webhook server for GitHub ``pull_request`` events.

The module exposes a ready-to-run ``app`` (``uvicorn pr_sentinel.webhook.server:app``)
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
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from pr_sentinel.analyzer.risk import analyze_pr
from pr_sentinel.bot.bot import PRBot
from pr_sentinel.config import Settings, SettingsError, load_settings
from pr_sentinel.db.database import Database
from pr_sentinel.graph.expertise import ExpertiseGraph, Reviewer
from pr_sentinel.notifications.composer import NotificationComposer

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
    refresher: asyncio.Task[None] | None = None  # periodic expertise graph refresh


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
    enqueued_at: float = field(default_factory=time.monotonic)  # webhook receipt time for latency


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


async def _resolve_reviewer_logins(deps: WebhookDeps, reviewers: list[Reviewer]) -> None:
    """Replace email local-parts with real GitHub logins (cached API lookup).

    The graph derives ``Reviewer.login`` from the commit author email's
    local part, which is often not the author's GitHub username. Successful
    resolutions are cached in the ``login_map`` table; failures fall back
    to the local part for this PR only.
    """
    for reviewer in reviewers:
        cached = await deps.graph.cached_github_login(reviewer.login)
        if cached:
            reviewer.login = cached
            continue
        email = await deps.graph.author_email(reviewer.login)
        if not email:
            continue
        try:
            resp = await deps.github.get("/search/users", params={"q": f'"{email}" in:email'})
            resp.raise_for_status()
            raw: object = resp.json()
            items = raw.get("items") if isinstance(raw, dict) else None
        except (httpx.HTTPError, ValueError):
            logger.warning("GitHub login lookup failed for %s; keeping email local-part", email)
            continue
        if (
            isinstance(items, list)
            and items
            and isinstance(items[0], dict)
            and isinstance(items[0].get("login"), str)
        ):
            github_login = items[0]["login"]
            await deps.graph.cache_github_login(reviewer.login, github_login)
            reviewer.login = github_login


async def _process_work_item(deps: WebhookDeps, item: WorkItem) -> None:
    """Run the full analysis/notification pipeline for one accepted item."""
    started = time.monotonic()
    diff, files = await _fetch_pr_diff_and_files(
        deps.github, deps.settings.github_repo, item.pr_number
    )
    after_fetch = time.monotonic()
    risk = await analyze_pr(
        diff=diff,
        pr_title=item.title,
        pr_body=item.body,
        language=item.language,
        model=item.model,
        api_key=item.api_key,
        base_url=item.base_url,
    )
    after_analyze = time.monotonic()
    reviewers = await deps.graph.recommend_reviewers(files)
    await _resolve_reviewer_logins(deps, reviewers)
    after_recommend = time.monotonic()
    message = deps.composer.compose(
        pr_title=item.title,
        pr_url=item.url,
        risk=risk,
        reviewers=reviewers,
        language=item.language,
    )
    await deps.bot.send_notification(deps.settings.telegram_chat_id, message)
    after_send = time.monotonic()
    await deps.db.record_pr(
        item.pr_number, item.title, item.url, risk.level, datetime.now(UTC).isoformat()
    )
    logger.info("notified about PR %s (risk=%s)", item.pr_number, risk.level)
    total_ms = int((after_send - item.enqueued_at) * 1000)
    logger.info(
        "pr #%s latency: fetch=%dms analyze=%dms recommend=%dms send=%dms total=%dms",
        item.pr_number,
        int((after_fetch - started) * 1000),
        int((after_analyze - after_fetch) * 1000),
        int((after_recommend - after_analyze) * 1000),
        int((after_send - after_recommend) * 1000),
        total_ms,
    )
    budget = deps.settings.latency_budget_seconds
    if total_ms > budget * 1000:
        logger.warning(
            "pr #%s exceeded the latency budget: total=%dms > %ds",
            item.pr_number,
            total_ms,
            budget,
        )


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


async def _graph_refresher(graph: ExpertiseGraph, repo_path: str, interval: float) -> None:
    """Rebuild the expertise graph from ``repo_path`` every ``interval`` seconds."""
    while True:
        await asyncio.sleep(interval)
        try:
            await graph.build_from_repo(repo_path)
            logger.info("expertise graph refreshed from %s", repo_path)
        except Exception:
            logger.exception("expertise graph refresh from %s failed", repo_path)


def start_graph_refresher(
    graph: ExpertiseGraph, repo_path: str, interval_seconds: int
) -> asyncio.Task[None] | None:
    """Start the periodic graph refresh task; ``0`` disables it."""
    if interval_seconds <= 0:
        return None
    return asyncio.create_task(_graph_refresher(graph, repo_path, interval_seconds))


async def build_default_deps(settings: Settings) -> WebhookDeps:
    """Construct the full service bundle from settings.

    Used when the app runs standalone (``uvicorn pr_sentinel.webhook.server:app``)
    and no pre-built deps were injected. The expertise graph is rebuilt from
    ``settings.repo_path``; a failure there is logged and leaves the graph
    empty, so the webhook server still starts.
    """
    db = Database(settings.database_path)
    await db.connect()
    graph = await ExpertiseGraph.create(settings.database_path)
    try:
        await graph.build_from_repo(settings.repo_path)
        logger.info("expertise graph built from %s", settings.repo_path)
    except Exception:
        logger.exception(
            "failed to build expertise graph from %s; continuing without reviewer recommendations",
            settings.repo_path,
        )
    return WebhookDeps(
        settings=settings,
        bot=PRBot(settings.telegram_bot_token),
        graph=graph,
        composer=NotificationComposer(),
        github=create_github_client(settings.github_token),
        db=db,
        refresher=start_graph_refresher(
            graph, settings.repo_path, settings.graph_refresh_interval_seconds
        ),
    )


async def _close_bundle(deps: WebhookDeps) -> None:
    """Release all resources held by a service bundle (each step idempotent)."""
    if deps.refresher is not None:
        deps.refresher.cancel()
        with suppress(asyncio.CancelledError):
            await deps.refresher
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
        deps = await build_default_deps(load_settings())
        state.deps = deps
    return deps


def create_app(deps: WebhookDeps | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        deps: Pre-built service bundle (dependency injection for tests and
            for :mod:`pr_sentinel.main`, which owns the bot lifecycle). When omitted,
            the bundle is built from environment settings at startup.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if deps is None:
            try:
                app.state.deps = await build_default_deps(load_settings())
            except SettingsError as exc:
                logger.error("%s", exc)
                raise
            app.state.deps_owned = True
        else:
            app.state.deps = deps
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
            deps.settings.github_webhook_secret,
        ):
            raise HTTPException(status_code=401, detail="invalid X-Hub-Signature-256")

        # Replay protection: skip deliveries we have already seen (SEC-09).
        delivery_id = request.headers.get("X-GitHub-Delivery")
        if delivery_id and deps.settings.replay_protection_enabled:
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
