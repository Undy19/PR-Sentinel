"""Main entry point: starts bot + webhook server concurrently."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import asyncio
import logging
import os
import signal
from functools import partial
from types import FrameType

import uvicorn

from src.bot.bot import PRBot
from src.config import get_settings
from src.db.database import Database
from src.graph.expertise import ExpertiseGraph
from src.notifications.composer import NotificationComposer
from src.webhook.server import WebhookDeps, create_app, create_github_client

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    """Map SIGINT/SIGTERM onto *stop_event*.

    Prefers ``loop.add_signal_handler`` (POSIX); on platforms where it is
    unavailable (Windows) falls back to ``signal.signal``.
    """

    def _mark_stopped(sig: int) -> None:
        logger.info("received signal %d; shutting down", sig)
        stop_event.set()

    def _signal_fallback(signum: int, _frame: FrameType | None) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, partial(_mark_stopped, sig))
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, _signal_fallback)


async def main() -> None:
    """Create all services, then run the bot and webhook server until stopped."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    settings = get_settings()

    db = Database(settings.database_path)
    await db.connect()

    graph = await ExpertiseGraph.create(settings.database_path)
    repo_path = os.environ.get("REPO_PATH", ".")
    try:
        await graph.build_from_repo(repo_path)
        logger.info("expertise graph built from %s", repo_path)
    except Exception:
        logger.exception(
            "failed to build expertise graph from %s; "
            "continuing without reviewer recommendations",
            repo_path,
        )

    bot = PRBot(settings.telegram_bot_token)
    deps = WebhookDeps(
        settings=settings,
        bot=bot,
        graph=graph,
        composer=NotificationComposer(),
        github=create_github_client(settings.github_token),
        db=db,
    )
    app = create_app(deps)

    config = uvicorn.Config(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )
    server = uvicorn.Server(config)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    _install_signal_handlers(loop, stop_event)

    logger.info("starting PR Sentinel for repo %s", settings.github_repo)
    bot_task = asyncio.create_task(bot.start(), name="telegram-bot")
    server_task = asyncio.create_task(server.serve(), name="webhook-server")

    await stop_event.wait()
    logger.info("shutdown requested; stopping bot and webhook server")

    await bot.stop()
    server.should_exit = True
    results = await asyncio.gather(bot_task, server_task, return_exceptions=True)
    for name, result in zip(("telegram-bot", "webhook-server"), results, strict=True):
        if isinstance(result, BaseException):
            logger.error("%s exited with: %s", name, result)

    await deps.github.aclose()
    await graph.close()
    await db.close()
    logger.info("shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
