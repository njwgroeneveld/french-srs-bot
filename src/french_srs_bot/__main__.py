"""Entry point: python -m french_srs_bot"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import db
from .bot import build_application
from .config import load_secrets, load_settings
from .scheduler import register_jobs


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # do not log request URLs, they contain the token
    settings = load_settings(Path(os.environ.get("SETTINGS_PATH", "settings.yaml")))
    secrets = load_secrets()
    with db.connect(secrets.database_url) as conn:
        applied = db.run_migrations(conn)
    logging.info("migrations applied: %s", applied or "none")
    app = build_application(settings, secrets)
    register_jobs(app, settings)
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
