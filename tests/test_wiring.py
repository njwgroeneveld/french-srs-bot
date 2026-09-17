from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from french_srs_bot.bot import build_application
from french_srs_bot.config import Secrets
from french_srs_bot.scheduler import register_jobs


def test_application_registers_handlers_and_jobs(settings):
    secrets = Secrets(telegram_bot_token="123:fake", telegram_user_id=42, database_url="postgresql://unused")
    app = build_application(settings, secrets)
    register_jobs(app, settings)

    handlers = app.handlers[0]
    commands = {cmd for h in handlers if isinstance(h, CommandHandler) for cmd in h.commands}
    assert commands == {"start", "practice"}
    assert any(isinstance(h, CallbackQueryHandler) for h in handlers)
    assert any(isinstance(h, MessageHandler) for h in handlers)
    assert sorted(job.name for job in app.job_queue.jobs()) == [
        "batch-0800", "batch-1300", "batch-1900", "reminder",
    ]
