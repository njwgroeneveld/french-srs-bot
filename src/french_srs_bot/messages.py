"""All user-facing text (Dutch). Telegram parse mode: HTML."""

from __future__ import annotations

from html import escape

from .grading import GradeResult
from .models import CardView, DayStats

BUTTON_UNDERSTOOD = "👍 Begrepen"

_FLAGS = {"fr_nl": "🇫🇷 → 🇳🇱", "nl_fr": "🇳🇱 → 🇫🇷"}
_GENDER = {"m": "mannelijk", "f": "vrouwelijk"}


def _gender_note(card: CardView) -> str:
    return f" <i>({_GENDER[card.gender]})</i>" if card.gender in _GENDER else ""


def _answer_line(card: CardView) -> str:
    return f"<b>{escape(card.french[0])}</b>{_gender_note(card)} = <b>{escape(card.dutch[0])}</b>"


def welcome() -> str:
    return (
        "👋 <b>Bonjour !</b>\n\n"
        "Ik overhoor je elke dag Franse woordjes, in setjes om 08:00, 13:00 en 19:00.\n"
        "Typ het antwoord gewoon als bericht. Wil je tussendoor oefenen? Stuur /practice."
    )


def intro(card: CardView) -> str:
    return f"🆕 <b>Nieuw woord</b>\n\n{_answer_line(card)}"


def prompt(card: CardView) -> str:
    text = f"{_FLAGS[card.direction]}\n\n<b>{escape(card.question)}</b>"
    if card.direction == "nl_fr" and card.hint:
        text += f"\n<i>({escape(card.hint)})</i>"
    return text


def feedback(result: GradeResult, card: CardView) -> str:
    expected = f"<b>{escape(result.expected)}</b>"
    if result.reason == "exact":
        return "✅ Parfait !"
    if result.reason == "accent":
        return f"🟡 Bijna! Let op de accenten: {expected}"
    if result.reason == "article":
        return f"🟠 Het woord klopt, maar let op het lidwoord: {expected}{_gender_note(card)}"
    if result.reason == "typo":
        return f"🟠 Kleine typefout. Het is: {expected}"
    return f"❌ Helaas. Het juiste antwoord:\n{_answer_line(card)}"


def summary(done_today: int, goal: int, due_now: int, streak: int) -> str:
    if done_today >= goal:
        text = f"🎯 <b>Dagdoel gehaald!</b> {done_today}/{goal} kaarten vandaag."
        if streak:
            text += f"\n🔥 Streak: {streak} {'dag' if streak == 1 else 'dagen'}"
    else:
        text = f"🏁 <b>Setje klaar.</b> Vandaag {done_today}/{goal} kaarten."
    if due_now:
        text += f"\n📚 Er staan nog {due_now} herhalingen klaar: /practice"
    elif done_today < goal:
        text += "\nVoor nu is er niets meer te oefenen. Tot het volgende setje!"
    return text


def reminder(stats: DayStats, goal: int) -> str:
    return (
        f"⏰ Je zit vandaag op {stats.total}/{goal} kaarten.\n"
        "Nog even oefenen voor je streak? /practice"
    )


def no_pending_card() -> str:
    return "Er staat geen vraag open. Stuur /practice om te oefenen."


def database_unavailable() -> str:
    return "⚠️ De database is even niet bereikbaar. Probeer het zo nog eens."
