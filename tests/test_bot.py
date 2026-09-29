"""Unit tests for src.bot.bot (Telegram bot wrapper)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from src.bot.bot import PRBot


def test_prbot_instantiation() -> None:
    bot = PRBot("123:TEST")

    assert bot.bot is not None
    assert bot.dp is not None


async def test_send_notification() -> None:
    bot = PRBot("123:TEST")
    bot.bot.send_message = AsyncMock()

    await bot.send_notification(42, "hello")

    bot.bot.send_message.assert_awaited_once_with(42, "hello", parse_mode="MarkdownV2")
