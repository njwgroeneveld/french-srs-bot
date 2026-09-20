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


def card_id(conn, item_id, direction, user_id):
    return conn.execute(
        "SELECT id FROM french.cards WHERE item_id = %s AND direction = %s AND user_id = %s",
        (item_id, direction, user_id),
    ).fetchone()["id"]


def make_review_due(conn, item_id, direction, *, due, user_id):
    conn.execute(
        """
        UPDATE french.cards SET introduced_at = %(intro)s, due = %(due)s, fsrs_state = 2, step = NULL,
               stability = 5.0, difficulty = 5.0, last_review = %(intro)s
        WHERE item_id = %(item)s AND direction = %(direction)s AND user_id = %(user_id)s
        """,
        {
            "intro": MORNING - timedelta(days=10),
            "due": due,
            "item": item_id,
            "direction": direction,
            "user_id": user_id,
        },
    )


def do_card(conn, settings, scheduler, step, text, now, *, user_id):
    """Play one card like the bot does. Returns the Answered result."""
    assert isinstance(step, session.Ask)
    if step.is_new:
        assert session.acknowledge_intro(conn, settings, step.card.card_id, now, user_id=user_id) is not None
    else:
        assert session.mark_asked(conn, step.card, now, user_id=user_id)
    return session.answer(conn, settings, scheduler, text, now, user_id=user_id)


def test_empty_database_gives_summary(conn, settings, user):
    step = session.start_batch(conn, settings, user.id, MORNING)
    assert step == session.Summary(done_today=0, goal=10, due_now=0, streak=0, more_available=False)


def test_summary_after_full_batch_reports_more_new_cards(conn, settings, scheduler, add_items, user):
    add_items([(f"mot{i}", f"woord{i}") for i in range(10)])
    step = session.start_batch(conn, settings, user.id, MORNING)
    while isinstance(step, session.Ask):
        step = do_card(conn, settings, scheduler, step, "x", MORNING, user_id=user.id).next
    assert step.due_now == 0
    assert step.more_available is True


def test_mark_asked_does_not_replace_another_pending_card(conn, settings, add_items, user):
    first, second = add_items([("le chien", "de hond"), ("le chat", "de kat")])
    make_review_due(conn, second, "fr_nl", due=MORNING - timedelta(hours=1), user_id=user.id)
    step = session.start_batch(conn, settings, user.id, MORNING)
    assert step.is_new and step.card.item_id == first
    session.acknowledge_intro(conn, settings, step.card.card_id, MORNING, user_id=user.id)
    review = db.get_card(conn, card_id(conn, second, "fr_nl", user.id), user_id=user.id)
    assert session.mark_asked(conn, review, MORNING, user_id=user.id) is False
    assert db.get_bot_state(conn, user_id=user.id).pending_card_id == step.card.card_id
    # re-asking the pending card itself still works
    assert session.mark_asked(conn, step.card, MORNING, user_id=user.id) is True


def test_batch_starts_with_new_card_then_reviews(conn, settings, scheduler, add_items, user):
    new, old = add_items([("le chien", "de hond"), ("le chat", "de kat")])
    make_review_due(conn, old, "fr_nl", due=MORNING - timedelta(hours=1), user_id=user.id)

    step = session.start_batch(conn, settings, user.id, MORNING)
    assert step.is_new and step.card.item_id == new

    answered = do_card(conn, settings, scheduler, step, "de hond", MORNING, user_id=user.id)
    assert answered.result.grade == Grade.CORRECT
    assert answered.next.is_new is False and answered.next.card.item_id == old


def test_new_card_learning_step_is_due_four_hours_later(conn, settings, scheduler, add_items, user):
    (item,) = add_items([("le chien", "de hond")])
    step = session.start_batch(conn, settings, user.id, MORNING)
    do_card(conn, settings, scheduler, step, "de hond", MORNING, user_id=user.id)

    afternoon = MORNING + timedelta(hours=5)
    step = session.start_batch(conn, settings, user.id, afternoon)
    assert step.is_new is False and step.card.card_id == card_id(conn, item, "fr_nl", user.id)


