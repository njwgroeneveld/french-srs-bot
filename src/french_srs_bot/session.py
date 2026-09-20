"""Practice flow: which card comes next, handling answers, daily goal and streak."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
from fsrs import Scheduler

from . import db, srs
from .config import Settings, settings_for
from .grading import Grade, GradeResult, grade
from .models import BotState, CardView, DayStats


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
    more_available: bool  # a due review or an allowed new card exists right now


@dataclass(frozen=True)
class Answered:
    result: GradeResult
    card: CardView
    next: Ask | Summary
    goal_just_reached: bool  # this very answer took the user over the daily goal
    review_id: int | None = None  # the review this answer produced, for the override button


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


def today_stats(conn: psycopg.Connection, settings: Settings, user_id: int, now: datetime) -> DayStats:
    start, end = day_bounds(now, settings.timezone)
    return db.day_stats(conn, user_id=user_id, day_start=start, day_end=end)


def _eligible_now(
    conn: psycopg.Connection,
    settings: Settings,
    user_id: int,
    now: datetime,
    kind: str | None = None,
) -> tuple[list[CardView], CardView | None]:
    """Due cards and the new card allowed right now (None when the new-card allowance is used up).

    Asking for one kind (/grammar) sets `kind` and lifts that allowance: a day spent on words
    would otherwise leave the sentences unreachable until tomorrow, which reads as "there is
    no grammar" rather than "not today".
    """
    start, end = day_bounds(now, settings.timezone)
    stats = db.day_stats(conn, user_id=user_id, day_start=start, day_end=end)
    due = db.due_cards(conn, user_id=user_id, now=now, day_start=start, kind=kind)
    new_allowed = max(settings.daily_new, settings.daily_goal - (stats.reviews + len(due)))
    new_card = (
        db.next_new_card(conn, user_id=user_id, now=now, day_start=start, kind=kind)
        if kind is not None or stats.new < new_allowed
        else None
    )
    return due, new_card


def needs_intro(card: CardView) -> bool:
    """Whether to show the word and its translation before quizzing it.

    Only when the *word* is new, not when only the direction is. A NL->FR card is offered
    exclusively once its FR->NL sibling was learned on an earlier day (see db.next_new_card),
    so the word has been seen: introducing it again would hand over the answer.
    """
    return card.direction == "fr_nl"


def pick_next(
    conn: psycopg.Connection,
    settings: Settings,
    user_id: int,
    now: datetime,
    *,
    first_of_batch: bool,
    kind: str | None = None,
) -> Ask | None:
    due, new_card = _eligible_now(conn, settings, user_id, now, kind)
    if first_of_batch and new_card is not None:
        return Ask(new_card, is_new=needs_intro(new_card))
    if due:
        return Ask(due[0], is_new=False)
    if new_card is not None:
        return Ask(new_card, is_new=needs_intro(new_card))
    return None


def summary(conn: psycopg.Connection, settings: Settings, user_id: int, now: datetime) -> Summary:
    start, end = day_bounds(now, settings.timezone)
    stats = db.day_stats(conn, user_id=user_id, day_start=start, day_end=end)
    due, new_card = _eligible_now(conn, settings, user_id, now)
    totals = db.daily_totals(
        conn, user_id=user_id, timezone_name=settings.timezone.key, since=start - timedelta(days=366)
    )
    return Summary(
        done_today=stats.total,
        goal=settings.daily_goal,
        due_now=len(due),
        streak=compute_streak(totals, start.date(), settings.daily_goal),
        more_available=bool(due) or new_card is not None,
    )


def start_batch(
    conn: psycopg.Connection,
    settings: Settings,
    user_id: int,
    now: datetime,
    kind: str | None = None,
) -> Ask | Summary:
    db.set_batch_remaining(conn, settings.batch_size, user_id=user_id, kind=kind)
    state = db.get_bot_state(conn, user_id=user_id)
    if state.pending_card_id is not None:
        pending = db.get_card(conn, state.pending_card_id, user_id=user_id)
        if pending is not None:
            return Ask(pending, is_new=False)
    return pick_next(conn, settings, user_id, now, first_of_batch=True, kind=kind) or summary(
        conn, settings, user_id, now
    )


def mark_asked(conn: psycopg.Connection, card: CardView, now: datetime, *, user_id: int) -> bool:
    """Make `card` the pending card. False when another card became pending meanwhile (don't ask it).

    A card that skipped the intro starts here: for a brand-new word `acknowledge_intro` has
    already set introduced_at, but a card without an intro would otherwise stay unstarted and
    never come back.
    """
    if not db.set_pending_if_none(conn, card.card_id, now, user_id=user_id):
        if db.get_bot_state(conn, user_id=user_id).pending_card_id != card.card_id:
            return False
    if card.introduced_at is None:
        db.introduce_card(conn, card.card_id, now, user_id=user_id)
    return True


def acknowledge_intro(
    conn: psycopg.Connection, settings: Settings, card_id: int, now: datetime, *, user_id: int
) -> CardView | None:
    """User pressed "Begrepen". Returns the card to quiz, or None for a stale button."""
    card = db.get_card(conn, card_id, user_id=user_id)
    if card is None:
        return None
    pending = db.get_bot_state(conn, user_id=user_id).pending_card_id
    if pending not in (None, card_id):
        return None
    if pending != card_id:
        # Not the pending card: only accept it if it is exactly the new card that may be introduced now.
        _due, new_card = _eligible_now(conn, settings, user_id, now)
        if new_card is None or new_card.card_id != card_id:
            return None
    with conn.transaction():
        db.introduce_card(conn, card_id, now, user_id=user_id)
        db.set_pending(conn, card_id, now, user_id=user_id)
    return db.get_card(conn, card_id, user_id=user_id)


def answer(
    conn: psycopg.Connection, settings: Settings, scheduler: Scheduler, text: str, now: datetime, *, user_id: int
) -> Answered | None:
    """Grade `text` for the pending card. Returns None when no card is waiting for an answer."""
    state = db.get_bot_state(conn, user_id=user_id)
    if state.pending_card_id is None:
        return None
    card = db.get_card(conn, state.pending_card_id, user_id=user_id)
    if card is None:
        return None
    result = grade(text, card.accepted, card.answer_lang, settings.typo_min_length)
    return _record(conn, settings, scheduler, card, text, result, now, state, user_id=user_id)


def _record(
    conn: psycopg.Connection,
    settings: Settings,
    scheduler: Scheduler,
    card: CardView,
    text: str,
    result: GradeResult,
    now: datetime,
    state: BotState,
    *,
    user_id: int,
) -> Answered:
    """Schedule the card, log the review and work out what comes next."""
    new_state, rating = srs.review(scheduler, card.srs, result.grade, now)
    review_id = db.save_review(
        conn,
        card_id=card.card_id,
        answer=text,
        grade=result.grade.value,
        rating=int(rating),
        due_before=card.srs.due if card.srs else None,
        prev_state=card.srs,
        new_state=new_state,
        now=now,
        user_id=user_id,
    )
    # Exactly equal, not >=: every answer adds one, so this is true on the crossing answer
    # only. That keeps the nudge to the other user to one message per day.
    just_reached = today_stats(conn, settings, user_id, now).total == settings.daily_goal
    next_step: Ask | Summary | None = None
    if state.batch_remaining - 1 > 0:
        next_step = pick_next(
            conn, settings, user_id, now, first_of_batch=False, kind=state.batch_kind
        )
    return Answered(
        result=result,
        card=card,
        next=next_step or summary(conn, settings, user_id, now),
        goal_just_reached=just_reached,
        review_id=review_id,
    )


def answer_choice(
    conn: psycopg.Connection,
    settings: Settings,
    scheduler: Scheduler,
    choice: str,
    now: datetime,
    *,
    user_id: int,
) -> Answered | None:
    """Grade a tapped choice for the pending gap card. None when no gap card is waiting."""
    state = db.get_bot_state(conn, user_id=user_id)
    if state.pending_card_id is None:
        return None
    card = db.get_card(conn, state.pending_card_id, user_id=user_id)
    if card is None or card.direction != "gap":
        return None
    correct = choice == card.gap_answer
    result = GradeResult(
        grade=Grade.CORRECT if correct else Grade.WRONG,
        reason="exact" if correct else "wrong",
        expected=card.gap_answer,
    )
    return _record(conn, settings, scheduler, card, choice, result, now, state, user_id=user_id)


def override(
    conn: psycopg.Connection,
    settings: Settings,
    scheduler: Scheduler,
    review_id: int,
    now: datetime,
    *,
    user_id: int,
) -> CardView | None:
    """Accept the answer of `review_id` after all. None when the button no longer applies.

    The card is rescheduled from the state it had *before* that review, so it ends up exactly
    where a correct answer would have put it - not one review further along.
    """
    row = db.review_for_override(conn, review_id, user_id=user_id)
    if row is None:
        return None
    prev_state = db.state_from_json(row["prev_state"])
    new_state, rating = srs.review(scheduler, prev_state, Grade.CORRECT, now)
    db.apply_override(
        conn,
        review_id=row["id"],
        card_id=row["card_id"],
        item_id=row["item_id"],
        answer=row["answer"],
        rating=int(rating),
        new_state=new_state,
        user_id=user_id,
    )
    return db.get_card(conn, row["card_id"], user_id=user_id)


def scheduled_batch(conn: psycopg.Connection, settings: Settings, user_id: int, now: datetime) -> Ask | None:
    """Batch for a scheduled time: nothing when the goal is reached or no card is available."""
    if today_stats(conn, settings, user_id, now).total >= settings.daily_goal:
        return None
    step = start_batch(conn, settings, user_id, now)
    return step if isinstance(step, Ask) else None


def reminder_needed(conn: psycopg.Connection, settings: Settings, user_id: int, now: datetime) -> DayStats | None:
    stats = today_stats(conn, settings, user_id, now)
    return stats if stats.total < settings.daily_goal else None


@dataclass(frozen=True)
class Standing:
    name: str
    cards: int  # reviews this week
    days_reached: int  # days this week this person met their own goal
    goal: int  # their goal, which need not be everyone's


def week_standings(conn: psycopg.Connection, base: Settings, now: datetime) -> list[Standing]:
    """One row per user over the last seven days, ordered as the users were registered.

    Everyone is measured against their own goal: with different paces a shared number
    would be meaningless for at least one of them.
    """
    start, _end = day_bounds(now, base.timezone)
    since = start - timedelta(days=6)
    standings = []
    for user in db.all_users(conn):
        settings = settings_for(base, user)
        totals = db.daily_totals(
            conn, user_id=user.id, timezone_name=settings.timezone.key, since=since
        )
        standings.append(
            Standing(
                name=user.name,
                cards=sum(totals.values()),
                days_reached=sum(1 for total in totals.values() if total >= settings.daily_goal),
                goal=settings.daily_goal,
            )
        )
    return standings
