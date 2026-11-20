"""Write sentence themes (kind: sentence) from the chapter notes in the Obsidian vault.

Each chapter note (frontmatter `type: hoofdstuk`, `unite: N`) has under `## Communication` one
`### <goal>` per learning goal, with the book's sentences on a line `- zinnen: a · b · c`. This
writes one YAML theme per goal with `dutch: [TODO]` for every sentence; the translations are
filled in by hand, and read_theme_file refuses the file until they are.

Usage:
    python tools/gen_sentences.py VAULT_DIR OUT_DIR --unites 7 --chapter "Zinnen U7" --start-position 2001
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from french_srs_bot.grading import normalize

REPO = Path(__file__).resolve().parents[1]
LEVEL = "A1.2"


class AlreadyTranslated(Exception):
    """An output file already holds translations; overwriting it would throw that work away."""


@dataclass
class Goal:
    title: str
    sentences: list[str]


@dataclass
class Chapter:
    unite: int
    note: str  # the note's file name without .md, the first half of `lesson`
    goals: list[Goal]


@dataclass(frozen=True)
class Skipped:
    unite: int
    goal: str
    french: str


@dataclass(frozen=True)
class Written:
    path: Path
    name: str
    position: int
    count: int


@dataclass
class Result:
    written: list[Written] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return {}, text
    meta = yaml.safe_load(parts[0][3:]) or {}
    body = parts[1].split("\n", 1)[1] if "\n" in parts[1] else ""
    return (meta if isinstance(meta, dict) else {}), body


def _goals(body: str) -> list[Goal]:
    """The `### goal` blocks under `## Communication`, with their `zinnen:` line split."""
    goals: list[Goal] = []
    in_communication = False
    for line in body.splitlines():
        if line.startswith("## "):
            in_communication = line[3:].strip() == "Communication"
            continue
        if not in_communication:
            continue
        if line.startswith("### "):
            goals.append(Goal(title=line[4:].strip(), sentences=[]))
        elif goals and line.strip().startswith("- zinnen:"):
            value = line.strip()[len("- zinnen:"):]
            # split on the dot alone: a trailing " · " loses its space to strip()
            goals[-1].sentences.extend(s.strip() for s in value.split("·") if s.strip())
    return goals


def collect(vault_dir: Path) -> list[Chapter]:
    """Every chapter note in the vault, in unit order, with its sentences per goal."""
    chapters = []
    for path in Path(vault_dir).rglob("*.md"):
        meta, body = _frontmatter(path.read_text(encoding="utf-8"))
        if meta.get("type") != "hoofdstuk" or meta.get("unite") is None:
            continue
        chapters.append(Chapter(unite=int(meta["unite"]), note=path.stem, goals=_goals(body)))
    return sorted(chapters, key=lambda c: c.unite)


def _french_of(item: dict) -> str | None:
    french = item.get("french")
    if isinstance(french, list) and french and isinstance(french[0], str):
        return french[0]
    sentence, gap = item.get("sentence"), item.get("gap", item.get("gap_answer"))
    if isinstance(sentence, str) and isinstance(gap, str):  # grammar items carry it in parts
        return sentence.replace("___", gap)
    return None


def known_french(data_dir: Path, exclude: Path | None = None) -> set[str]:
    """The normalized first French answer of every item in data/**/*.yaml.

    `exclude` leaves out a directory, so a rerun does not skip the sentences it wrote itself.
    """
    excluded = Path(exclude).resolve() if exclude else None
    known: set[str] = set()
    for path in Path(data_dir).rglob("*.yaml"):
        if excluded and path.resolve().is_relative_to(excluded):
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            continue
        for item in raw.get("items") or []:
            french = _french_of(item) if isinstance(item, dict) else None
            if french:
                known.add(normalize(french))
    return known


def _is_translated(path: Path) -> bool:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return any(item.get("dutch") != ["TODO"] for item in raw.get("items") or [])


def write_themes(
    vault_dir: Path,
    out_dir: Path,
    *,
    unites: set[int],
    chapter: str,
    start_position: int,
    known: set[str],
) -> Result:
    """One YAML theme per goal of the chosen units. Sentences in `known`, or seen earlier in
    this run, are left out and reported. Refuses (before writing anything) to overwrite a file
    whose translations have already been filled in."""
    out_dir = Path(out_dir)
    seen = set(known)
    result = Result()
    planned: list[tuple[Path, dict]] = []
    position = start_position
    for chap in collect(vault_dir):
        if chap.unite not in unites:
            continue
        for goal in chap.goals:
            sentences = []
            for sentence in goal.sentences:
                key = normalize(sentence)
                if key in seen:
                    result.skipped.append(Skipped(chap.unite, goal.title, sentence))
                    continue
                seen.add(key)
                sentences.append(sentence)
            if not sentences:
                continue
            ref = f"u{chap.unite}-{slug(goal.title)}"
            name = f"{chapter} · {goal.title}"
            theme = {
                "source": "vault",
                "source_ref": f"sentences/u{chap.unite}/{slug(goal.title)}",
                "level": LEVEL,
                "name": name,
                "position": position,
                "lesson": f"{chap.note}#{goal.title}",
                "kind": "sentence",
                "items": [{"french": [s], "dutch": ["TODO"]} for s in sentences],
            }
            path = out_dir / f"{ref}.yaml"
            planned.append((path, theme))
            result.written.append(Written(path, name, position, len(sentences)))
            position += 1
    translated = [path.name for path, _ in planned if path.exists() and _is_translated(path)]
    if translated:
        raise AlreadyTranslated(", ".join(translated))
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, theme in planned:
        text = yaml.safe_dump(theme, allow_unicode=True, sort_keys=False, width=100)
        path.write_text(text, encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("vault_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--unites", required=True, help="comma-separated, e.g. 7 or 4,5,6")
    parser.add_argument("--chapter", required=True, help='e.g. "Zinnen U7" (the /focus name)')
    parser.add_argument("--start-position", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, default=REPO / "data",
                        help="existing theme YAML; sentences already in there are skipped")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # French text on a Windows console (cp1252) otherwise
        stream.reconfigure(encoding="utf-8")
    unites = {int(u) for u in args.unites.split(",") if u.strip()}
    known = known_french(args.data_dir, exclude=args.out_dir)
    try:
        result = write_themes(args.vault_dir, args.out_dir, unites=unites, chapter=args.chapter,
                              start_position=args.start_position, known=known)
    except AlreadyTranslated as error:
        print(f"weigert: al vertaald, niet overschreven: {error}", file=sys.stderr)
        return 1
    for theme in result.written:
        print(f"{theme.position}  {theme.name}: {theme.count} zinnen  -> {theme.path}")
    if result.skipped:
        print(f"\n{len(result.skipped)} overgeslagen (staan al in de bot of eerder in deze run):")
        for s in result.skipped:
            print(f"  U{s.unite} · {s.goal}: {s.french}")
    if not result.written:
        print("geen zinnen gevonden", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
