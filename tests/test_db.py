from datetime import datetime, timedelta, timezone

from french_srs_bot import db
from french_srs_bot.models import SrsState

DAY_START = datetime(2026, 9, 17, 22, 0, tzinfo=timezone.utc)  # 2026-09-18 00:00 Amsterdam
NOW = DAY_START + timedelta(hours=10)


def card_ids(conn, item_id):
    rows = conn.execute(
        "SELECT direction, id FROM french.cards WHERE item_id = %s", (item_id,)
    ).fetchall()
    return {row["direction"]: row["id"] for row in rows}


def make_due(conn, card_id, *, introduced_at, due):
    conn.execute(
        """
        UPDATE french.cards SET introduced_at = %s, due = %s, fsrs_state = 2, step = NULL,
               stability = 5.0, difficulty = 5.0, last_review = %s
        WHERE id = %s
        """,
        (introduced_at, due, introduced_at, card_id),
    )


def test_migrations_are_idempotent(conn):
    assert db.run_migrations(conn) == []
    versions = [r["version"] for r in conn.execute("SELECT version FROM french.schema_migrations")]
    assert versions == ["001_initial"]


def test_upsert_item_creates_both_cards_and_updates_in_place(conn, add_items):
    (item_id,) = add_items([("le chien", "de hond")])
    assert set(card_ids(conn, item_id)) == {"fr_nl", "nl_fr"}
    theme_id = conn.execute("SELECT theme_id FROM french.items WHERE id = %s", (item_id,)).fetchone()["theme_id"]
    again = db.upsert_item(
        conn, theme_id=theme_id, position=1, french=["le chien"], dutch=["de hond", "de reu"],
        english="the dog", gender="m", hint=None,
    )
    assert again == item_id
    card = db.get_card(conn, card_ids(conn, item_id)["fr_nl"])
    assert card.dutch == ["de hond", "de reu"]
    assert card.gender == "m"
    assert card.srs is None


def test_upsert_item_keeps_card_progress(conn, add_items):
    (item_id,) = add_items([("le chien", "de hond")])
    fr_nl = card_ids(conn, item_id)["fr_nl"]
    make_due(conn, fr_nl, introduced_at=DAY_START - timedelta(days=3), due=NOW)
    add_items([("le chien", "de hond")])
    assert db.get_card(conn, fr_nl).srs is not None


def test_next_new_card_order_and_sibling_delay(conn, add_items):
    first, second = add_items([("le chien", "de hond"), ("le chat", "de kat")])
    new = db.next_new_card(conn, now=NOW, day_start=DAY_START)
    assert (new.item_id, new.direction) == (first, "fr_nl")

    # introduced today: its nl_fr must wait for a later day, so the next new card is item 2
    db.introduce_card(conn, card_ids(conn, first)["fr_nl"], NOW)
    new = db.next_new_card(conn, now=NOW, day_start=DAY_START)
    assert (new.item_id, new.direction) == (second, "fr_nl")

    # introduced yesterday and not due: nl_fr of item 1 comes before item 2
    fr_nl = card_ids(conn, first)["fr_nl"]
    make_due(conn, fr_nl, introduced_at=DAY_START - timedelta(hours=1), due=NOW + timedelta(hours=3))
    new = db.next_new_card(conn, now=NOW, day_start=DAY_START)
    assert (new.item_id, new.direction) == (first, "nl_fr")

    # fr_nl due right now: review it first, the reverse direction waits
    make_due(conn, fr_nl, introduced_at=DAY_START - timedelta(hours=1), due=NOW - timedelta(minutes=5))
    new = db.next_new_card(conn, now=NOW, day_start=DAY_START)
    assert (new.item_id, new.direction) == (second, "fr_nl")


def test_due_cards_skips_card_whose_sibling_was_reviewed_today(conn, add_items):
    (item_id,) = add_items([("le chien", "de hond")])
    ids = card_ids(conn, item_id)
    make_due(conn, ids["fr_nl"], introduced_at=DAY_START - timedelta(days=5), due=NOW - timedelta(hours=1))
    make_due(conn, ids["nl_fr"], introduced_at=DAY_START - timedelta(days=4), due=NOW - timedelta(hours=2))
    assert [c.direction for c in db.due_cards(conn, now=NOW, day_start=DAY_START)] == ["nl_fr", "fr_nl"]

    state = SrsState(fsrs_state=2, step=None, stability=6.0, difficulty=5.0,
                     due=NOW + timedelta(days=6), last_review=NOW)
    db.save_review(conn, card_id=ids["nl_fr"], answer="le chien", grade="correct", rating=3,
                   due_before=NOW - timedelta(hours=2), new_state=state, now=NOW)
    assert db.due_cards(conn, now=NOW, day_start=DAY_START) == []


