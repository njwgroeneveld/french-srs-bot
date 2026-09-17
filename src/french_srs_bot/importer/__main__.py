"""Importer CLI.

    python -m french_srs_bot.importer fetch https://french.kwiziq.com/learn/theme/128 --level A0 --position 1
    python -m french_srs_bot.importer load data/A0/theme-128.yaml [more files...]

`fetch` needs ANTHROPIC_API_KEY, `load` needs DATABASE_URL (both may come from a local .env file).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from .. import db
from .kwiziq import fetch_html, parse_theme, source_ref_from_url
from .suggest import build_theme_file, request_suggestions
from .theme_file import load_theme, read_theme_file, write_theme_file


def cmd_fetch(args: argparse.Namespace) -> None:
    source_ref = source_ref_from_url(args.url)
    parsed = parse_theme(fetch_html(args.url))
    suggestions = request_suggestions(anthropic.Anthropic(), parsed)
    theme = build_theme_file(
        parsed, suggestions, source_ref=source_ref, level=args.level, position=args.position
    )
    target = Path(args.out) / args.level / f"{source_ref.replace('/', '-')}.yaml"
    write_theme_file(target, theme)
    print(f"wrote {target} ({len(theme.items)} items) - review it, then run: load {target}")


def cmd_load(args: argparse.Namespace) -> None:
    with db.connect(os.environ["DATABASE_URL"]) as conn:
        db.run_migrations(conn)
        for path in args.files:
            count = load_theme(conn, read_theme_file(Path(path)))
            print(f"loaded {path}: {count} items")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m french_srs_bot.importer")
    commands = parser.add_subparsers(required=True)

    fetch = commands.add_parser("fetch", help="fetch a kwiziq theme and write a YAML file to review")
    fetch.add_argument("url")
    fetch.add_argument("--level", required=True)
    fetch.add_argument("--position", type=int, required=True, help="order in which themes are introduced")
    fetch.add_argument("--out", default="data")
    fetch.set_defaults(func=cmd_fetch)

    load = commands.add_parser("load", help="load reviewed YAML files into the database")
    load.add_argument("files", nargs="+")
    load.set_defaults(func=cmd_load)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
