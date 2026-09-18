"""Entry point: python -m french_srs_bot"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import psycopg

from . import db
from .bot import build_application
from .config import load_secrets, load_settings
from .scheduler import register_jobs


def migrate_with_retries(
    database_url: str, attempts: int = 8, delay: float = 15.0, max_delay: float = 300.0
) -> list[str]:
    """Run migrations, retrying while the database is unreachable (the node's Wi-Fi drops out).

    The delay doubles after every failure. Hammering is what triggers Supabase's connection circuit
    breaker ("too many authentication failures"), and every blocked attempt keeps it closed longer.
    """
    for attempt in range(1, attempts + 1):
        try:
            with db.connect(database_url) as conn:
                return db.run_migrations(conn)
        except psycopg.OperationalError as exc:
            if attempt == attempts:
                raise
            wait = min(delay * 2 ** (attempt - 1), max_delay)
            logging.warning(
                "database unreachable at startup (attempt %s/%s: %s), retrying in %ss",
                attempt, attempts, str(exc).splitlines()[0], int(wait),
            )
            time.sleep(wait)
    raise AssertionError("unreachable")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # do not log request URLs, they contain the token
    # Startup retries against Telegram are logged here, so a slow network is visible instead of silent.
    logging.getLogger("telegram.ext.Updater").setLevel(logging.INFO)
    settings = load_settings(Path(os.environ.get("SETTINGS_PATH", "settings.yaml")))
    secrets = load_secrets()
    applied = migrate_with_retries(secrets.database_url)
    logging.info("migrations applied: %s", applied or "none")
    app = build_application(settings, secrets)
    register_jobs(app, settings)
    # bootstrap_retries=-1: keep retrying when Telegram is unreachable at startup (flaky node Wi-Fi)
    # instead of crashing into CrashLoopBackOff.
    app.run_polling(allowed_updates=["message", "callback_query"], bootstrap_retries=-1)


if __name__ == "__main__":
    main()