def test_due_cards_includes_introduced_card_without_due(conn, add_items):
    (item_id,) = add_items([("le chien", "de hond")])
    fr_nl = card_ids(conn, item_id)["fr_nl"]
    db.introduce_card(conn, fr_nl, NOW - timedelta(hours=1))
    assert [c.card_id for c in db.due_cards(conn, now=NOW, day_start=DAY_START)] == [fr_nl]


def test_set_pending_if_none(conn, add_items):
    first, second = add_items([("le chien", "de hond"), ("le chat", "de kat")])
    card_a = card_ids(conn, first)["fr_nl"]
    card_b = card_ids(conn, second)["fr_nl"]
    assert db.set_pending_if_none(conn, card_a, NOW) is True
    assert db.set_pending_if_none(conn, card_b, NOW) is False
    assert db.set_pending_if_none(conn, card_a, NOW) is False
    assert db.get_bot_state(conn).pending_card_id == card_a


def test_save_review_logs_and_clears_pending(conn, add_items):
    (item_id,) = add_items([("le chien", "de hond")])
    fr_nl = card_ids(conn, item_id)["fr_nl"]
    db.introduce_card(conn, fr_nl, NOW)
    db.set_pending(conn, fr_nl, NOW)
    db.set_batch_remaining(conn, 4)
    state = SrsState(fsrs_state=1, step=1, stability=2.3, difficulty=2.1,
                     due=NOW + timedelta(hours=4), last_review=NOW)
    db.save_review(conn, card_id=fr_nl, answer="de hond", grade="correct", rating=3,
                   due_before=None, new_state=state, now=NOW)
    assert db.get_bot_state(conn).pending_card_id is None
    assert db.get_bot_state(conn).batch_remaining == 3
    card = db.get_card(conn, fr_nl)
    assert card.srs.step == 1 and card.srs.due == state.due
    stats = db.day_stats(conn, day_start=DAY_START, day_end=DAY_START + timedelta(days=1))
    assert (stats.total, stats.new, stats.reviews) == (1, 1, 0)


def test_save_review_keeps_other_pending_card(conn, add_items):
    (first, second) = add_items([("le chien", "de hond"), ("le chat", "de kat")])
    card_a = card_ids(conn, first)["fr_nl"]
    card_b = card_ids(conn, second)["fr_nl"]
    db.introduce_card(conn, card_a, NOW)
    db.introduce_card(conn, card_b, NOW)
    db.set_pending(conn, card_a, NOW)
    db.set_batch_remaining(conn, 4)
    state = SrsState(fsrs_state=1, step=1, stability=2.3, difficulty=2.1,
                     due=NOW + timedelta(hours=4), last_review=NOW)
    db.save_review(conn, card_id=card_b, answer="de kat", grade="correct", rating=3,
                   due_before=None, new_state=state, now=NOW)
    assert db.get_bot_state(conn).pending_card_id == card_a
    assert db.get_bot_state(conn).batch_remaining == 4


def test_daily_totals_uses_local_dates(conn, add_items):
    (item_id,) = add_items([("le chien", "de hond")])
    fr_nl = card_ids(conn, item_id)["fr_nl"]
    state = SrsState(fsrs_state=1, step=1, stability=1.0, difficulty=1.0, due=NOW, last_review=NOW)
    # 23:30 UTC on the 17th is 01:30 on the 18th in Amsterdam
    late = datetime(2026, 9, 17, 23, 30, tzinfo=timezone.utc)
    db.save_review(conn, card_id=fr_nl, answer="x", grade="wrong", rating=1,
                   due_before=None, new_state=state, now=late)
    totals = db.daily_totals(conn, timezone_name="Europe/Amsterdam", since=late - timedelta(days=1))
    assert totals == {datetime(2026, 9, 18).date(): 1}
