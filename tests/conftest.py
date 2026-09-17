import os
from datetime import time, timedelta
from zoneinfo import ZoneInfo

import pytest
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict

from french_srs_bot import db
from french_srs_bot.config import Settings

load_dotenv()


@pytest.fixture
def conn():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if "test" not in conninfo_to_dict(url).get("dbname", ""):
        pytest.fail("TEST_DATABASE_URL must point to a test database: this fixture drops schema french")
    with db.connect(url) as connection:
        connection.execute("DROP SCHEMA IF EXISTS french CASCADE")
        db.run_migrations(connection)
        yield connection


@pytest.fixture
def settings():
    return Settings(
        timezone=ZoneInfo("Europe/Amsterdam"),
        daily_goal=10,
        daily_new=3,
        batch_size=4,
        batch_times=(time(8), time(13), time(19)),
        reminder_time=time(20, 30),
        learning_steps=(timedelta(hours=4), timedelta(hours=4), timedelta(days=1)),
        relearning_steps=(timedelta(hours=4),),
        typo_min_length=4,
    )


@pytest.fixture
def add_items(conn):
    """Create a theme with the given (french, dutch) pairs; returns their item ids in order."""

    def _add(pairs, theme_ref="theme/1", theme_position=1):
        theme_id = db.upsert_theme(
            conn, source="test", source_ref=theme_ref, level="A0", name="Test", position=theme_position
        )
        return [
            db.upsert_item(
                conn,
                theme_id=theme_id,
                position=position,
                french=[french],
                dutch=[dutch],
                english=None,
                gender=None,
                hint=None,
            )
            for position, (french, dutch) in enumerate(pairs, start=1)
        ]

    return _add