def test_both_directions_never_on_the_same_day(conn, settings, scheduler, add_items, user):
    add_items([("le chien", "de hond")])
    answers = {"fr_nl": "de hond", "nl_fr": "le chien"}
    seen_per_day = []
    for day in range(6):
        now = MORNING + timedelta(days=day)
        directions = set()
        step = session.start_batch(conn, settings, user.id, now)
        while isinstance(step, session.Ask):
            directions.add(step.card.direction)
            step = do_card(conn, settings, scheduler, step, answers[step.card.direction], now, user_id=user.id).next
        seen_per_day.append(directions)
    assert all(len(directions) <= 1 for directions in seen_per_day)
    assert seen_per_day[0] == {"fr_nl"}
    assert any("nl_fr" in directions for directions in seen_per_day)


def test_sibling_reviewed_today_is_not_asked_again(conn, settings, scheduler, add_items, user):
    (item,) = add_items([("le chien", "de hond")])
    make_review_due(conn, item, "fr_nl", due=MORNING - timedelta(hours=2), user_id=user.id)
    make_review_due(conn, item, "nl_fr", due=MORNING - timedelta(hours=1), user_id=user.id)
    step = session.start_batch(conn, settings, user.id, MORNING)
    assert step.card.direction == "fr_nl"
    answered = do_card(conn, settings, scheduler, step, "de hond", MORNING, user_id=user.id)
    assert isinstance(answered.next, session.Summary)
    assert answered.next.due_now == 0


def test_daily_new_cap_when_enough_reviews_are_due(conn, settings, scheduler, add_items, user):
    words = [(f"mot{i}", f"woord{i}") for i in range(30)]
    items = add_items(words)
    for item in items[:15]:
        make_review_due(conn, item, "fr_nl", due=MORNING - timedelta(hours=1), user_id=user.id)
    new_seen = 0
    now = MORNING
    for _batch in range(6):
        step = session.start_batch(conn, settings, user.id, now)
        while isinstance(step, session.Ask):
            new_seen += step.is_new
            answered = do_card(conn, settings, scheduler, step, "fout", now, user_id=user.id)
            step = answered.next
        now += timedelta(minutes=30)
    assert new_seen == settings.daily_new


def test_new_cards_fill_the_goal_when_few_reviews_are_due(conn, settings, scheduler, add_items, user):
    items = add_items([(f"mot{i}", f"woord{i}") for i in range(20)])
    make_review_due(conn, items[0], "fr_nl", due=MORNING - timedelta(hours=1), user_id=user.id)
    make_review_due(conn, items[1], "fr_nl", due=MORNING - timedelta(hours=1), user_id=user.id)
    new_seen = 0
    step = session.start_batch(conn, settings, user.id, MORNING)
    for _ in range(3):
        while isinstance(step, session.Ask):
            new_seen += step.is_new
            step = do_card(conn, settings, scheduler, step, "woord", MORNING, user_id=user.id).next
        step = session.start_batch(conn, settings, user.id, MORNING)
    assert new_seen == 8  # 10 goal - 2 reviews


def test_batch_size_limits_cards_per_batch(conn, settings, scheduler, add_items, user):
    items = add_items([(f"mot{i}", f"woord{i}") for i in range(10)])
    for item in items:
        make_review_due(conn, item, "fr_nl", due=MORNING - timedelta(hours=1), user_id=user.id)
    step = session.start_batch(conn, settings, user.id, MORNING)
    asked = 0
    while isinstance(step, session.Ask):
        asked += 1
        step = do_card(conn, settings, scheduler, step, "x", MORNING, user_id=user.id).next
    assert asked == settings.batch_size
    assert step.due_now == 6


def test_pending_card_is_asked_again_on_new_batch(conn, settings, add_items, user):
    add_items([("le chien", "de hond"), ("le chat", "de kat")])
    step = session.start_batch(conn, settings, user.id, MORNING)
    session.acknowledge_intro(conn, settings, step.card.card_id, MORNING, user_id=user.id)
    again = session.start_batch(conn, settings, user.id, MORNING + timedelta(hours=5))
    assert again.card.card_id == step.card.card_id and again.is_new is False


def test_stale_intro_button_is_ignored(conn, settings, add_items, user):
    add_items([("le chien", "de hond"), ("le chat", "de kat")])
    first = session.start_batch(conn, settings, user.id, MORNING)
    session.acknowledge_intro(conn, settings, first.card.card_id, MORNING, user_id=user.id)
    other = db.next_new_card(
        conn, user_id=user.id, now=MORNING, day_start=session.day_bounds(MORNING, settings.timezone)[0]
    )
    assert session.acknowledge_intro(conn, settings, other.card_id, MORNING, user_id=user.id) is None


