"""All database access: connections, migrations and queries on schema `french`."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .models import BotState, CardView, DayStats, SrsState, ThemeProgress, User

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"


def connect(url: str) -> psycopg.Connection:
    # prepare_threshold=None: no server-side prepared statements, required by Supabase's transaction pooler.
    # Timeouts/keepalives so a dead network fails fast instead of hanging a handler. No statement_timeout:
    # the transaction pooler rejects the `options` startup parameter and does not keep session SETs.
    return psycopg.connect(
        url,
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=None,
        connect_timeout=30,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )


def run_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply all not yet applied *.sql files in filename order. Returns the versions applied now.

    All pending migrations run in one transaction, so migration files must not contain
    transaction control (BEGIN/COMMIT/ROLLBACK).
    """
    applied: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('french_srs_bot_migrations'))")
        conn.execute("CREATE SCHEMA IF NOT EXISTS french")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS french.schema_migrations ("
            " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {row["version"] for row in conn.execute("SELECT version FROM french.schema_migrations")}
        for path in sorted(directory.glob("*.sql")):
            if path.stem in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO french.schema_migrations (version) VALUES (%s)", (path.stem,))
            applied.append(path.stem)
    return applied


# --- users ---------------------------------------------------------------------------------

_USER_COLUMNS = "id, telegram_user_id, name, daily_goal, daily_new, batch_size, last_announcement"


def _user_from_row(row: dict) -> User:
    return User(
        id=row["id"],
        telegram_user_id=row["telegram_user_id"],
        name=row["name"],
        daily_goal=row["daily_goal"],
        daily_new=row["daily_new"],
        batch_size=row["batch_size"],
        last_announcement=row["last_announcement"],
    )


def claim_owner(conn: psycopg.Connection, *, telegram_user_id: int, name: str) -> None:
    """Give the placeholder from migration 004 its real Telegram id, once.

    The migration attached all pre-existing progress to a row with telegram_user_id = 0
    because SQL cannot know the real id. After the first claim this is a no-op.
    """
    with conn.transaction():
        conn.execute(
            "UPDATE french.users SET telegram_user_id = %s, name = %s WHERE telegram_user_id = 0",
            (telegram_user_id, name),
        )
        conn.execute(
            "INSERT INTO french.users (telegram_user_id, name) VALUES (%s, %s)"
            " ON CONFLICT (telegram_user_id) DO NOTHING",
            (telegram_user_id, name),
        )


def all_users(conn: psycopg.Connection) -> list[User]:
    rows = conn.execute(
        f"SELECT {_USER_COLUMNS} FROM french.users WHERE telegram_user_id <> 0 ORDER BY id"
    ).fetchall()
    return [_user_from_row(row) for row in rows]


def user_by_telegram_id(conn: psycopg.Connection, telegram_user_id: int) -> User | None:
    row = conn.execute(
        f"SELECT {_USER_COLUMNS} FROM french.users WHERE telegram_user_id = %s",
        (telegram_user_id,),
    ).fetchone()
    return _user_from_row(row) if row else None


def mark_announcement_seen(conn: psycopg.Connection, *, user_id: int, announcement_id: int) -> None:
    """Record that this user has received everything up to `announcement_id`.

    Never moves backwards: an older id arriving late must not make a user eligible again.
    """
    conn.execute(
        "UPDATE french.users SET last_announcement = %s WHERE id = %s AND last_announcement < %s",
        (announcement_id, user_id, announcement_id),
    )


def sync_cards(conn: psycopg.Connection) -> int:
    """Make sure every user has a card for every item in both directions, and a state row.

    Runs at startup. This is why upsert_item no longer creates cards itself: with more than
    one user, whoever adds an item cannot know who needs a card for it. Missing rows are
    filled in here instead, which also repairs a hand-written SQL import.
    """
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO french.bot_state (user_id) SELECT id FROM french.users
            ON CONFLICT (user_id) DO NOTHING
            """
        )
        cursor = conn.execute(
            """
            INSERT INTO french.cards (user_id, item_id, direction)
            SELECT u.id, i.id, d.direction
            FROM french.users u
            CROSS JOIN french.items i
            CROSS JOIN (VALUES ('fr_nl'), ('nl_fr')) AS d(direction)
            ON CONFLICT (user_id, item_id, direction) DO NOTHING
            """
        )
    return cursor.rowcount


# --- content -------------------------------------------------------------------------------


def upsert_theme(
    conn: psycopg.Connection,
    *,
    source: str,
    source_ref: str,
    level: str | None,
    name: str,
    position: int,
    lesson: str | None = None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO french.themes (source, source_ref, level, name, position, lesson)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, source_ref)
        DO UPDATE SET level = EXCLUDED.level, name = EXCLUDED.name, position = EXCLUDED.position,
                      lesson = EXCLUDED.lesson
        RETURNING id
        """,
        (source, source_ref, level, name, position, lesson),
    ).fetchone()
    return row["id"]


