"""Integration tests for the FastAPI GitHub webhook endpoint (ASGI transport)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from pr_sentinel.analyzer.risk import RiskAssessment
from pr_sentinel.bot.bot import PRBot
from pr_sentinel.config import Settings
from pr_sentinel.db.database import Database
from pr_sentinel.graph.expertise import ExpertiseGraph
from pr_sentinel.notifications.composer import NotificationComposer
from pr_sentinel.webhook.server import (
    MAX_BODY_BYTES,
    WebhookDeps,
    WorkItem,
    _close_bundle,
    _queue_worker,
    build_default_deps,
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
    with patch(
        "pr_sentinel.webhook.server.analyze_pr", new=AsyncMock(return_value=risk)
    ) as analyze:
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
    with patch("pr_sentinel.webhook.server.analyze_pr", new=AsyncMock(return_value=risk)):
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


def _make_repo(repo: Path) -> None:
    """Create a temp repo: alice commits a.py 2x (recent), bob commits b.py 1x (old)."""
    _git(repo, "init", "-q")

    for version, date in (
        ("a1\n", "2026-08-01T10:00:00+00:00"),
        ("a2\n", "2026-08-02T10:00:00+00:00"),
    ):
        (repo / "a.py").write_text(version)
        _git(repo, "add", "a.py", env=_dated(_ALICE, date))
        _git(repo, "commit", "-q", "-m", f"alice: {version.strip()}", env=_dated(_ALICE, date))

    (repo / "b.py").write_text("b1\n")
    _git(repo, "add", "b.py", env=_dated(_BOB, "2026-01-01T10:00:00+00:00"))
    _git(repo, "commit", "-q", "-m", "bob: b1", env=_dated(_BOB, "2026-01-01T10:00:00+00:00"))


def _make_settings(tmp_path: Path, **overrides: str) -> Settings:
    """A valid settings object for the standalone bundle; ``overrides`` replace keys."""
    values: dict[str, object] = {
        "telegram_bot_token": "123:TEST",
        "github_token": GITHUB_TOKEN,
        "github_webhook_secret": WEBHOOK_SECRET,
        "openai_api_key": "test-openai-key",
        "github_repo": "owner/repo",
        "telegram_chat_id": CHAT_ID,
        "database_path": str(tmp_path / "pr_sentinel.db"),
        "replay_protection_enabled": True,
    }
    values.update(overrides)
    return Settings.model_validate(values)


async def test_build_default_deps_builds_graph_from_repo_path(tmp_path: Path) -> None:
    """Standalone mode builds the expertise graph from ``REPO_PATH`` (regression)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)

    deps = await build_default_deps(_make_settings(tmp_path, repo_path=str(repo)))
    try:
        reviewers = await deps.graph.recommend_reviewers(["a.py", "b.py"])
        assert [reviewer.login for reviewer in reviewers] == ["alice", "bob"]
    finally:
        await _close_bundle(deps)


async def test_build_default_deps_non_git_repo_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-git ``REPO_PATH`` is logged, never raised: the server still starts."""
    plain = tmp_path / "plain"
    plain.mkdir()

    with caplog.at_level(logging.ERROR, logger="pr_sentinel.webhook.server"):
        deps = await build_default_deps(_make_settings(tmp_path, repo_path=str(plain)))

    try:
        assert await deps.graph.recommend_reviewers(["a.py"]) == []
    finally:
        await _close_bundle(deps)

    assert any("failed to build expertise graph" in record.message for record in caplog.records)