def test_stale_intro_button_respects_eligibility(conn, settings, scheduler, add_items, user):
    (item,) = add_items([("le chien", "de hond")])
    fr_nl = card_id(conn, item, "fr_nl", user.id)
    nl_fr = card_id(conn, item, "nl_fr", user.id)
    # fr_nl introduced yesterday, due at 13:00 today
    afternoon = MORNING + timedelta(hours=5)
    conn.execute(
        """
        UPDATE french.cards SET introduced_at = %(intro)s, due = %(due)s, fsrs_state = 2, step = NULL,
               stability = 1.0, difficulty = 5.0, last_review = %(intro)s
        WHERE id = %(id)s
        """,
        {"intro": MORNING - timedelta(days=1), "due": afternoon - timedelta(minutes=5), "id": fr_nl},
    )
    # 08:00: fr_nl is not due yet, so nl_fr is offered - as a direct question, since the
    # word itself is not new. Not answered here, so it stays unstarted.
    step = session.start_batch(conn, settings, user.id, MORNING)
    assert step.card.card_id == nl_fr and step.is_new is False
    # 13:00: fr_nl is due and gets answered
    step = session.start_batch(conn, settings, user.id, afternoon)
    assert step.card.card_id == fr_nl and step.is_new is False
    do_card(conn, settings, scheduler, step, "de hond", afternoon, user_id=user.id)
    # the old nl_fr button must not introduce the sibling on the same day
    assert session.acknowledge_intro(conn, settings, nl_fr, afternoon, user_id=user.id) is None
    assert db.get_card(conn, nl_fr, user_id=user.id).introduced_at is None


def test_pending_intro_button_pressed_again_is_accepted(conn, settings, add_items, user):
    add_items([("le chien", "de hond")])
    step = session.start_batch(conn, settings, user.id, MORNING)
    assert session.acknowledge_intro(conn, settings, step.card.card_id, MORNING, user_id=user.id) is not None
    assert session.acknowledge_intro(conn, settings, step.card.card_id, MORNING, user_id=user.id) is not None


def test_answer_without_pending_card(conn, settings, scheduler, user):
    assert session.answer(conn, settings, scheduler, "hallo", MORNING, user_id=user.id) is None


def test_scheduled_batch_stops_after_goal(conn, settings, scheduler, add_items, user):
    items = add_items([(f"mot{i}", f"woord{i}") for i in range(20)])
    for item in items:
        make_review_due(conn, item, "fr_nl", due=MORNING - timedelta(hours=1), user_id=user.id)
    for _ in range(3):
        step = session.scheduled_batch(conn, settings, user.id, MORNING)
        while isinstance(step, session.Ask):
            step = do_card(conn, settings, scheduler, step, "x", MORNING, user_id=user.id).next
    assert session.today_stats(conn, settings, user.id, MORNING).total >= settings.daily_goal
    assert session.scheduled_batch(conn, settings, user.id, MORNING) is None
    assert session.reminder_needed(conn, settings, user.id, MORNING) is None


def test_answering_is_scoped_to_the_user_who_answers(conn, settings, scheduler, add_items):
    db.claim_owner(conn, telegram_user_id=42, name="Niels")
    conn.execute("INSERT INTO french.users (telegram_user_id, name) VALUES (99, 'Inga')")
    niels, inga = db.all_users(conn)
    add_items([("à pied", "te voet")])

    # Niels gets a question; Inga has nothing pending and so cannot answer anything.
    step = session.start_batch(conn, settings, niels.id, MORNING)
    session.acknowledge_intro(conn, settings, step.card.card_id, MORNING, user_id=niels.id)

    assert session.answer(conn, settings, scheduler, "te voet", MORNING, user_id=inga.id) is None
    answered = session.answer(conn, settings, scheduler, "te voet", MORNING, user_id=niels.id)
    assert answered is not None


def test_goal_just_reached_is_true_only_on_the_crossing_answer(
    conn, settings, scheduler, add_items, user
):
    items = add_items([(f"mot{i}", f"woord{i}") for i in range(20)])
    for item in items:
        make_review_due(conn, item, "fr_nl", due=MORNING - timedelta(hours=1), user_id=user.id)

    flags = []
    for _ in range(6):  # comfortably more batches than needed to pass the goal of 10
        step = session.start_batch(conn, settings, user.id, MORNING)
        while isinstance(step, session.Ask):
            answered = do_card(conn, settings, scheduler, step, "x", MORNING, user_id=user.id)
            flags.append(answered.goal_just_reached)
            step = answered.next

    assert flags.count(True) == 1
    assert flags.index(True) == settings.daily_goal - 1  # the goal-th answer, zero-based


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


