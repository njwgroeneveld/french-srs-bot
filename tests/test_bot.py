import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import psycopg
from telegram.error import BadRequest, TelegramError

from french_srs_bot import bot, db, messages, scheduler, session
from french_srs_bot.config import Secrets
from french_srs_bot.grading import Grade, GradeResult
from french_srs_bot.models import BotState, CardView, DayStats

from conftest import USERS

NOW = datetime(2026, 9, 18, 6, 0, tzinfo=timezone.utc)
CARD = CardView(
    card_id=5, item_id=1, direction="fr_nl", french=["le chien"], dutch=["de hond"],
    gender=None, hint=None, introduced_at=NOW, srs=None,
)
GAP_CARD = CardView(
    card_id=7, item_id=2, direction="gap",
    french=["Je prends le vélo pour aller au travail."],
    dutch=["Ik neem de fiets om naar mijn werk te gaan"],
    gender=None, hint=None, introduced_at=NOW, srs=None,
    kind="grammar", sentence="Je prends le vélo ___ aller au travail.",
    gap_answer="pour", rule="pour + infinitief = doel",
    choices=["pour", "parce que", "mais", "avec", "sans"],
)


def make_context(settings):
    secrets = Secrets(telegram_bot_token="123:fake", telegram_user_id=42, database_url="postgresql://unused")
    context = MagicMock()
    context.application.bot_data = {"deps": bot.Deps(settings=settings, secrets=secrets, scheduler=None)}
    context.bot = AsyncMock()
    return context


def test_stale_intro_button_gets_feedback_and_is_removed(settings, monkeypatch):
    monkeypatch.setattr(bot.db, "connect", lambda url: nullcontext(object()))
    monkeypatch.setattr(bot.db, "user_by_telegram_id", lambda conn, telegram_user_id: USERS[0])
    monkeypatch.setattr(bot.session, "acknowledge_intro", lambda *args, **kwargs: None)
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
    monkeypatch.setattr(bot.session, "mark_asked", lambda conn, card, now, *, user_id: False)
    telegram_bot = AsyncMock()
    asyncio.run(bot.send_step(telegram_bot, 42, object(), session.Ask(CARD, is_new=False), NOW, settings, 1))
    telegram_bot.send_message.assert_not_awaited()


def test_send_step_asks_when_pending_could_be_set(settings, monkeypatch):
    monkeypatch.setattr(bot.session, "mark_asked", lambda conn, card, now, *, user_id: True)
    telegram_bot = AsyncMock()
    asyncio.run(bot.send_step(telegram_bot, 42, object(), session.Ask(CARD, is_new=False), NOW, settings, 1))
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
        goal_just_reached=False,
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
        goal_just_reached=False,
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


def test_the_scheduled_batch_runs_for_every_user(settings, monkeypatch):
    asked = []
    monkeypatch.setattr(scheduler.db, "connect", lambda url: nullcontext(object()))
    monkeypatch.setattr(scheduler.db, "all_users", lambda conn: USERS)
    monkeypatch.setattr(
        scheduler.session, "scheduled_batch",
        lambda conn, s, user_id, now: asked.append(user_id) or None,
    )
    context = make_context(settings)

    asyncio.run(scheduler.batch_job(context))

    assert asked == [1, 2]


def test_practice_really_asks_a_question(conn, settings, user, add_items, monkeypatch):
    """End to end through the real session and db layers: this is what catches signature drift
    between bot.py and session.py, which unit tests with a mocked session never see."""
    add_items([("le chien", "de hond")])
    monkeypatch.setattr(bot.db, "connect", lambda url: nullcontext(conn))
    update = MagicMock()
    update.effective_user.id = user.telegram_user_id
    update.effective_chat.id = user.telegram_user_id
    context = make_context(settings)

    asyncio.run(bot.on_practice(update, context))

    context.bot.send_message.assert_awaited()
    assert "le chien" in context.bot.send_message.await_args.kwargs["text"]


def test_only_the_others_are_nudged_when_someone_reaches_their_goal(settings, monkeypatch):
    monkeypatch.setattr(bot.db, "all_users", lambda conn: USERS)
    monkeypatch.setattr(
        bot.session, "today_stats",
        lambda conn, s, user_id, now: DayStats(total=12, new=0, reviews=12),
    )
    telegram_bot = AsyncMock()

    asyncio.run(bot.nudge_others(telegram_bot, object(), USERS[0], settings, NOW))

    # Only the other one hears about it, not the person who just finished.
    assert [call.kwargs["chat_id"] for call in telegram_bot.send_message.await_args_list] == [
        USERS[1].telegram_user_id
    ]


