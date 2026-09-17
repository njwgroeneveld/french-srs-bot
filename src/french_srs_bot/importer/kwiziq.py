"""Fetch and parse a kwiziq vocabulary theme page (https://french.kwiziq.com/learn/theme/<id>)."""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from bs4 import BeautifulSoup

_GENDER_MARKER = re.compile(r"\s*\((?:m|f|masc|fem)\)\s*$", re.IGNORECASE)
_THEME_PATH = re.compile(r"^/learn/(theme/\d+)/?$")


@dataclass(frozen=True)
class Entry:
    french: str
    english: str


@dataclass(frozen=True)
class ParsedTheme:
    name: str
    entries: list[Entry]


def source_ref_from_url(url: str) -> str:
    match = _THEME_PATH.match(urlparse(url).path)
    if not match:
        raise ValueError(f"not a kwiziq theme url: {url}")
    return match.group(1)


def fetch_html(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "french-srs-bot importer (personal use)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def parse_theme(html: str) -> ParsedTheme:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    if heading is None:
        raise ValueError("no <h1> found; is this a kwiziq theme page?")
    name = re.sub(r"\s+in French$", "", heading.get_text(" ", strip=True))
    entries = []
    for row in soup.select("table.table-vocab-list tbody tr"):
        foreign = row.select_one(".txt--lang-foreign")
        native = row.select_one(".txt--lang-native")
        if foreign is None or native is None:
            continue
        french = _GENDER_MARKER.sub("", foreign.get_text(" ", strip=True))
        entries.append(Entry(french=french, english=native.get_text(" ", strip=True)))
    if not entries:
        raise ValueError("no vocabulary rows found")
    return ParsedTheme(name=name, entries=entries)