def test_day_bounds_dst_end_is_25_hours(settings):
    # 2026-10-25: Europe/Amsterdam goes from CEST (+02:00) back to CET (+01:00)
    start, end = session.day_bounds(datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc), settings.timezone)
    assert end.astimezone(timezone.utc) - start.astimezone(timezone.utc) == timedelta(hours=25)
    assert start.utcoffset() != end.utcoffset()


def log_reviews(conn, user_id, item_ids, *, count, when):
    """Write `count` review rows for this user straight into the table, no grading involved."""
    for item_id in item_ids[:count]:
        conn.execute(
            """
            INSERT INTO french.reviews (card_id, reviewed_at, answer, grade, rating, due_after)
            SELECT id, %s, 'x', 'correct', 3, %s FROM french.cards
            WHERE user_id = %s AND item_id = %s AND direction = 'fr_nl'
            """,
            (when, when, user_id, item_id),
        )


def test_week_standings_counts_cards_and_days_reached(conn, settings, add_items):
    db.claim_owner(conn, telegram_user_id=42, name="Niels")
    conn.execute("INSERT INTO french.users (telegram_user_id, name) VALUES (99, 'Inga')")
    niels, inga = db.all_users(conn)
    items = add_items([(f"mot{i}", f"woord{i}") for i in range(20)])

    # Niels reaches his goal today; Inga does two cards today and reached her goal yesterday.
    log_reviews(conn, niels.id, items, count=settings.daily_goal, when=MORNING)
    log_reviews(conn, inga.id, items, count=2, when=MORNING)
    log_reviews(conn, inga.id, items, count=settings.daily_goal, when=MORNING - timedelta(days=1))

    rows = session.week_standings(conn, settings, MORNING)

    assert [(r.name, r.cards, r.days_reached, r.goal) for r in rows] == [
        ("Niels", settings.daily_goal, 1, settings.daily_goal),
        ("Inga", settings.daily_goal + 2, 1, settings.daily_goal),
    ]


def test_week_standings_measures_everyone_against_their_own_goal(conn, settings, add_items):
    db.claim_owner(conn, telegram_user_id=42, name="Niels")
    conn.execute(
        "INSERT INTO french.users (telegram_user_id, name, daily_goal) VALUES (99, 'Inga', 3)"
    )
    niels, inga = db.all_users(conn)
    items = add_items([(f"mot{i}", f"woord{i}") for i in range(20)])

    # The same four cards: below Niels's goal of 10, comfortably past Inga's goal of 3.
    log_reviews(conn, niels.id, items, count=4, when=MORNING)
    log_reviews(conn, inga.id, items, count=4, when=MORNING)

    rows = session.week_standings(conn, settings, MORNING)

    assert [(r.name, r.days_reached, r.goal) for r in rows] == [("Niels", 0, 10), ("Inga", 1, 3)]


def test_the_reverse_direction_is_asked_without_reintroducing_the_word(
    conn, settings, scheduler, add_items, user
):
    """NL->FR of a word already learned the other way round must not show the answer first."""
    (item,) = add_items([("en voiture", "met de auto")])
    make_review_due(conn, item, "fr_nl", due=MORNING + timedelta(days=3), user_id=user.id)

    step = session.start_batch(conn, settings, user.id, MORNING)

    assert step.card.direction == "nl_fr"
    assert step.is_new is False  # no intro, no "Begrepen" button, no answer given away
    assert step.card.introduced_at is None

    # Asking it is what starts it: otherwise the card would never be scheduled again.
    assert session.mark_asked(conn, step.card, MORNING, user_id=user.id)
    assert db.get_card(conn, step.card.card_id, user_id=user.id).introduced_at is not None


def test_a_brand_new_word_is_still_introduced_first(conn, settings, add_items, user):
    add_items([("le chien", "de hond")])

    step = session.start_batch(conn, settings, user.id, MORNING)

    assert step.card.direction == "fr_nl"
    assert step.is_new is True