def upsert_item(
    conn: psycopg.Connection,
    *,
    theme_id: int,
    position: int,
    french: list[str],
    dutch: list[str],
    english: str | None,
    gender: str | None,
    hint: str | None,
) -> int:
    """Insert or update an item. Cards are not created here: with more than one user that is
    sync_cards's job, at startup."""
    row = conn.execute(
        """
        INSERT INTO french.items (theme_id, position, french, dutch, english, gender, hint)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (theme_id, (french[1]))
        DO UPDATE SET position = EXCLUDED.position, french = EXCLUDED.french, dutch = EXCLUDED.dutch,
                      english = EXCLUDED.english, gender = EXCLUDED.gender, hint = EXCLUDED.hint
        RETURNING id
        """,
        (theme_id, position, french, dutch, english, gender, hint),
    ).fetchone()
    return row["id"]


def save_voice(conn: psycopg.Connection, *, item_id: int, file_id: str, key: str) -> None:
    """Store Telegram's handle for this item's audio, with the voice+tempo it was made with."""
    conn.execute(
        "UPDATE french.items SET voice_file_id = %s, voice_key = %s WHERE id = %s",
        (file_id, key, item_id),
    )


# --- cards ---------------------------------------------------------------------------------

_CARD_SELECT = """
    SELECT c.id AS card_id, c.item_id, c.direction, c.introduced_at, c.due, c.fsrs_state, c.step,
           c.stability, c.difficulty, c.last_review, i.french, i.dutch, i.gender, i.hint,
           i.voice_file_id, i.voice_key
    FROM french.cards c
    JOIN french.items i ON i.id = c.item_id
    JOIN french.themes t ON t.id = i.theme_id
"""

# The other direction of the same item was already reviewed today -> skip this card today.
_SIBLING_NOT_REVIEWED_TODAY = """
    NOT EXISTS (
        SELECT 1 FROM french.cards s JOIN french.reviews r ON r.card_id = s.id
        WHERE s.item_id = c.item_id AND s.id <> c.id AND s.user_id = c.user_id
          AND r.reviewed_at >= %(day_start)s
    )
"""


def _card_from_row(row: dict) -> CardView:
    srs = None
    if row["fsrs_state"] is not None:
        srs = SrsState(
            fsrs_state=row["fsrs_state"],
            step=row["step"],
            stability=row["stability"],
            difficulty=row["difficulty"],
            due=row["due"],
            last_review=row["last_review"],
        )
    return CardView(
        card_id=row["card_id"],
        item_id=row["item_id"],
        direction=row["direction"],
        french=list(row["french"]),
        dutch=list(row["dutch"]),
        gender=row["gender"],
        hint=row["hint"],
        introduced_at=row["introduced_at"],
        srs=srs,
        voice_file_id=row["voice_file_id"],
        voice_key=row["voice_key"],
    )


def get_card(conn: psycopg.Connection, card_id: int, *, user_id: int) -> CardView | None:
    row = conn.execute(
        _CARD_SELECT + " WHERE c.id = %(card_id)s AND c.user_id = %(user_id)s",
        {"card_id": card_id, "user_id": user_id},
    ).fetchone()
    return _card_from_row(row) if row else None


def due_cards(
    conn: psycopg.Connection, *, user_id: int, now: datetime, day_start: datetime
) -> list[CardView]:
    rows = conn.execute(
        _CARD_SELECT
        + """
        WHERE c.user_id = %(user_id)s
          AND c.introduced_at IS NOT NULL AND COALESCE(c.due, c.introduced_at) <= %(now)s AND
        """
        + _SIBLING_NOT_REVIEWED_TODAY
        + " ORDER BY COALESCE(c.due, c.introduced_at), c.id",
        {"now": now, "day_start": day_start, "user_id": user_id},
    ).fetchall()
    return [_card_from_row(row) for row in rows]


def next_new_card(
    conn: psycopg.Connection, *, user_id: int, now: datetime, day_start: datetime
) -> CardView | None:
    row = conn.execute(
        _CARD_SELECT
        + """
        WHERE c.user_id = %(user_id)s
          AND c.introduced_at IS NULL
          AND """
        + _SIBLING_NOT_REVIEWED_TODAY
        + """
          AND (
              c.direction = 'fr_nl'
              OR EXISTS (
                  SELECT 1 FROM french.cards s
                  WHERE s.item_id = c.item_id AND s.direction = 'fr_nl' AND s.user_id = c.user_id
                    AND s.introduced_at IS NOT NULL AND s.introduced_at < %(day_start)s
                    AND (s.due IS NOT NULL AND s.due > %(now)s)
              )
          )
        ORDER BY t.position, i.position, c.direction = 'nl_fr', c.id
        LIMIT 1
        """,
        {"now": now, "day_start": day_start, "user_id": user_id},
    ).fetchone()
    return _card_from_row(row) if row else None


def introduce_card(conn: psycopg.Connection, card_id: int, now: datetime, *, user_id: int) -> None:
    conn.execute(
        "UPDATE french.cards SET introduced_at = %s WHERE id = %s AND user_id = %s AND introduced_at IS NULL",
        (now, card_id, user_id),
    )


