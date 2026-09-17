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
    items: list[ThemeItem] = field(default_factory=list)


def write_theme_file(path: Path, theme: ThemeFile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(asdict(theme), allow_unicode=True, sort_keys=False, width=100)
    path.write_text(text, encoding="utf-8")


def read_theme_file(path: Path) -> ThemeFile:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = []
    for number, item in enumerate(raw["items"], start=1):
        french, dutch = item.get("french"), item.get("dutch")
        if not french or not dutch:
            raise ValueError(f"{path}: item {number} needs at least one french and one dutch answer")
        if item.get("gender") not in (None, "m", "f"):
            raise ValueError(f"{path}: item {number} has gender {item['gender']!r}, use m, f or null")
        items.append(
            ThemeItem(
                french=[str(v) for v in french],
                dutch=[str(v) for v in dutch],
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
        items=items,
    )


def load_theme(conn: psycopg.Connection, theme: ThemeFile) -> int:
    """Upsert the theme and its items. Returns the number of items loaded."""
    theme_id = db.upsert_theme(
        conn,
        source=theme.source,
        source_ref=theme.source_ref,
        level=theme.level,
        name=theme.name,
        position=theme.position,
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
    return len(theme.items)
