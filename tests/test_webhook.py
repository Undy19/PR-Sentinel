"""Integration tests for the FastAPI GitHub webhook endpoint (ASGI transport)."""

from __future__ import annotations

import hashlib
import hmac
import json
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
from src.webhook.server import WebhookDeps, create_app

GITHUB_SECRET = "test-github-secret"
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
    return "sha256=" + hmac.new(GITHUB_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest.fixture
async def webhook_env(tmp_path: Path) -> tuple[httpx.AsyncClient, WebhookDeps]:
    settings = Settings.model_validate(
        {
            "telegram_bot_token": "123:TEST",
            "github_token": GITHUB_SECRET,
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
    # ASGITransport does not run the app lifespan, so expose the bundle to
    # ``get_deps`` the same way ``create_app``'s lifespan would.
    app.state.deps = deps

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, deps

    await graph.close()
    await db.close()


async def test_health_endpoint(webhook_env: tuple[httpx.AsyncClient, WebhookDeps]) -> None:
    client, _ = webhook_env

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_webhook_valid_pull_request(
    webhook_env: tuple[httpx.AsyncClient, WebhookDeps],
) -> None:
    client, deps = webhook_env
    body = json.dumps(PAYLOAD).encode("utf-8")
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": _sign(body),
    }

    risk = RiskAssessment(level="MED", reasons=["Touches core config path"], confidence=0.7)
    with patch("src.webhook.server.analyze_pr", new=AsyncMock(return_value=risk)) as analyze:
        response = await client.post("/webhook/github", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

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


async def test_webhook_invalid_signature(
    webhook_env: tuple[httpx.AsyncClient, WebhookDeps],
) -> None:
    client, _ = webhook_env
    body = json.dumps(PAYLOAD).encode("utf-8")
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": "sha256=" + "0" * 64,
    }

    response = await client.post("/webhook/github", content=body, headers=headers)

    assert response.status_code == 401


async def test_webhook_wrong_event(webhook_env: tuple[httpx.AsyncClient, WebhookDeps]) -> None:
    client, _ = webhook_env
    body = json.dumps(PAYLOAD).encode("utf-8")

    response = await client.post(
        "/webhook/github", content=body, headers={"X-GitHub-Event": "push"}
    )

    assert response.status_code == 400


async def test_webhook_action_filter(webhook_env: tuple[httpx.AsyncClient, WebhookDeps]) -> None:
    client, _ = webhook_env
    payload = {**PAYLOAD, "action": "closed"}
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": _sign(body),
    }

    response = await client.post("/webhook/github", content=body, headers=headers)

    assert response.status_code == 400
