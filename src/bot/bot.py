"""Telegram bot (aiogram 3.x): polling lifecycle and notifications."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import Message

logger = logging.getLogger(__name__)

# Transient Telegram failures worth retrying: network/connectivity,
# rate-limit (429), and server-side (5xx). Non-retriable 4xx errors
# (bad request, unauthorized, ...) propagate immediately.
_RETRIABLE = (TelegramNetworkError, TelegramRetryAfter, TelegramServerError)
_SEND_MAX_ATTEMPTS = 5


class PRBot:
    """Long-running Telegram bot.

    :meth:`start` blocks for the lifetime of the polling loop, so run it
    as an ``asyncio`` task and end it with :meth:`stop`. The bot owns its
    aiogram :class:`Dispatcher` and session; no other state is kept.
    """

    def __init__(self, token: str) -> None:
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.dp.message.register(self._on_start)

    async def _on_start(self, message: Message) -> None:
        """Handle the ``/start`` command."""
        await message.answer("PR Sentinel is active.")

    async def start(self) -> None:
        """Start polling (blocks until :meth:`stop` is called).

        Signal handling is owned by :mod:`src.main`, so the dispatcher's
        built-in signal handlers are disabled to avoid double handling.
        """
        # Delete any existing webhook to avoid conflict with polling
        await self.bot.delete_webhook()
        await self.dp.start_polling(
            self.bot,
            handle_signals=False,
            close_bot_session=False,
        )

    async def stop(self) -> None:
        """Graceful shutdown: stop polling and close the bot session.

        Safe to call when polling has already ended (aiogram raises
        ``RuntimeError`` in that case), so shutdown is idempotent.
        """
        try:
            await self.dp.stop_polling()
        except RuntimeError:
            logger.info("polling was not active; nothing to stop")
        await self.bot.session.close()

    async def send_notification(self, chat_id: int, text: str) -> None:
        """Send a message to the configured chat.

        Retries transient failures (network errors, 429 rate-limit, 5xx)
        up to ``_SEND_MAX_ATTEMPTS`` times with a linear 1s, 2s, 3s, ...
        backoff between attempts. Non-retriable 4xx errors raise
        immediately.
        """
        for attempt in range(1, _SEND_MAX_ATTEMPTS + 1):
            try:
                await self.bot.send_message(chat_id, text, parse_mode="MarkdownV2")
                return
            except _RETRIABLE as exc:
                if attempt == _SEND_MAX_ATTEMPTS:
                    logger.error(
                        "Telegram send to chat %s failed after %d attempts: %s",
                        chat_id,
                        _SEND_MAX_ATTEMPTS,
                        exc,
                    )
                    raise
                delay = attempt
                logger.warning(
                    "Telegram send to chat %s failed (attempt %d/%d): %s; " "retrying in %ds",
                    chat_id,
                    attempt,
                    _SEND_MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
