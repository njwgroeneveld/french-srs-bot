import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from telegram.error import TelegramError

from french_srs_bot import bot, messages, session
from french_srs_bot.config import Secrets
from french_srs_bot.models import CardView

NOW = datetime(2026, 9, 18, 6, 0, tzinfo=timezone.utc)
CARD = CardView(
    card_id=5, item_id=1, direction="fr_nl", french=["le chien"], dutch=["de hond"],
    gender=None, hint=None, introduced_at=NOW, srs=None,
)


def make_context(settings):
    secrets = Secrets(telegram_bot_token="123:fake", telegram_user_id=42, database_url="postgresql://unused")
    context = MagicMock()
    context.application.bot_data = {"deps": bot.Deps(settings=settings, secrets=secrets, scheduler=None)}
    context.bot = AsyncMock()
    return context


def test_stale_intro_button_gets_feedback_and_is_removed(settings, monkeypatch):
    monkeypatch.setattr(bot.db, "connect", lambda url: nullcontext(object()))
    monkeypatch.setattr(bot.session, "acknowledge_intro", lambda *args: None)
    update = MagicMock()
    update.effective_user.id = 42
    update.effective_chat.id = 42
    query = update.callback_query
    query.data = "intro:5"
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock(side_effect=TelegramError("message is too old"))
    context = make_context(settings)

    asyncio.run(bot.on_intro_pressed(update, context))

    query.answer.assert_awaited_once_with(messages.stale_intro())
    assert messages.stale_intro() == "Dit woord is nu niet aan de beurt"
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    context.bot.send_message.assert_not_awaited()


def test_send_step_skips_question_when_another_card_is_pending(monkeypatch):
    monkeypatch.setattr(bot.session, "mark_asked", lambda conn, card, now: False)
    telegram_bot = AsyncMock()
    asyncio.run(bot.send_step(telegram_bot, 42, object(), session.Ask(CARD, is_new=False), NOW))
    telegram_bot.send_message.assert_not_awaited()


def test_send_step_asks_when_pending_could_be_set(monkeypatch):
    monkeypatch.setattr(bot.session, "mark_asked", lambda conn, card, now: True)
    telegram_bot = AsyncMock()
    asyncio.run(bot.send_step(telegram_bot, 42, object(), session.Ask(CARD, is_new=False), NOW))
    telegram_bot.send_message.assert_awaited_once()
