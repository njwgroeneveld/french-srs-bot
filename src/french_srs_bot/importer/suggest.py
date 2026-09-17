"""Ask Claude for Dutch translations, gender and hints. The result is always reviewed by a human."""

from __future__ import annotations

import json
from typing import Literal

import anthropic
from pydantic import BaseModel

from .kwiziq import ParsedTheme
from .theme_file import ThemeFile, ThemeItem

MODEL = "claude-opus-5"

SYSTEM_PROMPT = """\
You help a Dutch speaker learn French vocabulary. For each French entry (with its English gloss) give:
- dutch: the Dutch translation(s) a learner should type. For nouns include the Dutch article (de/het).
  Put the most common translation first; add alternatives only when they are equally correct.
  If the English gloss marks male/female (e.g. "the cat (female)"), express that in Dutch where natural
  (e.g. "de poes"), otherwise keep the plain translation.
- gender: "m" or "f" for French nouns and for adjectives/nationalities listed in a specific gender; "none" otherwise.
- hint: a very short Dutch hint shown with the Dutch prompt, only when the Dutch alone is ambiguous about
  which French form is expected (e.g. "vrouwelijk" for "allemande" when "allemand" also exists). Otherwise "".
Return the entries in the same order, copying each French text exactly."""


class Suggestion(BaseModel):
    french: str
    dutch: list[str]
    gender: Literal["m", "f", "none"]
    hint: str


class Suggestions(BaseModel):
    items: list[Suggestion]


def request_suggestions(client: anthropic.Anthropic, theme: ParsedTheme) -> list[Suggestion]:
    payload = json.dumps(
        {"theme": theme.name, "entries": [{"french": e.french, "english": e.english} for e in theme.entries]},
        ensure_ascii=False,
    )
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": payload}],
        output_format=Suggestions,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError(f"Claude returned no suggestions (stop_reason={response.stop_reason})")
    return response.parsed_output.items


def build_theme_file(
    theme: ParsedTheme,
    suggestions: list[Suggestion],
    *,
    source_ref: str,
    level: str,
    position: int,
) -> ThemeFile:
    if [s.french for s in suggestions] != [e.french for e in theme.entries]:
        raise ValueError("suggestions do not match the parsed entries one-to-one")
    items = [
        ThemeItem(
            french=[entry.french],
            dutch=suggestion.dutch,
            english=entry.english,
            gender=None if suggestion.gender == "none" else suggestion.gender,
            hint=suggestion.hint or None,
        )
        for entry, suggestion in zip(theme.entries, suggestions)
    ]
    return ThemeFile(
        source="kwiziq", source_ref=source_ref, level=level, name=theme.name, position=position, items=items
    )
