from datetime import datetime, timedelta, timezone

from conftest import all_migrations
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
    assert versions == all_migrations()


def test_upsert_item_creates_both_cards_and_updates_in_place(conn, user, add_items):
    (item_id,) = add_items([("le chien", "de hond")])
    assert set(card_ids(conn, item_id)) == {"fr_nl", "nl_fr"}
    theme_id = conn.execute("SELECT theme_id FROM french.items WHERE id = %s", (item_id,)).fetchone()["theme_id"]
    again = db.upsert_item(
        conn, theme_id=theme_id, position=1, french=["le chien"], dutch=["de hond", "de reu"],
        english="the dog", gender="m", hint=None,
    )
    assert again == item_id
    card = db.get_card(conn, card_ids(conn, item_id)["fr_nl"], user_id=user.id)
    assert card.dutch == ["de hond", "de reu"]
    assert card.gender == "m"
    assert card.srs is None


def test_upsert_item_keeps_card_progress(conn, user, add_items):
    (item_id,) = add_items([("le chien", "de hond")])
    fr_nl = card_ids(conn, item_id)["fr_nl"]
    make_due(conn, fr_nl, introduced_at=DAY_START - timedelta(days=3), due=NOW)
    add_items([("le chien", "de hond")])
    assert db.get_card(conn, fr_nl, user_id=user.id).srs is not None


def test_next_new_card_order_and_sibling_delay(conn, user, add_items):
    first, second = add_items([("le chien", "de hond"), ("le chat", "de kat")])
    new = db.next_new_card(conn, user_id=user.id, now=NOW, day_start=DAY_START)
    assert (new.item_id, new.direction) == (first, "fr_nl")

    # introduced today: its nl_fr must wait for a later day, so the next new card is item 2
    db.introduce_card(conn, card_ids(conn, first)["fr_nl"], NOW, user_id=user.id)
    new = db.next_new_card(conn, user_id=user.id, now=NOW, day_start=DAY_START)
    assert (new.item_id, new.direction) == (second, "fr_nl")

    # introduced yesterday and not due: nl_fr of item 1 comes before item 2
    fr_nl = card_ids(conn, first)["fr_nl"]
    make_due(conn, fr_nl, introduced_at=DAY_START - timedelta(hours=1), due=NOW + timedelta(hours=3))
    new = db.next_new_card(conn, user_id=user.id, now=NOW, day_start=DAY_START)
    assert (new.item_id, new.direction) == (first, "nl_fr")

    # fr_nl due right now: review it first, the reverse direction waits
    make_due(conn, fr_nl, introduced_at=DAY_START - timedelta(hours=1), due=NOW - timedelta(minutes=5))
    new = db.next_new_card(conn, user_id=user.id, now=NOW, day_start=DAY_START)
    assert (new.item_id, new.direction) == (second, "fr_nl")


def test_due_cards_skips_card_whose_sibling_was_reviewed_today(conn, user, add_items):
    (item_id,) = add_items([("le chien", "de hond")])
    ids = card_ids(conn, item_id)
    make_due(conn, ids["fr_nl"], introduced_at=DAY_START - timedelta(days=5), due=NOW - timedelta(hours=1))
    make_due(conn, ids["nl_fr"], introduced_at=DAY_START - timedelta(days=4), due=NOW - timedelta(hours=2))
    assert [c.direction for c in db.due_cards(conn, user_id=user.id, now=NOW, day_start=DAY_START)] == ["nl_fr", "fr_nl"]

    state = SrsState(fsrs_state=2, step=None, stability=6.0, difficulty=5.0,
                     due=NOW + timedelta(days=6), last_review=NOW)
    db.save_review(conn, card_id=ids["nl_fr"], answer="le chien", grade="correct", rating=3,
                   due_before=NOW - timedelta(hours=2), new_state=state, now=NOW, user_id=user.id)
    assert db.due_cards(conn, user_id=user.id, now=NOW, day_start=DAY_START) == []


def test_due_cards_includes_introduced_card_without_due(conn, user, add_items):
    (item_id,) = add_items([("le chien", "de hond")])
    fr_nl = card_ids(conn, item_id)["fr_nl"]
    db.introduce_card(conn, fr_nl, NOW - timedelta(hours=1), user_id=user.id)
    assert [c.card_id for c in db.due_cards(conn, user_id=user.id, now=NOW, day_start=DAY_START)] == [fr_nl]


def test_set_pending_if_none(conn, user, add_items):
    first, second = add_items([("le chien", "de hond"), ("le chat", "de kat")])
    card_a = card_ids(conn, first)["fr_nl"]
    card_b = card_ids(conn, second)["fr_nl"]
    assert db.set_pending_if_none(conn, card_a, NOW, user_id=user.id) is True
    assert db.set_pending_if_none(conn, card_b, NOW, user_id=user.id) is False
    assert db.set_pending_if_none(conn, card_a, NOW, user_id=user.id) is False
    assert db.get_bot_state(conn, user_id=user.id).pending_card_id == card_a


