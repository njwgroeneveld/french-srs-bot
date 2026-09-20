from datetime import datetime, timezone

from telegram import Chat, Message, MessageEntity, Update, User
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from french_srs_bot import bot as bot_module
from french_srs_bot.bot import build_application
from french_srs_bot.config import Secrets
from french_srs_bot.scheduler import register_jobs

from conftest import USERS


def test_application_registers_handlers_and_jobs(settings):
    secrets = Secrets(telegram_bot_token="123:fake", telegram_user_id=42, database_url="postgresql://unused")
    app = build_application(settings, secrets, USERS)
    register_jobs(app, settings)

    handlers = app.handlers[0]
    commands = {cmd for h in handlers if isinstance(h, CommandHandler) for cmd in h.commands}
    assert commands == {"start", "practice", "stand", "volgorde", "help"}
    assert any(isinstance(h, CallbackQueryHandler) for h in handlers)
    assert any(isinstance(h, MessageHandler) for h in handlers)
    assert sorted(job.name for job in app.job_queue.jobs()) == [
        "batch-0800", "batch-1300", "batch-1900", "reminder",
    ]


def _message(text, user_id=42):
    entities = [MessageEntity(MessageEntity.BOT_COMMAND, 0, len(text))] if text.startswith("/") else None
    return Message(
        message_id=1,
        date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        chat=Chat(user_id, Chat.PRIVATE),
        from_user=User(user_id, "Niels", False),
        text=text,
        entities=entities,
    )


def test_handlers_ignore_edited_messages(settings):
    secrets = Secrets(telegram_bot_token="123:fake", telegram_user_id=42, database_url="postgresql://unused")
    handlers = build_application(settings, secrets, USERS).handlers[0]
    text_handler = next(h for h in handlers if isinstance(h, MessageHandler))
    commands = [h for h in handlers if isinstance(h, CommandHandler)]

    assert text_handler.check_update(Update(1, message=_message("bonjour")))
    assert not text_handler.check_update(Update(2, edited_message=_message("bonjour")))
    # CommandHandler.check_update needs an initialized bot (get_me), so check its filters directly.
    for handler in commands:
        assert handler.filters.check_update(Update(3, message=_message("/practice")))
        assert not handler.filters.check_update(Update(4, edited_message=_message("/practice")))


def test_every_known_user_passes_the_filter_and_a_stranger_does_not(settings):
    secrets = Secrets(telegram_bot_token="123:fake", telegram_user_id=42, database_url="postgresql://unused")
    handlers = build_application(settings, secrets, USERS).handlers[0]
    text_handler = next(h for h in handlers if isinstance(h, MessageHandler))

    assert text_handler.check_update(Update(1, message=_message("bonjour", user_id=42)))
    assert text_handler.check_update(Update(2, message=_message("bonjour", user_id=99)))
    assert not text_handler.check_update(Update(3, message=_message("bonjour", user_id=7)))


def test_the_telegram_menu_only_offers_registered_commands(settings):
    """Telegram shows this list when you type "/": it must not promise commands that do not exist."""
    secrets = Secrets(telegram_bot_token="123:fake", telegram_user_id=42, database_url="postgresql://unused")
    handlers = build_application(settings, secrets, USERS).handlers[0]
    registered = {cmd for h in handlers if isinstance(h, CommandHandler) for cmd in h.commands}

    assert {entry.command for entry in bot_module.MENU} <= registered
    assert all(entry.description for entry in bot_module.MENU)
