"""Unit tests for src.analyzer.risk (LLM-based PR risk scoring)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import openai

from src.analyzer.risk import RiskAssessment, analyze_pr

VALID_JSON = json.dumps({"level": "LOW", "reasons": ["Minor change"], "confidence": 0.9})


def _success_response(content: str) -> Mock:
    """Build a mock chat-completion response with the given message content."""
    message = Mock(content=content)
    choice = Mock(message=message)
    return Mock(choices=[choice])


def _rate_limit_error() -> openai.RateLimitError:
    """Build an openai.RateLimitError (429) without any network activity."""
    return openai.RateLimitError(
        "rate limited",
        response=Mock(status_code=429, headers={}),
        body=None,
    )


def _mock_client() -> AsyncMock:
    client = AsyncMock()
    client.chat.completions.create = AsyncMock()
    return client


async def test_analyze_pr_success() -> None:
    client = _mock_client()
    client.chat.completions.create.return_value = _success_response(VALID_JSON)

    result = await analyze_pr("diff", "PR title", "PR body", client=client)

    assert result == RiskAssessment(level="LOW", reasons=["Minor change"], confidence=0.9)
    assert client.chat.completions.create.await_count == 1


async def test_analyze_pr_language_instruction() -> None:
    client = _mock_client()
    client.chat.completions.create.return_value = _success_response(VALID_JSON)

    await analyze_pr("diff", "PR title", "PR body", client=client, language="ru")
    system_msg = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "Russian" in system_msg
    assert "English" not in system_msg

    client.chat.completions.create.return_value = _success_response(VALID_JSON)
    await analyze_pr("diff", "PR title", "PR body", client=client, language="en")
    system_msg = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "English" in system_msg
    assert "Russian" not in system_msg


async def test_analyze_pr_malformed_json() -> None:
    client = _mock_client()
    client.chat.completions.create.return_value = _success_response("not json")

    result = await analyze_pr("diff", "PR title", "PR body", client=client)

    assert result.level == "HIGH"
    assert result.reasons[0] == "Не удалось разобрать ответ модели"


async def test_analyze_pr_rate_limit_retry() -> None:
    client = _mock_client()
    client.chat.completions.create.side_effect = [
        _rate_limit_error(),
        _success_response(VALID_JSON),
    ]

    with patch("src.analyzer.risk.asyncio.sleep", new=AsyncMock()) as sleep:
        result = await analyze_pr("diff", "PR title", "PR body", client=client)

    assert result == RiskAssessment(level="LOW", reasons=["Minor change"], confidence=0.9)
    assert client.chat.completions.create.await_count == 2
    assert sleep.await_count == 1


async def test_analyze_pr_rate_limit_exhausted() -> None:
    client = _mock_client()
    client.chat.completions.create.side_effect = _rate_limit_error()

    with patch("src.analyzer.risk.asyncio.sleep", new=AsyncMock()) as sleep:
        result = await analyze_pr("diff", "PR title", "PR body", client=client)

    assert result.level == "HIGH"
    assert result.reasons[0] == "Превышен лимит запросов к модели"
    # 1 initial attempt + 3 retries.
    assert client.chat.completions.create.await_count == 4
    assert sleep.await_count == 3


async def test_analyze_pr_timeout() -> None:
    client = _mock_client()
    client.chat.completions.create.side_effect = TimeoutError()

    with patch("src.analyzer.risk.asyncio.sleep", new=AsyncMock()):
        result = await analyze_pr("diff", "PR title", "PR body", client=client)

    assert result.level == "HIGH"
    assert result.reasons[0] == "Запрос к модели прерван по таймауту"
    # 1 initial attempt + 3 retries.
    assert client.chat.completions.create.await_count == 4
