from datetime import time, timedelta
from pathlib import Path

import pytest

from french_srs_bot.config import load_secrets, load_settings, parse_clock, parse_duration

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("30m", timedelta(minutes=30)), ("4h", timedelta(hours=4)), ("1d", timedelta(days=1))],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


def test_parse_duration_rejects_garbage():
    with pytest.raises(ValueError):
        parse_duration("4 hours")


def test_parse_clock():
    assert parse_clock("08:05") == time(8, 5)


def test_load_settings_from_repo_file():
    settings = load_settings(REPO_ROOT / "settings.yaml")
    assert settings.timezone.key == "Europe/Amsterdam"
    assert settings.daily_goal == 15
    assert settings.daily_new == 8
    assert settings.batch_times == (time(8, 0), time(13, 0), time(19, 0))
    assert settings.learning_steps == (timedelta(hours=4), timedelta(hours=4), timedelta(days=1))


def test_load_secrets_reports_all_missing_names():
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, DATABASE_URL"):
        load_secrets({})


def test_load_secrets():
    secrets = load_secrets(
        {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_USER_ID": "42", "DATABASE_URL": "postgresql://x"}
    )
    assert secrets.telegram_user_id == 42
