from __future__ import annotations

import logging

from pydantic import field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_VALID_LANGUAGES = frozenset({"ru", "en"})


class Settings(BaseSettings):
    telegram_bot_token: str
    github_token: str
    github_webhook_secret: str | None = (
        None  # dedicated HMAC key; falls back to github_token when unset (SEC-02)
    )
    openai_api_key: str
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o"
    database_path: str = "pr_sentinel.db"
    github_repo: str  # "owner/repo" format
    telegram_chat_id: int
    notification_language: str = "ru"  # "ru" or "en"
    replay_protection_enabled: bool = True  # set False to skip X-GitHub-Delivery dedup (SEC-09)

    class Config:
        env_file = ".env"

    @field_validator("notification_language")
    @classmethod
    def _normalize_language(cls, value: str) -> str:
        """Coerce *value* to a supported language code.

        Strips/lowers the input and maps any unknown code (e.g. ``"de"``,
        ``"RU"``) to the ``"en"`` fallback, logging a warning. Normalizing at
        the source keeps the LLM ``reasons`` language (analyzer) and the
        notification template language (composer) consistent.
        """
        lang = value.strip().lower()
        if lang not in _VALID_LANGUAGES:
            logger.warning(
                "Unknown NOTIFICATION_LANGUAGE %r; falling back to 'en' " "(supported: ru, en)",
                value,
            )
            return "en"
        return lang


_settings: Settings | None = None
settings: Settings  # lazily constructed; resolved via __getattr__ / get_settings


def get_settings() -> Settings:
    """Return the process-wide settings instance, built on first use.

    Lazy construction keeps ``import src.config`` side-effect free, so
    modules that only need the :class:`Settings` type (e.g. the webhook
    server under test) import cleanly without environment variables set.
    """
    global _settings
    if _settings is None:
        _settings = Settings.model_validate({})
    return _settings


def __getattr__(name: str) -> object:
    """PEP 562 hook: ``from src.config import settings`` resolves lazily."""
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
