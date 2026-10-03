from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: str
    github_token: str
    github_webhook_secret: str
    openai_api_key: str
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o"
    database_path: str = "pr_sentinel.db"
    github_repo: str  # "owner/repo" format
    telegram_chat_id: int
    notification_language: str = "ru"  # "ru" or "en"

    class Config:
        env_file = ".env"


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
