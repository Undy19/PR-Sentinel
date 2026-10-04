from __future__ import annotations

import logging

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_VALID_LANGUAGES = frozenset({"ru", "en"})


class Settings(BaseSettings):
    telegram_bot_token: str = Field(..., min_length=1)
    github_token: str = Field(..., min_length=1)
    github_webhook_secret: str = Field(
        ..., min_length=1
    )  # dedicated HMAC key for X-Hub-Signature-256 (SEC-02)
    openai_api_key: str = Field(..., min_length=1)
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
                "Unknown NOTIFICATION_LANGUAGE %r; falling back to 'en' (supported: ru, en)",
                value,
            )
            return "en"
        return lang

    @field_validator("github_repo")
    @classmethod
    def _validate_repo_format(cls, value: str) -> str:
        """Enforce the ``owner/repo`` format at startup, not on the first API call.

        Normalizes surrounding/inner whitespace (``" myorg / myrepo "`` →
        ``"myorg/myrepo"``) so typos in ``.env`` fail fast with a readable
        message instead of 404-ing every GitHub request.
        """
        parts = [part.strip() for part in value.split("/")]
        if len(parts) != 2 or not all(parts):
            raise ValueError("expected 'owner/repo' format (e.g. 'myorg/myrepo')")
        return "/".join(parts)


_settings: Settings | None = None
settings: Settings  # lazily constructed; resolved via __getattr__ / get_settings


def get_settings() -> Settings:
    """Return the process-wide settings instance, built on first use.

    Lazy construction keeps ``import pr_sentinel.config`` side-effect free, so
    modules that only need the :class:`Settings` type (e.g. the webhook
    server under test) import cleanly without environment variables set.
    """
    global _settings
    if _settings is None:
        _settings = Settings.model_validate({})
    return _settings


class SettingsError(RuntimeError):
    """Configuration is missing or invalid; ``str(exc)`` is user-readable."""


def _format_settings_error(exc: ValidationError) -> str:
    """Render a pydantic ``ValidationError`` as an actionable message."""
    lines = [
        (
            "Configuration error: the service cannot start. "
            "Fix .env (template: .env.example) and restart:"
        )
    ]
    for err in exc.errors():
        field = str(err["loc"][0]) if err["loc"] else "unknown"
        env_var = field.upper()
        if err["type"] == "missing":
            lines.append(f"  - {env_var}: is required but not set")
        elif err["type"] == "string_too_short":
            lines.append(f"  - {env_var}: must not be empty")
        else:
            lines.append(f"  - {env_var}: {err['msg']}")
    return "\n".join(lines)


def load_settings() -> Settings:
    """Build the process-wide settings with a user-readable failure mode.

    Raises:
        SettingsError: if any required variable is missing or invalid; the
            message lists every offending ``ENV_VAR`` by name.
    """
    try:
        return get_settings()
    except ValidationError as exc:
        error = SettingsError(_format_settings_error(exc))
        # The ValidationError is fully summarized in the message; suppress the
        # implicit context so entry points don't print a pydantic traceback.
        error.__suppress_context__ = True
        raise error


def __getattr__(name: str) -> object:
    """PEP 562 hook: ``from pr_sentinel.config import settings`` resolves lazily."""
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
