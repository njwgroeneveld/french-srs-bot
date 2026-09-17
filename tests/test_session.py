from datetime import date, datetime, timedelta, timezone

import pytest

from french_srs_bot import db, session
from french_srs_bot.grading import Grade
from french_srs_bot.srs import build_scheduler

# 08:00 in Amsterdam on 2026-09-18
MORNING = datetime(2026, 9, 18, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def scheduler(settings):
    return build_scheduler(settings.learning_steps, settings.relearning_steps, enable_fuzzing=False)


def card_id(conn, item_id, direction):
    return conn.execute(
        "SELECT id FROM french.cards WHERE item_id = %s AND direction = %s", (item_id, direction)
    ).fetchone()["id"]


def make_review_due(conn, item_id, direction, *, due):
    conn.execute(
        """
        UPDATE french.cards SET introduced_at = %(intro)s, due = %(due)s, fsrs_state = 2, step = NULL,
               stability = 5.0, difficulty = 5.0, last_review = %(intro)s
        WHERE item_id = %(item)s AND direction = %(direction)s
        """,
        {"intro": MORNING - timedelta(days=10), "due": due, "item": item_id, "direction": direction},
    )


def do_card(conn, settings, scheduler, step, text, now):
    """Play one card like the bot does. Returns the Answered result."""
    assert isinstance(step, session.Ask)
    if step.is_new:
        assert session.acknowledge_intro(conn, step.card.card_id, now) is not None
    else:
        session.mark_asked(conn, step.card, now)
    return session.answer(conn, settings, scheduler, text, now)


def test_empty_database_gives_summary(conn, settings):
    step = session.start_batch(conn, settings, MORNING)
    assert step == session.Summary(done_today=0, goal=10, due_now=0, streak=0)


def test_batch_starts_with_new_card_then_reviews(conn, settings, scheduler, add_items):
    new, old = add_items([("le chien", "de hond"), ("le chat", "de kat")])
    make_review_due(conn, old, "fr_nl", due=MORNING - timedelta(hours=1))

    step = session.start_batch(conn, settings, MORNING)
    assert step.is_new and step.card.item_id == new

    answered = do_card(conn, settings, scheduler, step, "de hond", MORNING)
    assert answered.result.grade == Grade.CORRECT
    assert answered.next.is_new is False and answered.next.card.item_id == old


def test_new_card_learning_step_is_due_four_hours_later(conn, settings, scheduler, add_items):
    (item,) = add_items([("le chien", "de hond")])
    step = session.start_batch(conn, settings, MORNING)
    do_card(conn, settings, scheduler, step, "de hond", MORNING)

    afternoon = MORNING + timedelta(hours=5)
    step = session.start_batch(conn, settings, afternoon)
    assert step.is_new is False and step.card.card_id == card_id(conn, item, "fr_nl")


def test_both_directions_never_on_the_same_day(conn, settings, scheduler, add_items):
    add_items([("le chien", "de hond")])
    answers = {"fr_nl": "de hond", "nl_fr": "le chien"}
    seen_per_day = []
    for day in range(6):
        now = MORNING + timedelta(days=day)
        directions = set()
        step = session.start_batch(conn, settings, now)
        while isinstance(step, session.Ask):
            directions.add(step.card.direction)
            step = do_card(conn, settings, scheduler, step, answers[step.card.direction], now).next
        seen_per_day.append(directions)
    assert all(len(directions) <= 1 for directions in seen_per_day)
    assert seen_per_day[0] == {"fr_nl"}
    assert any("nl_fr" in directions for directions in seen_per_day)


def test_sibling_reviewed_today_is_not_asked_again(conn, settings, scheduler, add_items):
    (item,) = add_items([("le chien", "de hond")])
    make_review_due(conn, item, "fr_nl", due=MORNING - timedelta(hours=2))
    make_review_due(conn, item, "nl_fr", due=MORNING - timedelta(hours=1))
    step = session.start_batch(conn, settings, MORNING)
    assert step.card.direction == "fr_nl"
    answered = do_card(conn, settings, scheduler, step, "de hond", MORNING)
    assert isinstance(answered.next, session.Summary)
    assert answered.next.due_now == 0


def test_daily_new_cap_when_enough_reviews_are_due(conn, settings, scheduler, add_items):
    words = [(f"mot{i}", f"woord{i}") for i in range(30)]
    items = add_items(words)
    for item in items[:15]:
        make_review_due(conn, item, "fr_nl", due=MORNING - timedelta(hours=1))
    new_seen = 0
    now = MORNING
    for _batch in range(6):
        step = session.start_batch(conn, settings, now)
        while isinstance(step, session.Ask):
            new_seen += step.is_new
            answered = do_card(conn, settings, scheduler, step, "fout", now)
            step = answered.next
        now += timedelta(minutes=30)
    assert new_seen == settings.daily_new


def test_new_cards_fill_the_goal_when_few_reviews_are_due(conn, settings, scheduler, add_items):
    items = add_items([(f"mot{i}", f"woord{i}") for i in range(20)])
    make_review_due(conn, items[0], "fr_nl", due=MORNING - timedelta(hours=1))
    make_review_due(conn, items[1], "fr_nl", due=MORNING - timedelta(hours=1))
    new_seen = 0
    step = session.start_batch(conn, settings, MORNING)
    for _ in range(3):
        while isinstance(step, session.Ask):
            new_seen += step.is_new
            step = do_card(conn, settings, scheduler, step, "woord", MORNING).next
        step = session.start_batch(conn, settings, MORNING)
    assert new_seen == 8  # 10 goal - 2 reviews


def test_batch_size_limits_cards_per_batch(conn, settings, scheduler, add_items):
    items = add_items([(f"mot{i}", f"woord{i}") for i in range(10)])
    for item in items:
        make_review_due(conn, item, "fr_nl", due=MORNING - timedelta(hours=1))
    step = session.start_batch(conn, settings, MORNING)
    asked = 0
    while isinstance(step, session.Ask):
        asked += 1
        step = do_card(conn, settings, scheduler, step, "x", MORNING).next
    assert asked == settings.batch_size
    assert step.due_now == 6


def test_pending_card_is_asked_again_on_new_batch(conn, settings, add_items):
    add_items([("le chien", "de hond"), ("le chat", "de kat")])
    step = session.start_batch(conn, settings, MORNING)
    session.acknowledge_intro(conn, step.card.card_id, MORNING)
    again = session.start_batch(conn, settings, MORNING + timedelta(hours=5))
    assert again.card.card_id == step.card.card_id and again.is_new is False


def test_stale_intro_button_is_ignored(conn, settings, add_items):
    add_items([("le chien", "de hond"), ("le chat", "de kat")])
    first = session.start_batch(conn, settings, MORNING)
    session.acknowledge_intro(conn, first.card.card_id, MORNING)
    other = db.next_new_card(conn, now=MORNING, day_start=session.day_bounds(MORNING, settings.timezone)[0])
    assert session.acknowledge_intro(conn, other.card_id, MORNING) is None


def test_answer_without_pending_card(conn, settings, scheduler):
    assert session.answer(conn, settings, scheduler, "hallo", MORNING) is None


def test_scheduled_batch_stops_after_goal(conn, settings, scheduler, add_items):
    items = add_items([(f"mot{i}", f"woord{i}") for i in range(20)])
    for item in items:
        make_review_due(conn, item, "fr_nl", due=MORNING - timedelta(hours=1))
    for _ in range(3):
        step = session.scheduled_batch(conn, settings, MORNING)
        while isinstance(step, session.Ask):
            step = do_card(conn, settings, scheduler, step, "x", MORNING).next
    assert session.today_stats(conn, settings, MORNING).total >= settings.daily_goal
    assert session.scheduled_batch(conn, settings, MORNING) is None
    assert session.reminder_needed(conn, settings, MORNING) is None


def test_compute_streak():
    today = date(2026, 9, 18)
    totals = {today - timedelta(days=d): 10 for d in (1, 2, 3)} | {today - timedelta(days=5): 12}
    assert session.compute_streak(totals, today, 10) == 3  # today not done yet
    assert session.compute_streak(totals | {today: 10}, today, 10) == 4
    assert session.compute_streak({}, today, 10) == 0


def test_day_bounds_are_local(settings):
    start, end = session.day_bounds(datetime(2026, 9, 17, 23, 30, tzinfo=timezone.utc), settings.timezone)
    assert start.isoformat() == "2026-09-18T00:00:00+02:00"
    assert end - start == timedelta(days=1)
