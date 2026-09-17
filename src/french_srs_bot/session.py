"""Practice flow: which card comes next, handling answers, daily goal and streak."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
from fsrs import Scheduler

from . import db, srs
from .config import Settings
from .grading import GradeResult, grade
from .models import CardView, DayStats


@dataclass(frozen=True)
class Ask:
    card: CardView
    is_new: bool  # True: show the intro with a "Begrepen" button first


@dataclass(frozen=True)
class Summary:
    done_today: int
    goal: int
    due_now: int
    streak: int


@dataclass(frozen=True)
class Answered:
    result: GradeResult
    card: CardView
    next: Ask | Summary


def day_bounds(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    local_day = now.astimezone(tz).date()
    start = datetime.combine(local_day, time.min, tzinfo=tz)
    end = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


def compute_streak(totals: dict[date, int], today: date, goal: int) -> int:
    """Consecutive days reaching the goal, ending today (or yesterday if today is not done yet)."""
    day = today if totals.get(today, 0) >= goal else today - timedelta(days=1)
    streak = 0
    while totals.get(day, 0) >= goal:
        streak += 1
        day -= timedelta(days=1)
    return streak


def today_stats(conn: psycopg.Connection, settings: Settings, now: datetime) -> DayStats:
    start, end = day_bounds(now, settings.timezone)
    return db.day_stats(conn, day_start=start, day_end=end)


def pick_next(conn: psycopg.Connection, settings: Settings, now: datetime, *, first_of_batch: bool) -> Ask | None:
    start, end = day_bounds(now, settings.timezone)
    stats = db.day_stats(conn, day_start=start, day_end=end)
    due = db.due_cards(conn, now=now, day_start=start)
    new_allowed = max(settings.daily_new, settings.daily_goal - (stats.reviews + len(due)))
    new_card = db.next_new_card(conn, now=now, day_start=start) if stats.new < new_allowed else None
    if first_of_batch and new_card is not None:
        return Ask(new_card, is_new=True)
    if due:
        return Ask(due[0], is_new=False)
    if new_card is not None:
        return Ask(new_card, is_new=True)
    return None


def summary(conn: psycopg.Connection, settings: Settings, now: datetime) -> Summary:
    start, end = day_bounds(now, settings.timezone)
    stats = db.day_stats(conn, day_start=start, day_end=end)
    due = db.due_cards(conn, now=now, day_start=start)
    totals = db.daily_totals(conn, timezone_name=settings.timezone.key, since=start - timedelta(days=366))
    return Summary(
        done_today=stats.total,
        goal=settings.daily_goal,
        due_now=len(due),
        streak=compute_streak(totals, start.date(), settings.daily_goal),
    )


def start_batch(conn: psycopg.Connection, settings: Settings, now: datetime) -> Ask | Summary:
    db.set_batch_remaining(conn, settings.batch_size)
    state = db.get_bot_state(conn)
    if state.pending_card_id is not None:
        pending = db.get_card(conn, state.pending_card_id)
        if pending is not None:
            return Ask(pending, is_new=False)
    return pick_next(conn, settings, now, first_of_batch=True) or summary(conn, settings, now)


def mark_asked(conn: psycopg.Connection, card: CardView, now: datetime) -> None:
    db.set_pending(conn, card.card_id, now)


def acknowledge_intro(conn: psycopg.Connection, card_id: int, now: datetime) -> CardView | None:
    """User pressed "Begrepen". Returns the card to quiz, or None for a stale button."""
    card = db.get_card(conn, card_id)
    if card is None:
        return None
    pending = db.get_bot_state(conn).pending_card_id
    if pending not in (None, card_id):
        return None
    if card.introduced_at is not None and pending != card_id:
        return None
    with conn.transaction():
        db.introduce_card(conn, card_id, now)
        db.set_pending(conn, card_id, now)
    return db.get_card(conn, card_id)


def answer(
    conn: psycopg.Connection, settings: Settings, scheduler: Scheduler, text: str, now: datetime
) -> Answered | None:
    """Grade `text` for the pending card. Returns None when no card is waiting for an answer."""
    state = db.get_bot_state(conn)
    if state.pending_card_id is None:
        return None
    card = db.get_card(conn, state.pending_card_id)
    if card is None:
        return None
    result = grade(text, card.accepted, card.answer_lang, settings.typo_min_length)
    new_state, rating = srs.review(scheduler, card.srs, result.grade, now)
    db.save_review(
        conn,
        card_id=card.card_id,
        answer=text,
        grade=result.grade.value,
        rating=int(rating),
        due_before=card.srs.due if card.srs else None,
        new_state=new_state,
        now=now,
    )
    next_step: Ask | Summary | None = None
    if state.batch_remaining - 1 > 0:
        next_step = pick_next(conn, settings, now, first_of_batch=False)
    return Answered(result=result, card=card, next=next_step or summary(conn, settings, now))


def scheduled_batch(conn: psycopg.Connection, settings: Settings, now: datetime) -> Ask | None:
    """Batch for a scheduled time: nothing when the goal is reached or no card is available."""
    if today_stats(conn, settings, now).total >= settings.daily_goal:
        return None
    step = start_batch(conn, settings, now)
    return step if isinstance(step, Ask) else None


def reminder_needed(conn: psycopg.Connection, settings: Settings, now: datetime) -> DayStats | None:
    stats = today_stats(conn, settings, now)
    return stats if stats.total < settings.daily_goal else None