def test_nobody_is_nudged_when_there_is_only_one_user(settings, monkeypatch):
    monkeypatch.setattr(bot.db, "all_users", lambda conn: USERS[:1])
    telegram_bot = AsyncMock()

    asyncio.run(bot.nudge_others(telegram_bot, object(), USERS[0], settings, NOW))

    telegram_bot.send_message.assert_not_awaited()


def test_a_goal_crossing_answer_nudges_the_others(conn, settings, user, add_items, monkeypatch):
    """Through the real handler: answering the card that crosses the goal must reach the other."""
    conn.execute("INSERT INTO french.users (telegram_user_id, name) VALUES (99, 'Inga')")
    add_items([("le chien", "de hond")])
    monkeypatch.setattr(bot.db, "connect", lambda url: nullcontext(conn))
    nudged = []
    monkeypatch.setattr(bot, "nudge_others", AsyncMock(side_effect=lambda *a: nudged.append(a[2])))
    # Make the very next answer the crossing one.
    monkeypatch.setattr(
        bot.session, "answer",
        lambda *args, **kwargs: session.Answered(
            result=GradeResult(grade=Grade.CORRECT, reason="exact", expected="de hond"),
            card=CARD,
            next=session.Summary(done_today=1, goal=1, due_now=0, streak=1, more_available=False),
            goal_just_reached=True,
        ),
    )
    update = MagicMock()
    update.effective_user.id = user.telegram_user_id
    update.effective_chat.id = user.telegram_user_id
    update.message.text = "de hond"

    asyncio.run(bot.on_text(update, make_context(settings)))

    assert [u.id for u in nudged] == [user.id]


def test_gap_question_carries_one_button_per_choice(settings, monkeypatch):
    def fail(text, tts_settings):
        raise AssertionError("a sentence with a hole in it must not be read out loud")

    monkeypatch.setattr(bot.tts, "synthesize", fail)
    telegram_bot = AsyncMock()

    asyncio.run(bot.send_question(telegram_bot, 42, object(), GAP_CARD, enable_tts(settings)))

    telegram_bot.send_voice.assert_not_awaited()
    markup = telegram_bot.send_message.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert sorted(button.text for button in buttons) == sorted(GAP_CARD.choices)
    assert all(button.callback_data.startswith(f"gap:{GAP_CARD.card_id}:") for button in buttons)


def test_the_callback_data_survives_the_shuffle(settings):
    markup = bot.gap_markup(GAP_CARD)
    buttons = [button for row in markup.inline_keyboard for button in row]
    for button in buttons:
        assert bot.choice_from_callback(GAP_CARD, button.callback_data) == button.text
    assert bot.choice_from_callback(GAP_CARD, f"gap:{GAP_CARD.card_id}:99") is None


def test_gap_feedback_speaks_the_completed_sentence(settings, monkeypatch):
    spoken = []
    monkeypatch.setattr(bot.tts, "synthesize", lambda text, s: spoken.append(text) or b"OggS-fake")
    monkeypatch.setattr(bot.db, "save_voice", lambda conn, **kwargs: None)
    answered = session.Answered(
        result=GradeResult(grade=Grade.CORRECT, reason="exact", expected="pour"),
        card=GAP_CARD,
        next=session.Summary(done_today=1, goal=10, due_now=0, streak=0, more_available=False),
        goal_just_reached=False,
    )
    telegram_bot = AsyncMock()
    telegram_bot.send_voice.return_value.voice.file_id = "AwACAgQAAxk"

    asyncio.run(bot.send_feedback(telegram_bot, 42, object(), answered, enable_tts(settings)))

    assert spoken == ["Je prends le vélo pour aller au travail."]


