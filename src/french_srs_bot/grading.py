"""Grade a typed answer against the accepted answers of a card."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

Lang = Literal["fr", "nl"]


class Grade(StrEnum):
    CORRECT = "correct"
    ALMOST = "almost"
    HARD = "hard"
    WRONG = "wrong"


Reason = Literal["exact", "accent", "article", "typo", "wrong"]

_RANK = {Grade.CORRECT: 3, Grade.ALMOST: 2, Grade.HARD: 1, Grade.WRONG: 0}

# Each article includes its separator ("le " vs "l'"), so "le " never matches "les chats".
ARTICLES: dict[str, tuple[str, ...]] = {
    "fr": ("le ", "la ", "les ", "l'", "un ", "une ", "des "),
    "nl": ("de ", "het ", "een "),
}


@dataclass(frozen=True)
class GradeResult:
    grade: Grade
    reason: Reason
    expected: str  # the accepted answer closest to what was typed


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = (
        text.strip()
        .lower()
        .replace("’", "'")
        .replace("‘", "'")
        .replace("ʼ", "'")
        .replace("`", "'")
    )
    text = re.sub(r"\s+", " ", text)
    if text != "?":
        text = text.rstrip(".!?,;").rstrip()
    return text


def strip_accents(text: str) -> str:
    text = text.replace("œ", "oe").replace("æ", "ae")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def strip_article(text: str, lang: Lang) -> str:
    for article in ARTICLES[lang]:
        if text.startswith(article):
            return text[len(article):].strip()
    return text


def levenshtein(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (char_a != char_b),
                )
            )
        previous = current
    return previous[-1]


def _compare(answer: str, expected: str, lang: Lang, typo_min_length: int) -> tuple[Grade, Reason]:
    if answer == expected:
        return Grade.CORRECT, "exact"
    answer_plain, expected_plain = strip_accents(answer), strip_accents(expected)
    if answer_plain == expected_plain:
        return Grade.ALMOST, "accent"
    answer_bare = strip_article(answer_plain, lang)
    expected_bare = strip_article(expected_plain, lang)
    if answer_bare == expected_bare:
        return Grade.HARD, "article"
    if len(expected_bare) >= typo_min_length and levenshtein(answer_bare, expected_bare) == 1:
        return Grade.HARD, "typo"
    return Grade.WRONG, "wrong"


def grade(answer: str, accepted: list[str], lang: Lang, typo_min_length: int = 4) -> GradeResult:
    if not accepted:
        raise ValueError("a card needs at least one accepted answer")
    typed = normalize(answer)
    if typed in ("", "?"):
        return GradeResult(Grade.WRONG, "wrong", accepted[0])
    best = GradeResult(Grade.WRONG, "wrong", accepted[0])
    for expected in accepted:
        result, reason = _compare(typed, normalize(expected), lang, typo_min_length)
        if _RANK[result] > _RANK[best.grade]:
            best = GradeResult(result, reason, expected)
    return best
