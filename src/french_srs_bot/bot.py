"""Telegram handlers. Thin layer: all decisions live in session.py."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO

import psycopg
from fsrs import Scheduler
from telegram import BotCommand, Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
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

from . import db, messages, session, tts
from .config import Secrets, Settings, settings_for
from .grading import Grade
from .models import CardView, User
from .srs import build_scheduler

log = logging.getLogger(__name__)

GAP_PREFIX = "gap"
OVERRIDE_PREFIX = "ok"


@dataclass(frozen=True)
class Deps:
    settings: Settings
    secrets: Secrets
    scheduler: Scheduler


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def deps_of(context: ContextTypes.DEFAULT_TYPE) -> Deps:
    return context.application.bot_data["deps"]


def user_of(conn: psycopg.Connection, update: Update) -> User | None:
    """The user this update belongs to. None for someone the bot does not know."""
    if update.effective_user is None:
        return None
    return db.user_by_telegram_id(conn, update.effective_user.id)


async def send(bot: Bot, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def send_with_voice(
    bot: Bot, chat_id: int, conn: psycopg.Connection, card: CardView, text: str, settings: Settings
) -> None:
    """Send `text` as the caption of a voice memo of this item's French word.

    The audio belongs to the item, so both directions share one file_id. If the audio
    fails, `text` still goes out as a plain message: no sound must never mean no message.
    """
    if card.voice_file_id and card.voice_key == settings.tts.key:
        try:
            await bot.send_voice(
                chat_id=chat_id, voice=card.voice_file_id, caption=text, parse_mode=ParseMode.HTML
            )
            return
        except TelegramError:
            log.warning("Telegram refused the stored file_id of item %s", card.item_id, exc_info=True)

    try:
        # Synthesis is half a second of CPU work: in a thread, or the whole bot stalls.
        audio = await asyncio.to_thread(tts.synthesize, card.french[0], settings.tts)
        message = await bot.send_voice(
            chat_id=chat_id,
            voice=InputFile(BytesIO(audio), filename=f"{card.item_id}.ogg"),
            caption=text,
            parse_mode=ParseMode.HTML,
        )
    except Exception:  # a missing model, a broken voice, a refused upload: all the same here
        log.exception("no audio for card %s, the message goes as text", card.card_id)
        await send(bot, chat_id, text)
        return

    # The message is already out; forgetting the file_id only costs one extra synthesis later,
    # so it must not trigger the fallback above and send the same text a second time.
    try:
        db.save_voice(conn, item_id=card.item_id, file_id=message.voice.file_id, key=settings.tts.key)
    except psycopg.Error:
        log.warning("could not store the file_id of item %s", card.item_id, exc_info=True)


def gap_markup(card: CardView) -> InlineKeyboardMarkup:
    """One button per choice, shuffled so the position is not what gets learned.

    The callback data carries the index into the theme's own list, so a shuffled layout
    never changes what a button means.
    """
    buttons = [
        InlineKeyboardButton(choice, callback_data=f"{GAP_PREFIX}:{card.card_id}:{index}")
        for index, choice in enumerate(card.choices)
    ]
    random.shuffle(buttons)
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(rows)


def choice_from_callback(card: CardView, data: str) -> str | None:
    """The choice a `gap:<card_id>:<index>` callback points at, or None when it does not fit."""
    _prefix, _card_id, index = data.split(":")
    if not index.isdigit() or int(index) >= len(card.choices):
        return None
    return card.choices[int(index)]


async def send_question(
    bot: Bot, chat_id: int, conn: psycopg.Connection, card: CardView, settings: Settings
) -> None:
    """Ask the question. The French sounds wherever it is the question itself: with FR->NL and
    with a sentence to translate. With NL->FR and with a gap to fill it would give the answer away."""
    text = messages.prompt(card)
    if card.direction == "gap":
        if not card.choices:
            # Hand-edited row: a gap card without choices has no answerable question. Skipping it
            # leaves it unasked and due, which is recoverable; sending it would strand the batch.
            log.error("grammar card %s has no choices, skipping it", card.card_id)
            return
        await send(bot, chat_id, text, gap_markup(card))
        return
    if settings.tts.enabled and card.direction in ("fr_nl", "translate"):
        await send_with_voice(bot, chat_id, conn, card, text, settings)
    else:
        await send(bot, chat_id, text)


async def send_feedback(
    bot: Bot, chat_id: int, conn: psycopg.Connection, answered: session.Answered, settings: Settings
) -> None:
    """Show the verdict. The French sounds where the right French first appears: after an
    NL->FR answer, and with the completed sentence of a gap card."""
    card = answered.card
    if card.direction == "gap":
        text = messages.gap_feedback(answered.result.grade is Grade.CORRECT, card)
    else:
        text = messages.feedback(answered.result, card)
    markup = None
    if card.direction == "translate" and answered.result.grade is Grade.WRONG:
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(messages.BUTTON_OVERRIDE,
                                   callback_data=f"{OVERRIDE_PREFIX}:{answered.review_id}")]]
        )
    if settings.tts.enabled and card.direction in ("nl_fr", "gap"):
        await send_with_voice(bot, chat_id, conn, card, text, settings)
        if markup is not None:  # a voice memo cannot carry a keyboard
            await send(bot, chat_id, messages.override_hint(), markup)
        return
    await send(bot, chat_id, text, markup)


async def nudge_others(
    bot: Bot, conn: psycopg.Connection, achiever: User, base: Settings, now: datetime
) -> None:
    """Tell everyone else that `achiever` just hit their daily goal.

    Once per person per day: session.answer flags only the crossing answer. Everyone sees
    their own score against their own goal, not the achiever's.
    """
    for other in db.all_users(conn):
        if other.id == achiever.id:
            continue
        settings = settings_for(base, other)
        stats = session.today_stats(conn, settings, other.id, now)
        await send(
            bot,
            other.telegram_user_id,
            messages.peer_reached_goal(achiever.name, done=stats.total, goal=settings.daily_goal),
        )


async def send_step(
    bot: Bot,
    chat_id: int,
    conn: psycopg.Connection,
    step: session.Ask | session.Summary,
    now: datetime,
    settings: Settings,
    user_id: int,
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
    if not session.mark_asked(conn, step.card, now, user_id=user_id):
        # Another card became pending meanwhile (e.g. a scheduled batch): that question stays open.
        log.info("not asking card %s: another card is pending", step.card.card_id)
        return
    await send_question(bot, chat_id, conn, step.card, settings)


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(context.bot, update.effective_chat.id, messages.welcome(deps_of(context).settings.batch_times))


async def on_practice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    chat_id = update.effective_chat.id
    now = utcnow()
    try:
        with db.connect(deps.secrets.database_url) as conn:
            user = user_of(conn, update)
            if user is None:
                return
            settings = settings_for(deps.settings, user)
            step = session.start_batch(conn, settings, user.id, now)
            await send_step(context.bot, chat_id, conn, step, now, settings, user.id)
    except psycopg.Error:
        log.exception("database unavailable in /practice")
        await send(context.bot, chat_id, messages.database_unavailable())


async def on_intro_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    query = update.callback_query
    card_id = int(query.data.split(":", 1)[1])
    now = utcnow()
    try:
        with db.connect(deps.secrets.database_url) as conn:
            user = user_of(conn, update)
            if user is None:
                await query.answer()
                return
            settings = settings_for(deps.settings, user)
            card = session.acknowledge_intro(conn, settings, card_id, now, user_id=user.id)
            if card is None:
                await query.answer(messages.stale_intro())
            else:
                await query.answer()
                await send_question(context.bot, update.effective_chat.id, conn, card, settings)
    except psycopg.Error:
        log.exception("database unavailable on intro button")
        await query.answer()
        await send(context.bot, update.effective_chat.id, messages.database_unavailable())
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        log.warning("could not remove the intro button", exc_info=True)


async def on_gap_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    query = update.callback_query
    chat_id = update.effective_chat.id
    now = utcnow()
    try:
        with db.connect(deps.secrets.database_url) as conn:
            user = user_of(conn, update)
            if user is None:
                await query.answer()
                return
            settings = settings_for(deps.settings, user)
            state = db.get_bot_state(conn, user_id=user.id)
            card_id = int(query.data.split(":")[1])
            card = db.get_card(conn, card_id, user_id=user.id) if state.pending_card_id == card_id else None
            choice = choice_from_callback(card, query.data) if card else None
            if choice is None:
                await query.answer(messages.stale_intro())
                return
            await query.answer()
            answered = session.answer_choice(conn, settings, deps.scheduler, choice, now, user_id=user.id)
            if answered is None:
                return
            await send_feedback(context.bot, chat_id, conn, answered, settings)
            await send_step(context.bot, chat_id, conn, answered.next, now, settings, user.id)
            if answered.goal_just_reached:
                await nudge_others(context.bot, conn, user, deps.settings, now)
    except psycopg.Error:
        log.exception("database unavailable on a gap button")
        await query.answer()
        await send(context.bot, chat_id, messages.database_unavailable())
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        log.warning("could not remove the gap buttons", exc_info=True)


async def on_override_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    query = update.callback_query
    review_id = int(query.data.split(":")[1])
    now = utcnow()
    try:
        with db.connect(deps.secrets.database_url) as conn:
            user = user_of(conn, update)
            if user is None:
                await query.answer()
                return
            settings = settings_for(deps.settings, user)
            card = session.override(conn, settings, deps.scheduler, review_id, now, user_id=user.id)
            await query.answer(messages.override_applied() if card else messages.override_refused())
    except psycopg.Error:
        log.exception("database unavailable on the override button")
        await query.answer()
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        log.warning("could not remove the override button", exc_info=True)


async def on_grammar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    chat_id = update.effective_chat.id
    now = utcnow()
    try:
        with db.connect(deps.secrets.database_url) as conn:
            user = user_of(conn, update)
            if user is None:
                return
            settings = settings_for(deps.settings, user)
            step = session.start_batch(conn, settings, user.id, now, kind="grammar")
            await send_step(context.bot, chat_id, conn, step, now, settings, user.id)
    except psycopg.Error:
        log.exception("database unavailable in /grammar")
        await send(context.bot, chat_id, messages.database_unavailable())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    chat_id = update.effective_chat.id
    now = utcnow()
    try:
        with db.connect(deps.secrets.database_url) as conn:
            user = user_of(conn, update)
            if user is None:
                return
            settings = settings_for(deps.settings, user)
            answered = session.answer(
                conn, settings, deps.scheduler, update.message.text, now, user_id=user.id
            )
            if answered is None:
                await send(context.bot, chat_id, messages.no_pending_card())
                return
            await send_feedback(context.bot, chat_id, conn, answered, settings)
            await send_step(context.bot, chat_id, conn, answered.next, now, settings, user.id)
            if answered.goal_just_reached:
                await nudge_others(context.bot, conn, user, deps.settings, now)
    except psycopg.Error:
        log.exception("database unavailable while answering")
        await send(context.bot, chat_id, messages.database_unavailable())


async def on_standings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    chat_id = update.effective_chat.id
    try:
        with db.connect(deps.secrets.database_url) as conn:
            rows = session.week_standings(conn, deps.settings, utcnow())
    except psycopg.Error:
        log.exception("database unavailable in /stand")
        await send(context.bot, chat_id, messages.database_unavailable())
        return
    await send(context.bot, chat_id, messages.standings(rows))


async def on_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    chat_id = update.effective_chat.id
    try:
        with db.connect(deps.secrets.database_url) as conn:
            user = user_of(conn, update)
            if user is None:
                return
            themes = db.theme_progress(conn, user_id=user.id)
    except psycopg.Error:
        log.exception("database unavailable in /volgorde")
        await send(context.bot, chat_id, messages.database_unavailable())
        return
    await send(context.bot, chat_id, messages.study_order(themes))


async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = deps_of(context)
    settings = deps.settings
    try:
        with db.connect(deps.secrets.database_url) as conn:
            user = user_of(conn, update)
            if user is not None:
                settings = settings_for(settings, user)
    except psycopg.Error:
        # Help is exactly what you want when things are broken, so a dead database only
        # costs the personal goal, not the message.
        log.warning("database unavailable in /help, showing the shared settings", exc_info=True)
    await send(
        context.bot,
        update.effective_chat.id,
        messages.help_text(settings.batch_times, settings.daily_goal),
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("unhandled error", exc_info=context.error)


# What Telegram shows when you type "/" in the chat. Far more discoverable than a help text
# you have to know about first.
MENU = [
    BotCommand("practice", "Oefen nu een setje"),
    BotCommand("grammar", "Oefen alleen de zinnen"),
    BotCommand("stand", "Hoe staan jullie er deze week voor"),
    BotCommand("volgorde", "Welke onderwerpen komen eraan"),
    BotCommand("help", "Hoe werkt deze bot?"),
]


async def publish_menu(app: Application) -> None:
    """Register the command menu with Telegram. A flaky network must not block startup."""
    try:
        await app.bot.set_my_commands(MENU)
    except TelegramError:
        log.warning("could not publish the command menu", exc_info=True)


async def send_announcements(app: Application) -> None:
    """Push short release notes once to everyone who has not received them yet.

    Runs on every start, but only sends what is new: a plain redeploy stays silent. One
    unreachable person must not stop the others, so each user is handled on their own.
    """
    deps = app.bot_data["deps"]
    try:
        with db.connect(deps.secrets.database_url) as conn:
            for user in db.all_users(conn):
                pending = messages.unseen_announcements(user.last_announcement)
                if not pending:
                    continue
                try:
                    for _number, text in pending:
                        await send(app.bot, user.telegram_user_id, text)
                except TelegramError:
                    log.warning("could not reach %s with the release notes", user.name, exc_info=True)
                    continue
                db.mark_announcement_seen(
                    conn, user_id=user.id, announcement_id=pending[-1][0]
                )
                log.info("sent %s release note(s) to %s", len(pending), user.name)
    except psycopg.Error:
        log.warning("database unavailable, release notes wait for the next start", exc_info=True)


async def on_startup(app: Application) -> None:
    await publish_menu(app)
    await send_announcements(app)


def build_application(settings: Settings, secrets: Secrets, users: Sequence[User]) -> Application:
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
        .post_init(on_startup)
        .build()
    )
    app.bot_data["deps"] = Deps(
        settings=settings,
        secrets=secrets,
        scheduler=build_scheduler(settings.learning_steps, settings.relearning_steps),
    )
    # UpdateType.MESSAGE: ignore edited messages, an edit must not count as a new answer.
    known = filters.User(user_id=[u.telegram_user_id for u in users]) & filters.UpdateType.MESSAGE
    app.add_handler(CommandHandler("start", on_start, filters=known))
    app.add_handler(CommandHandler("practice", on_practice, filters=known))
    app.add_handler(CommandHandler("grammar", on_grammar, filters=known))
    app.add_handler(CommandHandler("stand", on_standings, filters=known))
    app.add_handler(CommandHandler("volgorde", on_order, filters=known))
    app.add_handler(CommandHandler("help", on_help, filters=known))
    app.add_handler(CallbackQueryHandler(on_intro_pressed, pattern=r"^intro:\d+$"))
    app.add_handler(CallbackQueryHandler(on_gap_pressed, pattern=r"^gap:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(on_override_pressed, pattern=r"^ok:\d+$"))
    app.add_handler(MessageHandler(known & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app