def test_a_wrong_translation_offers_the_override_button(settings):
    translate = replace(GAP_CARD, direction="translate", card_id=8)
    answered = session.Answered(
        result=GradeResult(grade=Grade.WRONG, reason="wrong",
                           expected="Ik neem de fiets om naar mijn werk te gaan"),
        card=translate,
        next=session.Summary(done_today=1, goal=10, due_now=0, streak=0, more_available=False),
        goal_just_reached=False,
        review_id=123,
    )
    telegram_bot = AsyncMock()

    asyncio.run(bot.send_feedback(telegram_bot, 42, object(), answered, settings))

    markup = telegram_bot.send_message.await_args.kwargs["reply_markup"]
    button = markup.inline_keyboard[0][0]
    assert (button.text, button.callback_data) == (messages.BUTTON_OVERRIDE, "ok:123")


def test_a_correct_translation_has_no_override_button(settings):
    translate = replace(GAP_CARD, direction="translate", card_id=8)
    answered = session.Answered(
        result=GradeResult(grade=Grade.CORRECT, reason="exact",
                           expected="Ik neem de fiets om naar mijn werk te gaan"),
        card=translate,
        next=session.Summary(done_today=1, goal=10, due_now=0, streak=0, more_available=False),
        goal_just_reached=False,
        review_id=123,
    )
    telegram_bot = AsyncMock()

    asyncio.run(bot.send_feedback(telegram_bot, 42, object(), answered, settings))

    assert telegram_bot.send_message.await_args.kwargs["reply_markup"] is None


def test_a_grammar_card_without_choices_is_not_asked(settings, caplog):
    broken = replace(GAP_CARD, choices=[])
    telegram_bot = AsyncMock()

    asyncio.run(bot.send_question(telegram_bot, 42, object(), broken, settings))

    telegram_bot.send_message.assert_not_awaited()
    assert "choices" in caplog.text


def test_a_choiceless_gap_card_does_not_strand_the_batch(
    conn, settings, user, add_items, add_grammar, monkeypatch
):
    """An unusable card must not stay pending: every later /practice would answer nothing at all.

    Through the real session and db layers, because the stranding was exactly in the seam
    between send_step (which makes a card pending) and send_question (which refused it).
    """
    (grammar_item,) = add_grammar(
        [("Je prends le vélo ___ aller au travail.", "pour", ["Ik neem de fiets"])],
        theme_ref="theme/g1", theme_position=1, choices=(),
    )
    (vocab_item,) = add_items([("le chien", "de hond")], theme_ref="theme/v1", theme_position=2)
    gap_card = card_of(conn, grammar_item, "gap", user.id)
    vocab_card = card_of(conn, vocab_item, "fr_nl", user.id)
    # A review that was already waiting, so there is a next card to move on to.
    real_now = datetime.now(timezone.utc)
    conn.execute(
        """
        UPDATE french.cards SET introduced_at = %s, due = %s, fsrs_state = 2, step = NULL,
               stability = 5.0, difficulty = 5.0, last_review = %s
        WHERE id = %s
        """,
        (real_now - timedelta(days=1), real_now - timedelta(hours=1), real_now - timedelta(days=1),
         vocab_card),
    )
    monkeypatch.setattr(bot.db, "connect", lambda url: nullcontext(conn))
    update = MagicMock()
    update.effective_user.id = user.telegram_user_id
    update.effective_chat.id = user.telegram_user_id
    context = make_context(settings)

    asyncio.run(bot.on_practice(update, context))

    state = db.get_bot_state(conn, user_id=user.id)
    assert state.pending_card_id != gap_card
    assert state.pending_card_id == vocab_card
    assert "le chien" in context.bot.send_message.await_args.kwargs["text"]


def card_of(conn, item_id, direction, user_id):
    return conn.execute(
        "SELECT id FROM french.cards WHERE item_id = %s AND direction = %s AND user_id = %s",
        (item_id, direction, user_id),
    ).fetchone()["id"]


