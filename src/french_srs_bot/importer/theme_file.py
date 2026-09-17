"""The reviewable YAML file between fetching a theme and loading it into the database."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import psycopg
import yaml

from .. import db


@dataclass
class ThemeItem:
    french: list[str]
    dutch: list[str]
    english: str | None = None
    gender: str | None = None
    hint: str | None = None


@dataclass
class ThemeFile:
    source: str
    source_ref: str
    level: str | None
    name: str
    position: int
    lesson: str | None = None  # e.g. the Obsidian lesson note this theme comes from
    items: list[ThemeItem] = field(default_factory=list)


def write_theme_file(path: Path, theme: ThemeFile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(asdict(theme), allow_unicode=True, sort_keys=False, width=100)
    path.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class LoadResult:
    loaded: int
    stale: list[str]  # first French answers of items in the database that are no longer in the file


def _is_answer_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) and v.strip() for v in value)


def read_theme_file(path: Path) -> ThemeFile:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw.get("lesson"), (str, type(None))):
        raise ValueError(f"{path}: lesson must be a string or null, got {raw['lesson']!r}")
    items = []
    seen: set[str] = set()
    for number, item in enumerate(raw["items"], start=1):
        french, dutch = item.get("french"), item.get("dutch")
        for key, value in (("french", french), ("dutch", dutch)):
            if not _is_answer_list(value):
                raise ValueError(
                    f"{path}: item {number} {key} must be a non-empty list of strings, got {value!r}"
                    " (quote words like on/yes/no)"
                )
        for key in ("english", "hint"):
            if not isinstance(item.get(key), (str, type(None))):
                raise ValueError(f"{path}: item {number} {key} must be a string or null, got {item[key]!r}")
        if item.get("gender") not in (None, "m", "f"):
            raise ValueError(f"{path}: item {number} has gender {item['gender']!r}, use m, f or null")
        if french[0] in seen:
            raise ValueError(f"{path}: item {number} repeats french {french[0]!r}")
        seen.add(french[0])
        items.append(
            ThemeItem(
                french=list(french),
                dutch=list(dutch),
                english=item.get("english"),
                gender=item.get("gender"),
                hint=item.get("hint") or None,
            )
        )
    return ThemeFile(
        source=raw["source"],
        source_ref=str(raw["source_ref"]),
        level=raw.get("level"),
        name=raw["name"],
        position=int(raw["position"]),
        lesson=raw.get("lesson"),
        items=items,
    )


def load_theme(conn: psycopg.Connection, theme: ThemeFile) -> LoadResult:
    """Upsert the theme and its items in one transaction.

    Items of the theme that are no longer in the file are reported as stale, never deleted.
    """
    with conn.transaction():
        theme_id = db.upsert_theme(
            conn,
            source=theme.source,
            source_ref=theme.source_ref,
            level=theme.level,
            name=theme.name,
            position=theme.position,
            lesson=theme.lesson,
        )
        for position, item in enumerate(theme.items, start=1):
            db.upsert_item(
                conn,
                theme_id=theme_id,
                position=position,
                french=item.french,
                dutch=item.dutch,
                english=item.english,
                gender=item.gender,
                hint=item.hint,
            )
        rows = conn.execute(
            "SELECT french[1] AS french FROM french.items WHERE theme_id = %s AND NOT (french[1] = ANY(%s))"
            " ORDER BY position, id",
            (theme_id, [item.french[0] for item in theme.items]),
        ).fetchall()
    return LoadResult(loaded=len(theme.items), stale=[row["french"] for row in rows])