def test_save_review_logs_and_clears_pending(conn, user, add_items):
    (item_id,) = add_items([("le chien", "de hond")])
    fr_nl = card_ids(conn, item_id)["fr_nl"]
    db.introduce_card(conn, fr_nl, NOW, user_id=user.id)
    db.set_pending(conn, fr_nl, NOW, user_id=user.id)
    db.set_batch_remaining(conn, 4, user_id=user.id)
    state = SrsState(fsrs_state=1, step=1, stability=2.3, difficulty=2.1,
                     due=NOW + timedelta(hours=4), last_review=NOW)
    db.save_review(conn, card_id=fr_nl, answer="de hond", grade="correct", rating=3,
                   due_before=None, new_state=state, now=NOW, user_id=user.id)
    assert db.get_bot_state(conn, user_id=user.id).pending_card_id is None
    assert db.get_bot_state(conn, user_id=user.id).batch_remaining == 3
    card = db.get_card(conn, fr_nl, user_id=user.id)
    assert card.srs.step == 1 and card.srs.due == state.due
    stats = db.day_stats(conn, user_id=user.id, day_start=DAY_START, day_end=DAY_START + timedelta(days=1))
    assert (stats.total, stats.new, stats.reviews) == (1, 1, 0)


def test_save_review_keeps_other_pending_card(conn, user, add_items):
    (first, second) = add_items([("le chien", "de hond"), ("le chat", "de kat")])
    card_a = card_ids(conn, first)["fr_nl"]
    card_b = card_ids(conn, second)["fr_nl"]
    db.introduce_card(conn, card_a, NOW, user_id=user.id)
    db.introduce_card(conn, card_b, NOW, user_id=user.id)
    db.set_pending(conn, card_a, NOW, user_id=user.id)
    db.set_batch_remaining(conn, 4, user_id=user.id)
    state = SrsState(fsrs_state=1, step=1, stability=2.3, difficulty=2.1,
                     due=NOW + timedelta(hours=4), last_review=NOW)
    db.save_review(conn, card_id=card_b, answer="de kat", grade="correct", rating=3,
                   due_before=None, new_state=state, now=NOW, user_id=user.id)
    assert db.get_bot_state(conn, user_id=user.id).pending_card_id == card_a
    assert db.get_bot_state(conn, user_id=user.id).batch_remaining == 4


def test_daily_totals_uses_local_dates(conn, user, add_items):
    (item_id,) = add_items([("le chien", "de hond")])
    fr_nl = card_ids(conn, item_id)["fr_nl"]
    state = SrsState(fsrs_state=1, step=1, stability=1.0, difficulty=1.0, due=NOW, last_review=NOW)
    # 23:30 UTC on the 17th is 01:30 on the 18th in Amsterdam
    late = datetime(2026, 9, 17, 23, 30, tzinfo=timezone.utc)
    db.save_review(conn, card_id=fr_nl, answer="x", grade="wrong", rating=1,
                   due_before=None, new_state=state, now=late, user_id=user.id)
    totals = db.daily_totals(conn, user_id=user.id, timezone_name="Europe/Amsterdam", since=late - timedelta(days=1))
    assert totals == {datetime(2026, 9, 18).date(): 1}


def test_voice_file_id_is_stored_on_the_item_and_read_back_with_the_card(conn, user, add_items):
    (item_id,) = add_items([("à pied", "te voet")])
    card = db.get_card(conn, card_ids(conn, item_id)["fr_nl"], user_id=user.id)
    assert card.voice_file_id is None

    db.save_voice(conn, item_id=item_id, file_id="AwACAgQAAxk", key="fr_FR-siwis-medium@1.0")

    card = db.get_card(conn, card.card_id, user_id=user.id)
    assert card.voice_file_id == "AwACAgQAAxk"
    assert card.voice_key == "fr_FR-siwis-medium@1.0"


def test_both_directions_of_an_item_share_the_audio(conn, user, add_items):
    (item_id,) = add_items([("à pied", "te voet")])
    db.save_voice(conn, item_id=item_id, file_id="AwACAgQAAxk", key="fr_FR-siwis-medium@1.0")

    for direction in ("fr_nl", "nl_fr"):
        card = db.get_card(conn, card_ids(conn, item_id)[direction], user_id=user.id)
        assert card.voice_file_id == "AwACAgQAAxk"


def test_the_migration_keeps_existing_progress_and_attaches_it_to_one_user(conn, add_items):
    # The conn fixture runs every migration, so 004 has been applied: there should be
    # exactly one placeholder user, and every card should hang off it.
    (item_id,) = add_items([("à pied", "te voet")])

    rows = conn.execute("SELECT DISTINCT user_id FROM french.cards").fetchall()
    assert len(rows) == 1

    owner = conn.execute("SELECT id, telegram_user_id FROM french.users").fetchall()
    assert len(owner) == 1
    assert owner[0]["telegram_user_id"] == 0  # not claimed yet
    assert rows[0]["user_id"] == owner[0]["id"]