def test_a_database_error_on_a_gap_button_is_reported_once(settings, monkeypatch):
    """Answering the same callback query twice raises BadRequest, which would swallow the message."""
    monkeypatch.setattr(bot.db, "connect", lambda url: nullcontext(object()))
    monkeypatch.setattr(bot.db, "user_by_telegram_id", lambda conn, telegram_user_id: USERS[0])
    monkeypatch.setattr(
        bot.db, "get_bot_state",
        lambda conn, *, user_id: BotState(pending_card_id=GAP_CARD.card_id, batch_remaining=4),
    )
    monkeypatch.setattr(bot.db, "get_card", lambda conn, card_id, *, user_id: GAP_CARD)

    def boom(*args, **kwargs):
        raise psycopg.OperationalError("connection gone")

    monkeypatch.setattr(bot.session, "answer_choice", boom)
    answered = []

    async def answer(text=None):
        if answered:
            raise BadRequest("Query is too old and response timeout expired")
        answered.append(text)

    update = MagicMock()
    update.effective_user.id = 42
    update.effective_chat.id = 42
    query = update.callback_query
    query.data = f"gap:{GAP_CARD.card_id}:0"
    query.answer = answer
    context = make_context(settings)

    asyncio.run(bot.on_gap_pressed(update, context))

    assert answered == [None]
    assert context.bot.send_message.await_args.kwargs["text"] == messages.database_unavailable()


def test_a_database_error_on_the_override_button_is_reported(settings, monkeypatch):
    monkeypatch.setattr(bot.db, "connect", lambda url: nullcontext(object()))
    monkeypatch.setattr(bot.db, "user_by_telegram_id", lambda conn, telegram_user_id: USERS[0])

    def boom(*args, **kwargs):
        raise psycopg.OperationalError("connection gone")

    monkeypatch.setattr(bot.session, "override", boom)
    update = MagicMock()
    update.effective_user.id = 42
    update.effective_chat.id = 42
    query = update.callback_query
    query.data = "ok:123"
    query.answer = AsyncMock()
    context = make_context(settings)

    asyncio.run(bot.on_override_pressed(update, context))

    assert context.bot.send_message.await_args.kwargs["text"] == messages.database_unavailable()


def test_grammar_says_so_when_there_are_no_sentences(conn, settings, user, add_items, monkeypatch):
    """A generic "setje klaar" is confusing when you asked for grammar in particular."""
    add_items([("le chien", "de hond")])  # words, but not a single sentence
    monkeypatch.setattr(bot.db, "connect", lambda url: nullcontext(conn))
    update = MagicMock()
    update.effective_user.id = user.telegram_user_id
    update.effective_chat.id = user.telegram_user_id
    context = make_context(settings)

    asyncio.run(bot.on_grammar(update, context))

    context.bot.send_message.assert_awaited_once()
    assert context.bot.send_message.await_args.kwargs["text"] == messages.no_grammar_due()


def test_grammar_still_asks_when_a_sentence_is_waiting(conn, settings, user, add_grammar, monkeypatch):
    add_grammar([("Je prends le vélo ___ aller au travail.", "pour", ["Ik neem de fiets"])])
    monkeypatch.setattr(bot.db, "connect", lambda url: nullcontext(conn))
    update = MagicMock()
    update.effective_user.id = user.telegram_user_id
    update.effective_chat.id = user.telegram_user_id
    context = make_context(settings)

    asyncio.run(bot.on_grammar(update, context))

    markup = context.bot.send_message.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert sorted(button.text for button in buttons) == ["mais", "parce que", "pour"]


def test_a_gap_button_for_another_card_changes_nothing(conn, settings, user, add_grammar, monkeypatch):
    """Only the pending card may be graded: an older keyboard must not answer today's question."""
    items = add_grammar(
        [("Je prends le vélo ___ aller au travail.", "pour", ["Ik neem de fiets"]),
         ("Le métro est pratique, ___ il est cher.", "mais", ["De metro is handig"])],
    )
    pending = card_of(conn, items[0], "gap", user.id)
    other = card_of(conn, items[1], "gap", user.id)
    db.set_pending(conn, pending, datetime.now(timezone.utc), user_id=user.id)
    monkeypatch.setattr(bot.db, "connect", lambda url: nullcontext(conn))
    update = MagicMock()
    update.effective_user.id = user.telegram_user_id
    update.effective_chat.id = user.telegram_user_id
    query = update.callback_query
    query.data = f"gap:{other}:0"
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    context = make_context(settings)

    asyncio.run(bot.on_gap_pressed(update, context))

    query.answer.assert_awaited_once_with(messages.stale_intro())
    assert conn.execute("SELECT count(*) AS n FROM french.reviews").fetchone()["n"] == 0
    assert db.get_bot_state(conn, user_id=user.id).pending_card_id == pending
    context.bot.send_message.assert_not_awaited()
