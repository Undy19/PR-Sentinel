"""Tests for :class:`Settings` language normalization (unknown-language guard).

The guard normalizes ``NOTIFICATION_LANGUAGE`` at the source so the LLM
``reasons`` language (analyzer) and the notification template language
(composer) always agree, instead of diverging on an unknown code.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from pr_sentinel.config import Settings


def _settings(language: str) -> Settings:
    return Settings.model_validate(
        {
            "telegram_bot_token": "123:TEST",
            "github_token": "gh-token",
            "github_webhook_secret": "wh-secret",
            "openai_api_key": "key",
            "github_repo": "owner/repo",
            "telegram_chat_id": 42,
            "notification_language": language,
        }
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ru", "ru"),
        ("en", "en"),
        ("RU", "ru"),  # case-insensitive
        (" En ", "en"),  # stripped + lowered
        ("de", "en"),  # unknown code -> EN
        ("fr", "en"),
        ("", "en"),  # empty -> EN
    ],
)
def test_notification_language_normalization(raw: str, expected: str) -> None:
    assert _settings(raw).notification_language == expected


def test_unknown_language_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="pr_sentinel.config"):
        settings = _settings("de")
    assert settings.notification_language == "en"
    assert any("Unknown NOTIFICATION_LANGUAGE" in rec.message for rec in caplog.records)


@pytest.mark.parametrize("secret", [None, ""])
def test_missing_or_empty_webhook_secret_rejected(
    secret: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GITHUB_WEBHOOK_SECRET`` is required and non-empty (SEC-02); no token fallback."""
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    kwargs: dict[str, object] = {
        "telegram_bot_token": "123:TEST",
        "github_token": "gh-token",
        "openai_api_key": "key",
        "github_repo": "owner/repo",
        "telegram_chat_id": 42,
    }
    if secret is not None:
        kwargs["github_webhook_secret"] = secret
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)
