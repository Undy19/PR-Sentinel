"""Integration tests for the FastAPI GitHub webhook endpoint (ASGI transport)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from contextlib import suppress
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from src.analyzer.risk import RiskAssessment
from src.bot.bot import PRBot
from src.config import Settings
from src.db.database import Database
from src.graph.expertise import ExpertiseGraph
from src.notifications.composer import NotificationComposer
from src.webhook.server import (
    MAX_BODY_BYTES,
    WebhookDeps,
    WorkItem,
    _queue_worker,
    create_app,
)

GITHUB_TOKEN = "test-github-token"
WEBHOOK_SECRET = "test-webhook-secret"
CHAT_ID = 42

PAYLOAD: dict[str, Any] = {
    "action": "opened",
    "pull_request": {
        "number": 42,
        "title": "Refactor settings loader",
        "html_url": "https://github.com/owner/repo/pull/42",
        "body": "Introduce lazy loading for settings",
    },
}

Env = tuple[httpx.AsyncClient, WebhookDeps, asyncio.Queue[WorkItem]]


def _make_github_mock() -> AsyncMock:
    """AsyncMock standing in for the GitHub REST API client.

    ``client.get(url)``: a URL containing ``/files`` returns the changed-file
    list; any other URL (the diff request, whose format is requested via the
    Accept header) returns the diff text.
    """

    def fake_get(url: str, *args: Any, **kwargs: Any) -> Mock:
        response = Mock()
        response.status_code = 200
        if "/files" in url:
            response.json.return_value = [
                {"filename": "src/main.py"},
                {"filename": "src/config.py"},
            ]
        else:
            response.text = "diff content here"
        return response

    client: AsyncMock = AsyncMock()
    client.get = AsyncMock(side_effect=fake_get)
    return client


def _sign(body: bytes) -> str:
    """Compute the ``X-Hub-Signature-256`` header value for *body*."""
    return "sha256=" + hmac.new(WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest.fixture
async def webhook_env(tmp_path: Path) -> Env:
    settings = Settings.model_validate(
        {
            "telegram_bot_token": "123:TEST",
            "github_token": GITHUB_TOKEN,
            "github_webhook_secret": WEBHOOK_SECRET,
            "openai_api_key": "test-openai-key",
            "github_repo": "owner/repo",
            "telegram_chat_id": CHAT_ID,
            "database_path": str(tmp_path / "pr_sentinel.db"),
            "replay_protection_enabled": True,
        }
    )
    bot = PRBot("123:TEST")
    bot.send_notification = AsyncMock()
    graph = await ExpertiseGraph.create(str(tmp_path / "expertise.db"))
    composer = NotificationComposer()
    github = _make_github_mock()
    db = Database(str(tmp_path / "pr_sentinel.db"))
    await db.connect()

    deps = WebhookDeps(
        settings=settings,
        bot=bot,
        graph=graph,
        composer=composer,
        github=github,
        db=db,
    )
    app = create_app(deps=deps)
    # ASGITransport does not run the app lifespan, so expose the bundle, the
    # work queue, and the background worker the same way ``create_app``'s
    # lifespan would.
    app.state.deps = deps
    queue: asyncio.Queue[WorkItem] = asyncio.Queue(maxsize=100)
    app.state.queue = queue
    app.state.worker = asyncio.create_task(_queue_worker(deps, queue))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, deps, queue

    app.state.worker.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.worker
    await graph.close()
    await db.close()


@pytest.fixture
async def webhook_env_fallback(tmp_path: Path) -> Env:
    """Bundle with ``GITHUB_WEBHOOK_SECRET`` unset.

    The webhook endpoint must then verify against ``GITHUB_TOKEN`` (SEC-02
    fallback) so pre-SEC-02 deployments keep working.
    """
    settings = Settings.model_validate(
        {
            "telegram_bot_token": "123:TEST",
            "github_token": GITHUB_TOKEN,
            "openai_api_key": "test-openai-key",
            "github_repo": "owner/repo",
            "telegram_chat_id": CHAT_ID,
            "database_path": str(tmp_path / "pr_sentinel.db"),
        }
    )
    bot = PRBot("123:TEST")
    bot.send_notification = AsyncMock()
    graph = await ExpertiseGraph.create(str(tmp_path / "expertise.db"))
    composer = NotificationComposer()
    github = _make_github_mock()
    db = Database(str(tmp_path / "pr_sentinel.db"))
    await db.connect()

    deps = WebhookDeps(
        settings=settings,
        bot=bot,
        graph=graph,
        composer=composer,
        github=github,
        db=db,
    )
    app = create_app(deps=deps)
    app.state.deps = deps
    queue: asyncio.Queue[WorkItem] = asyncio.Queue(maxsize=100)
    app.state.queue = queue
    app.state.worker = asyncio.create_task(_queue_worker(deps, queue))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, deps, queue

    app.state.worker.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.worker
    await graph.close()
    await db.close()


async def test_webhook_fallback_to_github_token(webhook_env_fallback: Env) -> None:
    client, deps, _ = webhook_env_fallback
    assert deps.settings.github_webhook_secret is None
    body = json.dumps(PAYLOAD).encode("utf-8")
    signature = "sha256=" + hmac.new(GITHUB_TOKEN.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": signature,
    }

    response = await client.post("/webhook/github", content=body, headers=headers)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}


async def test_health_endpoint(webhook_env: Env) -> None:
    client, _, _ = webhook_env

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_webhook_valid_pull_request(webhook_env: Env) -> None:
    client, deps, queue = webhook_env
    body = json.dumps(PAYLOAD).encode("utf-8")
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": _sign(body),
        "X-GitHub-Delivery": "delivery-1",
    }

    risk = RiskAssessment(level="MED", reasons=["Touches core config path"], confidence=0.7)
    with patch("src.webhook.server.analyze_pr", new=AsyncMock(return_value=risk)) as analyze:
        response = await client.post("/webhook/github", content=body, headers=headers)

        # The handler acknowledges and enqueues; the worker does the pipeline.
        assert response.status_code == 202
        assert response.json() == {"status": "accepted"}
        await asyncio.wait_for(queue.join(), timeout=10)
    analyze.assert_awaited_once()
    deps.bot.send_notification.assert_awaited_once()
    (chat_id, text), _ = deps.bot.send_notification.call_args
    assert chat_id == CHAT_ID
    assert isinstance(text, str)
    assert "📊 Риск: 🟡 Средний" in text

    history = await deps.db.get_pr_history()
    assert len(history) == 1
    assert history[0]["pr_number"] == 42
    assert history[0]["title"] == PAYLOAD["pull_request"]["title"]
    assert history[0]["risk_level"] == "MED"


async def test_webhook_invalid_signature(webhook_env: Env) -> None:
    client, _, _ = webhook_env
    body = json.dumps(PAYLOAD).encode("utf-8")
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": "sha256=" + "0" * 64,
    }

    response = await client.post("/webhook/github", content=body, headers=headers)

    assert response.status_code == 401


async def test_webhook_wrong_event(webhook_env: Env) -> None:
    client, _, _ = webhook_env
    body = json.dumps(PAYLOAD).encode("utf-8")

    response = await client.post(
        "/webhook/github", content=body, headers={"X-GitHub-Event": "push"}
    )

    assert response.status_code == 400


async def test_webhook_action_filter(webhook_env: Env) -> None:
    client, _, _ = webhook_env
    payload = {**PAYLOAD, "action": "closed"}
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": _sign(body),
    }

    response = await client.post("/webhook/github", content=body, headers=headers)

    assert response.status_code == 400


async def test_webhook_non_json_payload(webhook_env: Env) -> None:
    client, _, _ = webhook_env
    body = b"not json"
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": _sign(body),
    }

    response = await client.post("/webhook/github", content=body, headers=headers)

    assert response.status_code == 400


async def test_webhook_body_too_large(webhook_env: Env) -> None:
    client, _, _ = webhook_env
    body = b"{" + b"x" * (MAX_BODY_BYTES + 1)
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": _sign(body),
    }

    response = await client.post("/webhook/github", content=body, headers=headers)

    assert response.status_code == 413


async def test_webhook_duplicate_delivery(webhook_env: Env) -> None:
    client, deps, queue = webhook_env
    body = json.dumps(PAYLOAD).encode("utf-8")
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": _sign(body),
        "X-GitHub-Delivery": "delivery-dup",
    }

    risk = RiskAssessment(level="MED", reasons=["Touches core config path"], confidence=0.7)
    with patch("src.webhook.server.analyze_pr", new=AsyncMock(return_value=risk)):
        first = await client.post("/webhook/github", content=body, headers=headers)
        second = await client.post("/webhook/github", content=body, headers=headers)

        # Only the first delivery was enqueued; drive the worker to completion.
        await asyncio.wait_for(queue.join(), timeout=10)

    assert first.status_code == 202
    assert first.json() == {"status": "accepted"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    deps.bot.send_notification.assert_awaited_once()
    history = await deps.db.get_pr_history()
    assert len(history) == 1
