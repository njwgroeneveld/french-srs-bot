from datetime import time, timedelta
from pathlib import Path

import pytest

from french_srs_bot.config import TtsSettings, load_secrets, load_settings, parse_clock, parse_duration

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
    """The shipped settings.yaml parses and holds together.

    Deliberately no assertions on the tuning numbers themselves (goal, new, batch size):
    those are meant to be changed without breaking the build. What is asserted are the
    relations between them, which a careless edit does break.
    """
    settings = load_settings(REPO_ROOT / "settings.yaml")

    assert settings.timezone.key == "Europe/Amsterdam"
    assert settings.daily_goal > 0
    assert 0 < settings.daily_new <= settings.daily_goal
    assert settings.batch_size > 0
    assert settings.learning_steps and settings.relearning_steps

    # The scheduled batches must be able to reach the goal on their own; otherwise the
    # evening reminder fires every single day no matter how well you did.
    assert settings.batch_size * len(settings.batch_times) >= settings.daily_goal


def test_the_voice_in_settings_is_the_one_baked_into_the_image():
    # The model is downloaded at build time by name. Change the voice here and forget the
    # Dockerfile, and the file is simply missing at runtime: every question falls back to
    # text, silently. Cheaper to catch it here.
    settings = load_settings(REPO_ROOT / "settings.yaml")
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert f"ARG PIPER_VOICE={settings.tts.voice}" in dockerfile


def test_tts_settings_are_loaded():
    settings = load_settings(REPO_ROOT / "settings.yaml")

    assert settings.tts.enabled is True
    assert settings.tts.voice == "fr_FR-siwis-medium"
    assert settings.tts.voices_dir == Path("/app/voices")
    assert settings.tts.length_scale > 0  # the tempo itself is Niels's to tune


def test_tts_key_changes_when_the_tempo_changes():
    # The key travels with a stored file_id: change voice or tempo and the audio
    # Telegram holds is stale and must be made again.
    base = dict(enabled=True, voice="fr_FR-siwis-medium", voices_dir=Path("/app/voices"))

    assert TtsSettings(**base, length_scale=1.0).key == "fr_FR-siwis-medium@1.0"
    assert TtsSettings(**base, length_scale=1.2).key == "fr_FR-siwis-medium@1.2"


def test_load_secrets_reports_all_missing_names():
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, DATABASE_URL"):
        load_secrets({})


def test_load_secrets():
    secrets = load_secrets(
        {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_USER_ID": "42", "DATABASE_URL": "postgresql://x"}
    )
    assert secrets.telegram_user_id == 42
