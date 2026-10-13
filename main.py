"""
FitNudge — FastAPI application entry point.

The Discord gateway client and APScheduler run inside this process, on the same event loop.
Run database migrations first: `uv run alembic upgrade head`.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import FastAPI

from config import settings
from routes.internal import router as internal_router
from utils.logging_setup import configure_logging

configure_logging()
log = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    from agent import dispatch
    from agent.runtime import purge_stale_transcripts
    from scheduler.jobs import scheduler
    from timing.learn import refresh_all
    from timing.tick import run_tick

    scheduler.start()
    purge_stale_transcripts()
    scheduler.add_job(purge_stale_transcripts, "interval", minutes=30,
                      id="purge_agent_transcripts", replace_existing=True)
    scheduler.add_job(run_tick, "interval", minutes=5, id="daily_window_tick",
                      replace_existing=True, max_instances=1, coalesce=True)
    scheduler.add_job(refresh_all, "cron", hour=3, minute=30, id="timing_refresh",
                      replace_existing=True, coalesce=True)

    bot = None
    bot_task: asyncio.Task | None = None
    if settings.discord_bot_token:
        from discord_bot.bot import FitNudgeBot

        bot = FitNudgeBot()
        bot_task = asyncio.create_task(bot.start(settings.discord_bot_token), name="discord-gateway")
        bot_task.add_done_callback(_log_bot_exit)
        if settings.discord_application_id:
            log.info(
                "Invite the bot: https://discord.com/oauth2/authorize?client_id=%s&scope=bot%%20applications.commands",
                settings.discord_application_id,
            )
    else:
        log.warning("DISCORD_BOT_TOKEN is not set — Discord channel disabled.")

    log.info("FitNudge started | channel=%s | model=%s", settings.primary_channel, settings.agent_model)
    try:
        yield
    finally:
        if bot is not None:
            await bot.close()
        if bot_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await bot_task
        with contextlib.suppress(TimeoutError):
            await dispatch.wait_idle(timeout=20)
        scheduler.shutdown(wait=False)


def _log_bot_exit(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("Discord client stopped with an error", exc_info=task.exception())


app = FastAPI(
    title="FitNudge",
    description="Claude-powered workout companion (Discord now, KakaoTalk later)",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(internal_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