def save_review(
    conn: psycopg.Connection,
    *,
    card_id: int,
    answer: str,
    grade: str,
    rating: int,
    due_before: datetime | None,
    new_state: SrsState,
    now: datetime,
    user_id: int,
) -> None:
    """Store the new schedule, log the review and clear the pending card, atomically."""
    with conn.transaction():
        conn.execute(
            """
            UPDATE french.cards
            SET due = %s, fsrs_state = %s, step = %s, stability = %s, difficulty = %s, last_review = %s
            WHERE id = %s AND user_id = %s
            """,
            (
                new_state.due,
                new_state.fsrs_state,
                new_state.step,
                new_state.stability,
                new_state.difficulty,
                new_state.last_review,
                card_id,
                user_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO french.reviews (card_id, reviewed_at, answer, grade, rating, due_before, due_after)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (card_id, now, answer, grade, rating, due_before, new_state.due),
        )
        conn.execute(
            """
            UPDATE french.bot_state
            SET pending_card_id = NULL, pending_since = NULL, batch_remaining = GREATEST(batch_remaining - 1, 0)
            WHERE user_id = %s AND pending_card_id = %s
            """,
            (user_id, card_id),
        )


# --- statistics ----------------------------------------------------------------------------


def theme_progress(conn: psycopg.Connection, *, user_id: int) -> list[ThemeProgress]:
    """Every theme in the order new cards are drawn from it, with this user's progress."""
    rows = conn.execute(
        """
        SELECT t.position, t.name, count(c.id) AS cards,
               count(*) FILTER (WHERE c.introduced_at IS NOT NULL) AS started
        FROM french.themes t
        JOIN french.items i ON i.theme_id = t.id
        JOIN french.cards c ON c.item_id = i.id AND c.user_id = %(user)s
        GROUP BY t.id, t.position, t.name
        ORDER BY t.position
        """,
        {"user": user_id},
    ).fetchall()
    return [
        ThemeProgress(
            position=row["position"], name=row["name"], cards=row["cards"], started=row["started"]
        )
        for row in rows
    ]



def day_stats(
    conn: psycopg.Connection, *, user_id: int, day_start: datetime, day_end: datetime
) -> DayStats:
    row = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM french.reviews r JOIN french.cards c ON c.id = r.card_id
             WHERE c.user_id = %(user)s
               AND r.reviewed_at >= %(start)s AND r.reviewed_at < %(end)s) AS total,
            (SELECT count(*) FROM french.cards
             WHERE user_id = %(user)s
               AND introduced_at >= %(start)s AND introduced_at < %(end)s) AS new,
            (SELECT count(*) FROM french.reviews r JOIN french.cards c ON c.id = r.card_id
             WHERE c.user_id = %(user)s
               AND r.reviewed_at >= %(start)s AND r.reviewed_at < %(end)s
               AND c.introduced_at < %(start)s) AS reviews
        """,
        {"start": day_start, "end": day_end, "user": user_id},
    ).fetchone()
    return DayStats(total=row["total"], new=row["new"], reviews=row["reviews"])


def daily_totals(
    conn: psycopg.Connection, *, user_id: int, timezone_name: str, since: datetime
) -> dict[date, int]:
    rows = conn.execute(
        """
        SELECT (r.reviewed_at AT TIME ZONE %(tz)s)::date AS day, count(*) AS total
        FROM french.reviews r JOIN french.cards c ON c.id = r.card_id
        WHERE c.user_id = %(user)s AND r.reviewed_at >= %(since)s
        GROUP BY 1
        """,
        {"tz": timezone_name, "since": since, "user": user_id},
    ).fetchall()
    return {row["day"]: row["total"] for row in rows}


# --- bot state -----------------------------------------------------------------------------


def get_bot_state(conn: psycopg.Connection, *, user_id: int) -> BotState:
    row = conn.execute(
        "SELECT pending_card_id, batch_remaining FROM french.bot_state WHERE user_id = %s", (user_id,)
    ).fetchone()
    return BotState(pending_card_id=row["pending_card_id"], batch_remaining=row["batch_remaining"])


def set_pending(conn: psycopg.Connection, card_id: int, now: datetime, *, user_id: int) -> None:
    conn.execute(
        "UPDATE french.bot_state SET pending_card_id = %s, pending_since = %s WHERE user_id = %s",
        (card_id, now, user_id),
    )


def set_pending_if_none(conn: psycopg.Connection, card_id: int, now: datetime, *, user_id: int) -> bool:
    """Make `card_id` pending only when no card is pending. Returns whether it was set."""
    cursor = conn.execute(
        "UPDATE french.bot_state SET pending_card_id = %s, pending_since = %s"
        " WHERE user_id = %s AND pending_card_id IS NULL",
        (card_id, now, user_id),
    )
    return cursor.rowcount == 1


def set_batch_remaining(conn: psycopg.Connection, remaining: int, *, user_id: int) -> None:
    conn.execute(
        "UPDATE french.bot_state SET batch_remaining = %s WHERE user_id = %s", (remaining, user_id)
    )
