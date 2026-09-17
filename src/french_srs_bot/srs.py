"""Map grades to FSRS ratings and compute the next schedule of a card."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from fsrs import Card, Rating, Scheduler, State

from .grading import Grade
from .models import SrsState

RATING_BY_GRADE: dict[Grade, Rating] = {
    Grade.CORRECT: Rating.Good,
    Grade.ALMOST: Rating.Hard,
    Grade.HARD: Rating.Hard,
    Grade.WRONG: Rating.Again,
}


def build_scheduler(
    learning_steps: Sequence[timedelta],
    relearning_steps: Sequence[timedelta],
    enable_fuzzing: bool = True,
) -> Scheduler:
    return Scheduler(
        learning_steps=tuple(learning_steps),
        relearning_steps=tuple(relearning_steps),
        enable_fuzzing=enable_fuzzing,
    )


def _utc(value: datetime | None) -> datetime | None:
    return value.astimezone(timezone.utc) if value is not None else None


def review(
    scheduler: Scheduler, state: SrsState | None, grade: Grade, now: datetime
) -> tuple[SrsState, Rating]:
    """Return the new state after answering with `grade`. `state` is None for a card never reviewed."""
    now = now.astimezone(timezone.utc)
    if state is None:
        card = Card(due=now)
    else:
        card = Card(
            state=State(state.fsrs_state),
            step=state.step,
            stability=state.stability,
            difficulty=state.difficulty,
            due=_utc(state.due),
            last_review=_utc(state.last_review),
        )
    rating = RATING_BY_GRADE[grade]
    card, _log = scheduler.review_card(card, rating, review_datetime=now)
    new_state = SrsState(
        fsrs_state=int(card.state),
        step=card.step,
        stability=card.stability,
        difficulty=card.difficulty,
        due=card.due,
        last_review=card.last_review,
    )
    return new_state, rating
