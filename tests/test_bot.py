import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import psycopg
from telegram.error import TelegramError

from french_srs_bot import bot, messages, session
from french_srs_bot.config import Secrets
from french_srs_bot.grading import Grade, GradeResult
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


def test_send_step_skips_question_when_another_card_is_pending(settings, monkeypatch):
    monkeypatch.setattr(bot.session, "mark_asked", lambda conn, card, now: False)
    telegram_bot = AsyncMock()
    asyncio.run(bot.send_step(telegram_bot, 42, object(), session.Ask(CARD, is_new=False), NOW, settings))
    telegram_bot.send_message.assert_not_awaited()


def test_send_step_asks_when_pending_could_be_set(settings, monkeypatch):
    monkeypatch.setattr(bot.session, "mark_asked", lambda conn, card, now: True)
    telegram_bot = AsyncMock()
    asyncio.run(bot.send_step(telegram_bot, 42, object(), session.Ask(CARD, is_new=False), NOW, settings))
    telegram_bot.send_message.assert_awaited_once()


def enable_tts(settings, **overrides):
    """The same settings, but with tts switched on."""
    return replace(settings, tts=replace(settings.tts, enabled=True, **overrides))


def test_fr_nl_question_is_sent_as_a_voice_memo_with_the_question_as_caption(settings, monkeypatch):
    monkeypatch.setattr(bot.tts, "synthesize", lambda text, tts_settings: b"OggS-fake")
    saved = {}
    monkeypatch.setattr(bot.db, "save_voice", lambda conn, **kwargs: saved.update(kwargs))
    telegram_bot = AsyncMock()
    telegram_bot.send_voice.return_value.voice.file_id = "AwACAgQAAxk"

    asyncio.run(bot.send_question(telegram_bot, 42, object(), CARD, enable_tts(settings)))

    telegram_bot.send_message.assert_not_awaited()
    telegram_bot.send_voice.assert_awaited_once()
    assert telegram_bot.send_voice.await_args.kwargs["caption"] == messages.prompt(CARD)
    assert saved == {"item_id": 1, "file_id": "AwACAgQAAxk", "key": "fr_FR-siwis-medium@1.0"}


def test_a_stored_file_id_is_reused_without_synthesising(settings, monkeypatch):
    def fail(text, tts_settings):
        raise AssertionError("should not have synthesised again")

    monkeypatch.setattr(bot.tts, "synthesize", fail)
    monkeypatch.setattr(bot.db, "save_voice", lambda conn, **kwargs: None)
    cached = replace(CARD, voice_file_id="AwACAgQAAxk", voice_key="fr_FR-siwis-medium@1.0")
    telegram_bot = AsyncMock()

    asyncio.run(bot.send_question(telegram_bot, 42, object(), cached, enable_tts(settings)))

    assert telegram_bot.send_voice.await_args.kwargs["voice"] == "AwACAgQAAxk"


def test_a_file_id_from_another_tempo_is_not_reused(settings, monkeypatch):
    monkeypatch.setattr(bot.tts, "synthesize", lambda text, tts_settings: b"OggS-fake")
    monkeypatch.setattr(bot.db, "save_voice", lambda conn, **kwargs: None)
    stale = replace(CARD, voice_file_id="AwACAgQAAxk", voice_key="fr_FR-siwis-medium@1.4")
    telegram_bot = AsyncMock()
    telegram_bot.send_voice.return_value.voice.file_id = "nieuw"

    asyncio.run(bot.send_question(telegram_bot, 42, object(), stale, enable_tts(settings)))

    assert telegram_bot.send_voice.await_args.kwargs["voice"] != "AwACAgQAAxk"


def test_the_question_still_arrives_as_text_when_the_audio_fails(settings, monkeypatch):
    def boom(text, tts_settings):
        raise OSError("model missing")

    monkeypatch.setattr(bot.tts, "synthesize", boom)
    telegram_bot = AsyncMock()

    asyncio.run(bot.send_question(telegram_bot, 42, object(), CARD, enable_tts(settings)))

    telegram_bot.send_message.assert_awaited_once()
    assert telegram_bot.send_message.await_args.kwargs["text"] == messages.prompt(CARD)


def test_nl_fr_questions_stay_text_only(settings, monkeypatch):
    def fail(text, tts_settings):
        raise AssertionError("NL->FR asks for a French answer; audio would give it away")

    monkeypatch.setattr(bot.tts, "synthesize", fail)
    dutch_first = replace(CARD, direction="nl_fr")
    telegram_bot = AsyncMock()

    asyncio.run(bot.send_question(telegram_bot, 42, object(), dutch_first, enable_tts(settings)))

    telegram_bot.send_voice.assert_not_awaited()
    telegram_bot.send_message.assert_awaited_once()


def test_nl_fr_feedback_carries_the_french_word_as_audio(settings, monkeypatch):
    spoken = []
    monkeypatch.setattr(bot.tts, "synthesize", lambda text, tts_settings: spoken.append(text) or b"OggS-fake")
    monkeypatch.setattr(bot.db, "save_voice", lambda conn, **kwargs: None)
    dutch_first = replace(CARD, direction="nl_fr")
    answered = session.Answered(
        result=GradeResult(grade=Grade.CORRECT, reason="exact", expected="le chien"),
        card=dutch_first,
        next=session.Summary(done_today=1, goal=10, due_now=0, streak=0, more_available=False),
    )
    telegram_bot = AsyncMock()
    telegram_bot.send_voice.return_value.voice.file_id = "AwACAgQAAxk"

    asyncio.run(bot.send_feedback(telegram_bot, 42, object(), answered, enable_tts(settings)))

    # The item's French word is spoken, not whatever was typed.
    assert spoken == ["le chien"]
    assert telegram_bot.send_voice.await_args.kwargs["caption"] == messages.feedback(
        answered.result, dutch_first
    )


def test_fr_nl_feedback_stays_text_only(settings, monkeypatch):
    def fail(text, tts_settings):
        raise AssertionError("with FR->NL the word already sounded at the question")

    monkeypatch.setattr(bot.tts, "synthesize", fail)
    answered = session.Answered(
        result=GradeResult(grade=Grade.CORRECT, reason="exact", expected="de hond"),
        card=CARD,
        next=session.Summary(done_today=1, goal=10, due_now=0, streak=0, more_available=False),
    )
    telegram_bot = AsyncMock()

    asyncio.run(bot.send_feedback(telegram_bot, 42, object(), answered, enable_tts(settings)))

    telegram_bot.send_voice.assert_not_awaited()
    telegram_bot.send_message.assert_awaited_once()


def test_a_failed_file_id_write_does_not_send_the_message_twice(settings, monkeypatch):
    # The voice memo is already delivered; losing the file_id only costs one extra
    # synthesis later, so it must not fall back to sending the same text again.
    monkeypatch.setattr(bot.tts, "synthesize", lambda text, tts_settings: b"OggS-fake")

    def boom(conn, **kwargs):
        raise psycopg.OperationalError("connection gone")

    monkeypatch.setattr(bot.db, "save_voice", boom)
    telegram_bot = AsyncMock()
    telegram_bot.send_voice.return_value.voice.file_id = "AwACAgQAAxk"

    asyncio.run(bot.send_question(telegram_bot, 42, object(), CARD, enable_tts(settings)))

    telegram_bot.send_voice.assert_awaited_once()
    telegram_bot.send_message.assert_not_awaited()
