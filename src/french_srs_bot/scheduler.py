"""Daily jobs: practice batches at fixed times and an evening reminder."""

from __future__ import annotations

import logging

import psycopg
from telegram.ext import Application, ContextTypes

from . import db, messages, session
from .bot import deps_of, send, send_step, utcnow
from .config import Settings, settings_for

log = logging.getLogger(__name__)


async def batch_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    now = utcnow()
    try:
        with db.connect(deps.secrets.database_url) as conn:
            for user in db.all_users(conn):
                settings = settings_for(deps.settings, user)
                step = session.scheduled_batch(conn, settings, user.id, now)
                if step is not None:
                    await send_step(
                        context.bot, user.telegram_user_id, conn, step, now, settings, user.id
                    )
    except psycopg.Error:
        log.exception("database unavailable in scheduled batch")


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    now = utcnow()
    try:
        with db.connect(deps.secrets.database_url) as conn:
            needed = []
            for user in db.all_users(conn):
                settings = settings_for(deps.settings, user)
                stats = session.reminder_needed(conn, settings, user.id, now)
                if stats is not None:
                    needed.append((user, stats, settings.daily_goal))
    except psycopg.Error:
        log.exception("database unavailable in reminder")
        return
    for user, stats, goal in needed:
        await send(context.bot, user.telegram_user_id, messages.reminder(stats, goal))


def register_jobs(app: Application, settings: Settings) -> None:
    for moment in settings.batch_times:
        app.job_queue.run_daily(
            batch_job, time=moment.replace(tzinfo=settings.timezone), name=f"batch-{moment:%H%M}"
        )
    app.job_queue.run_daily(
        reminder_job, time=settings.reminder_time.replace(tzinfo=settings.timezone), name="reminder"
    )
