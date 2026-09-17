"""Telegram handlers. Thin layer: all decisions live in session.py."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg
from fsrs import Scheduler
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import db, messages, session
from .config import Secrets, Settings
from .srs import build_scheduler

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Deps:
    settings: Settings
    secrets: Secrets
    scheduler: Scheduler


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def deps_of(context: ContextTypes.DEFAULT_TYPE) -> Deps:
    return context.application.bot_data["deps"]


async def send(bot: Bot, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def send_step(
    bot: Bot, chat_id: int, conn: psycopg.Connection, step: session.Ask | session.Summary, now: datetime
) -> None:
    """Send the next thing to the user: an intro, a question, or a summary."""
    if isinstance(step, session.Summary):
        text = messages.summary(
            step.done_today, step.goal, step.due_now, step.streak, more_available=step.more_available
        )
        await send(bot, chat_id, text)
        return
    if step.is_new:
        button = InlineKeyboardButton(messages.BUTTON_UNDERSTOOD, callback_data=f"intro:{step.card.card_id}")
        await send(bot, chat_id, messages.intro(step.card), InlineKeyboardMarkup([[button]]))
        return
    if not session.mark_asked(conn, step.card, now):
        # Another card became pending meanwhile (e.g. a scheduled batch): that question stays open.
        log.info("not asking card %s: another card is pending", step.card.card_id)
        return
    await send(bot, chat_id, messages.prompt(step.card))


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(context.bot, update.effective_chat.id, messages.welcome(deps_of(context).settings.batch_times))


async def on_practice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    chat_id = update.effective_chat.id
    now = utcnow()
    try:
        with db.connect(deps.secrets.database_url) as conn:
            await send_step(context.bot, chat_id, conn, session.start_batch(conn, deps.settings, now), now)
    except psycopg.Error:
        log.exception("database unavailable in /practice")
        await send(context.bot, chat_id, messages.database_unavailable())


async def on_intro_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    query = update.callback_query
    if update.effective_user is None or update.effective_user.id != deps.secrets.telegram_user_id:
        await query.answer()
        return
    card_id = int(query.data.split(":", 1)[1])
    now = utcnow()
    try:
        with db.connect(deps.secrets.database_url) as conn:
            card = session.acknowledge_intro(conn, deps.settings, card_id, now)
    except psycopg.Error:
        log.exception("database unavailable on intro button")
        await query.answer()
        await send(context.bot, update.effective_chat.id, messages.database_unavailable())
        return
    if card is None:
        await query.answer(messages.stale_intro())
    else:
        await query.answer()
        await send(context.bot, update.effective_chat.id, messages.prompt(card))
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        log.warning("could not remove the intro button", exc_info=True)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    chat_id = update.effective_chat.id
    now = utcnow()
    try:
        with db.connect(deps.secrets.database_url) as conn:
            answered = session.answer(conn, deps.settings, deps.scheduler, update.message.text, now)
            if answered is None:
                await send(context.bot, chat_id, messages.no_pending_card())
                return
            await send(context.bot, chat_id, messages.feedback(answered.result, answered.card))
            await send_step(context.bot, chat_id, conn, answered.next, now)
    except psycopg.Error:
        log.exception("database unavailable while answering")
        await send(context.bot, chat_id, messages.database_unavailable())


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("unhandled error", exc_info=context.error)


def build_application(settings: Settings, secrets: Secrets) -> Application:
    # Generous timeouts: the node's Wi-Fi can be very slow (seconds of latency, packet loss).
    # The library defaults (5s) turn such a moment into an endless retry loop at startup.
    app = (
        Application.builder()
        .token(secrets.telegram_bot_token)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(40)
        .build()
    )
    app.bot_data["deps"] = Deps(
        settings=settings,
        secrets=secrets,
        scheduler=build_scheduler(settings.learning_steps, settings.relearning_steps),
    )
    # UpdateType.MESSAGE: ignore edited messages, an edit must not count as a new answer or command.
    only_me = filters.User(user_id=secrets.telegram_user_id) & filters.UpdateType.MESSAGE
    app.add_handler(CommandHandler("start", on_start, filters=only_me))
    app.add_handler(CommandHandler("practice", on_practice, filters=only_me))
    app.add_handler(CallbackQueryHandler(on_intro_pressed, pattern=r"^intro:\d+$"))
    app.add_handler(MessageHandler(only_me & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app
