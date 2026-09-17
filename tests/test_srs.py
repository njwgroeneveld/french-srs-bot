from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fsrs import Rating, State

from french_srs_bot.grading import Grade
from french_srs_bot.srs import build_scheduler, review

NOW = datetime(2026, 9, 18, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def scheduler():
    return build_scheduler(
        learning_steps=[timedelta(hours=4), timedelta(hours=4), timedelta(days=1)],
        relearning_steps=[timedelta(hours=4)],
        enable_fuzzing=False,
    )


def test_new_card_correct_comes_back_in_four_hours(scheduler):
    state, rating = review(scheduler, None, Grade.CORRECT, NOW)
    assert rating == Rating.Good
    assert state.fsrs_state == State.Learning
    assert state.due - NOW == timedelta(hours=4)


def test_learning_path_intro_then_4h_then_1d_then_graduated(scheduler):
    state, _ = review(scheduler, None, Grade.CORRECT, NOW)
    now = state.due
    state, _ = review(scheduler, state, Grade.CORRECT, now)
    assert state.due - now == timedelta(days=1)
    assert state.fsrs_state == State.Learning
    now = state.due
    state, _ = review(scheduler, state, Grade.CORRECT, now)
    assert state.fsrs_state == State.Review
    assert state.due - now >= timedelta(days=2)


@pytest.mark.parametrize("grade", list(Grade))
def test_new_card_never_graduates_on_first_review(scheduler, grade):
    state, _ = review(scheduler, None, grade, NOW)
    assert state.fsrs_state == State.Learning


def test_wrong_on_review_card_goes_to_relearning(scheduler):
    state, _ = review(scheduler, None, Grade.CORRECT, NOW)
    for _ in range(2):
        state, _ = review(scheduler, state, Grade.CORRECT, state.due)
    assert state.fsrs_state == State.Review
    now = state.due
    state, rating = review(scheduler, state, Grade.WRONG, now)
    assert rating == Rating.Again
    assert state.fsrs_state == State.Relearning
    assert state.due - now == timedelta(hours=4)


def test_almost_and_hard_map_to_fsrs_hard(scheduler):
    _, rating_almost = review(scheduler, None, Grade.ALMOST, NOW)
    _, rating_hard = review(scheduler, None, Grade.HARD, NOW)
    assert rating_almost == rating_hard == Rating.Hard


def test_accepts_non_utc_datetimes_from_the_database(scheduler):
    state, _ = review(scheduler, None, Grade.CORRECT, NOW)
    local_state = type(state)(
        fsrs_state=state.fsrs_state,
        step=state.step,
        stability=state.stability,
        difficulty=state.difficulty,
        due=state.due.astimezone(ZoneInfo("Europe/Amsterdam")),
        last_review=state.last_review.astimezone(ZoneInfo("Europe/Amsterdam")),
    )
    later = state.due.astimezone(ZoneInfo("Europe/Amsterdam"))
    new_state, _ = review(scheduler, local_state, Grade.CORRECT, later)
    assert new_state.due - later == timedelta(days=1)


def test_rejects_naive_datetimes(scheduler):
    with pytest.raises(ValueError):
        review(scheduler, None, Grade.CORRECT, datetime(2026, 9, 18, 8, 0))