def test_claiming_the_owner_is_idempotent(conn):
    db.claim_owner(conn, telegram_user_id=42, name="Niels")
    db.claim_owner(conn, telegram_user_id=42, name="Niels")

    users = conn.execute("SELECT telegram_user_id, name FROM french.users").fetchall()
    assert [(u["telegram_user_id"], u["name"]) for u in users] == [(42, "Niels")]


def test_sync_cards_gives_every_user_a_card_for_every_item(conn, add_items):
    db.claim_owner(conn, telegram_user_id=42, name="Niels")
    conn.execute("INSERT INTO french.users (telegram_user_id, name) VALUES (99, 'Inga')")
    add_items([("à pied", "te voet"), ("à vélo", "met de fiets")])

    db.sync_cards(conn)

    counts = conn.execute(
        "SELECT user_id, count(*) AS n FROM french.cards GROUP BY user_id ORDER BY user_id"
    ).fetchall()
    assert [row["n"] for row in counts] == [4, 4]  # 2 items x 2 directions, per user
    states = conn.execute("SELECT count(*) AS n FROM french.bot_state").fetchone()
    assert states["n"] == 2  # one open-question row per user


def test_user_overrides_default_to_none(conn):
    db.claim_owner(conn, telegram_user_id=42, name="Niels")
    conn.execute(
        "INSERT INTO french.users (telegram_user_id, name, daily_goal) VALUES (99, 'Inga', 15)"
    )

    niels, inga = db.all_users(conn)

    assert (niels.daily_goal, niels.daily_new, niels.batch_size) == (None, None, None)
    assert inga.daily_goal == 15
    assert inga.batch_size is None


def two_users(conn):
    """Two users with cards for the same words. Returns (niels, inga)."""
    db.claim_owner(conn, telegram_user_id=42, name="Niels")
    conn.execute("INSERT INTO french.users (telegram_user_id, name) VALUES (99, 'Inga')")
    return db.all_users(conn)


def test_users_do_not_see_each_others_cards(conn, add_items):
    niels, inga = two_users(conn)
    add_items([("à pied", "te voet")])

    card_id = conn.execute(
        "SELECT id FROM french.cards WHERE user_id = %s LIMIT 1", (niels.id,)
    ).fetchone()["id"]

    assert db.get_card(conn, card_id, user_id=niels.id) is not None
    assert db.get_card(conn, card_id, user_id=inga.id) is None


def test_new_cards_and_day_stats_are_per_user(conn, add_items):
    niels, inga = two_users(conn)
    add_items([("à pied", "te voet")])

    card = db.next_new_card(conn, user_id=niels.id, now=NOW, day_start=DAY_START)
    db.introduce_card(conn, card.card_id, NOW, user_id=niels.id)

    # Inga has done nothing, so her first new card is still waiting.
    assert db.next_new_card(conn, user_id=inga.id, now=NOW, day_start=DAY_START) is not None
    day_end = NOW + timedelta(days=1)
    assert db.day_stats(conn, user_id=inga.id, day_start=DAY_START, day_end=day_end).new == 0
    assert db.day_stats(conn, user_id=niels.id, day_start=DAY_START, day_end=day_end).new == 1


def test_the_pending_question_is_per_user(conn, add_items):
    niels, inga = two_users(conn)
    add_items([("à pied", "te voet")])
    card = db.next_new_card(conn, user_id=niels.id, now=NOW, day_start=DAY_START)

    assert db.set_pending_if_none(conn, card.card_id, NOW, user_id=niels.id)

    assert db.get_bot_state(conn, user_id=niels.id).pending_card_id == card.card_id
    assert db.get_bot_state(conn, user_id=inga.id).pending_card_id is None


def test_theme_progress_counts_only_this_users_cards(conn, add_items):
    niels, inga = two_users(conn)
    items = add_items([("à pied", "te voet"), ("à vélo", "met de fiets")])
    db.introduce_card(
        conn, card_ids_for(conn, items[0], niels.id)["fr_nl"], NOW, user_id=niels.id
    )

    for_niels = db.theme_progress(conn, user_id=niels.id)
    for_inga = db.theme_progress(conn, user_id=inga.id)

    assert [(t.cards, t.started) for t in for_niels] == [(4, 1)]
    assert [(t.cards, t.started) for t in for_inga] == [(4, 0)]


def card_ids_for(conn, item_id, user_id):
    rows = conn.execute(
        "SELECT direction, id FROM french.cards WHERE item_id = %s AND user_id = %s",
        (item_id, user_id),
    ).fetchall()
    return {row["direction"]: row["id"] for row in rows}


def test_marking_an_announcement_seen_never_goes_backwards(conn):
    db.claim_owner(conn, telegram_user_id=42, name="Niels")
    (user,) = db.all_users(conn)
    assert user.last_announcement == 0

    db.mark_announcement_seen(conn, user_id=user.id, announcement_id=3)
    db.mark_announcement_seen(conn, user_id=user.id, announcement_id=1)

    assert db.all_users(conn)[0].last_announcement == 3
