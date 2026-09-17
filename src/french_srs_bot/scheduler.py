"""Daily jobs: practice batches at fixed times and an evening reminder."""

from __future__ import annotations

import logging

import psycopg
from telegram.ext import Application, ContextTypes

from . import db, messages, session
from .bot import deps_of, send, send_step, utcnow
from .config import Settings

log = logging.getLogger(__name__)


async def batch_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    chat_id = deps.secrets.telegram_user_id
    now = utcnow()
    try:
        with db.connect(deps.secrets.database_url) as conn:
            step = session.scheduled_batch(conn, deps.settings, now)
            if step is not None:
                await send_step(context.bot, chat_id, conn, step, now)
    except psycopg.OperationalError:
        log.exception("database unavailable in scheduled batch")


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    try:
        with db.connect(deps.secrets.database_url) as conn:
            stats = session.reminder_needed(conn, deps.settings, utcnow())
    except psycopg.OperationalError:
        log.exception("database unavailable in reminder")
        return
    if stats is not None:
        await send(context.bot, deps.secrets.telegram_user_id, messages.reminder(stats, deps.settings.daily_goal))


def register_jobs(app: Application, settings: Settings) -> None:
    for moment in settings.batch_times:
        app.job_queue.run_daily(
            batch_job, time=moment.replace(tzinfo=settings.timezone), name=f"batch-{moment:%H%M}"
        )
    app.job_queue.run_daily(
        reminder_job, time=settings.reminder_time.replace(tzinfo=settings.timezone), name="reminder"
    )
