"""Load settings.yaml and secrets from environment variables."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

_DURATION = re.compile(r"^(\d+)([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def parse_duration(text: str) -> timedelta:
    match = _DURATION.match(text.strip())
    if not match:
        raise ValueError(f"invalid duration {text!r}, use e.g. 30m, 4h or 1d")
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


def parse_clock(text: str) -> time:
    hours, minutes = text.strip().split(":")
    return time(int(hours), int(minutes))


@dataclass(frozen=True)
class Settings:
    timezone: ZoneInfo
    daily_goal: int
    daily_new: int
    batch_size: int
    batch_times: tuple[time, ...]
    reminder_time: time
    learning_steps: tuple[timedelta, ...]
    relearning_steps: tuple[timedelta, ...]
    typo_min_length: int


def load_settings(path: Path) -> Settings:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Settings(
        timezone=ZoneInfo(raw["timezone"]),
        daily_goal=int(raw["daily_goal"]),
        daily_new=int(raw["daily_new"]),
        batch_size=int(raw["batch_size"]),
        batch_times=tuple(parse_clock(t) for t in raw["batch_times"]),
        reminder_time=parse_clock(raw["reminder_time"]),
        learning_steps=tuple(parse_duration(s) for s in raw["learning_steps"]),
        relearning_steps=tuple(parse_duration(s) for s in raw["relearning_steps"]),
        typo_min_length=int(raw["typo_min_length"]),
    )


@dataclass(frozen=True)
class Secrets:
    telegram_bot_token: str
    telegram_user_id: int
    database_url: str


def load_secrets(env: Mapping[str, str] = os.environ) -> Secrets:
    names = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID", "DATABASE_URL")
    missing = [name for name in names if not env.get(name)]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)}")
    return Secrets(
        telegram_bot_token=env["TELEGRAM_BOT_TOKEN"],
        telegram_user_id=int(env["TELEGRAM_USER_ID"]),
        database_url=env["DATABASE_URL"],
    )
