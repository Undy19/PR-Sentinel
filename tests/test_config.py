"""Tests for :class:`Settings` validation and language normalization.

The language guard normalizes ``NOTIFICATION_LANGUAGE`` at the source so the
LLM ``reasons`` language (analyzer) and the notification template language
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


def _settings_kwargs(**overrides: object) -> dict[str, object]:
    """A valid settings mapping; override any key, pass ``None`` to delete it."""
    kwargs: dict[str, object] = {
        "telegram_bot_token": "123:TEST",
        "github_token": "gh-token",
        "github_webhook_secret": "wh-secret",
        "openai_api_key": "key",
        "github_repo": "owner/repo",
        "telegram_chat_id": 42,
    }
    for key, value in overrides.items():
        if value is None:
            del kwargs[key]
        else:
            kwargs[key] = value
    return kwargs


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
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_settings_kwargs(github_webhook_secret=secret))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("telegram_bot_token", None),
        ("telegram_bot_token", ""),
        ("github_token", None),
        ("github_token", ""),
        ("openai_api_key", None),
        ("openai_api_key", ""),
    ],
)
def test_missing_or_empty_secret_rejected(
    field: str, value: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All required secrets must be present and non-empty."""
    monkeypatch.delenv(field.upper(), raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_settings_kwargs(**{field: value}))


@pytest.mark.parametrize(
    "repo",
    ["not-a-repo", "a/b/c", "owner/", "/repo", "   "],
)
def test_invalid_repo_format_rejected(repo: str) -> None:
    """``GITHUB_REPO`` must be ``owner/repo``; typos fail at startup."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_settings_kwargs(github_repo=repo))


def test_repo_format_normalized() -> None:
    settings = Settings(_env_file=None, **_settings_kwargs(github_repo="  myorg / myrepo  "))
    assert settings.github_repo == "myorg/myrepo"
