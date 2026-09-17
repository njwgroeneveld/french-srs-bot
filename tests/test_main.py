import psycopg
import pytest

from french_srs_bot import __main__ as entry


def test_migrate_with_retries_returns_applied_versions(conn, monkeypatch):
    conn.execute("DROP SCHEMA french CASCADE")
    monkeypatch.setattr(entry.db, "connect", lambda url: conn)
    assert entry.migrate_with_retries("postgresql://ignored", attempts=1) == [
        "001_initial",
        "002_theme_lesson",
    ]


def test_migrate_with_retries_retries_then_succeeds(conn, monkeypatch):
    conn.execute("DROP SCHEMA french CASCADE")
    calls = []

    def flaky(url):
        calls.append(url)
        if len(calls) < 3:
            raise psycopg.OperationalError("connection timeout expired")
        return conn

    monkeypatch.setattr(entry.db, "connect", flaky)
    monkeypatch.setattr(entry.time, "sleep", lambda _seconds: None)
    assert entry.migrate_with_retries("postgresql://ignored", attempts=5) == [
        "001_initial",
        "002_theme_lesson",
    ]
    assert len(calls) == 3


def test_migrate_with_retries_gives_up(monkeypatch):
    def always_fails(url):
        raise psycopg.OperationalError("connection timeout expired")

    monkeypatch.setattr(entry.db, "connect", always_fails)
    monkeypatch.setattr(entry.time, "sleep", lambda _seconds: None)
    with pytest.raises(psycopg.OperationalError):
        entry.migrate_with_retries("postgresql://ignored", attempts=3)
