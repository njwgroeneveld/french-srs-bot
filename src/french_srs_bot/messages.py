"""All user-facing text (Dutch). Telegram parse mode: HTML."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import time
from html import escape

from .grading import GradeResult
from .models import CardView, DayStats
from .session import Standing

BUTTON_UNDERSTOOD = "👍 Begrepen"

_FLAGS = {"fr_nl": "🇫🇷 → 🇳🇱", "nl_fr": "🇳🇱 → 🇫🇷"}
_GENDER = {"m": "mannelijk", "f": "vrouwelijk"}


def _gender_note(card: CardView) -> str:
    return f" <i>({_GENDER[card.gender]})</i>" if card.gender in _GENDER else ""


def _answer_line(card: CardView) -> str:
    return f"<b>{escape(card.french[0])}</b>{_gender_note(card)} = <b>{escape(card.dutch[0])}</b>"


def _join_times(moments: Sequence[time]) -> str:
    labels = [f"{moment:%H:%M}" for moment in moments]
    return labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} en {labels[-1]}"


def welcome(batch_times: Sequence[time]) -> str:
    return (
        "👋 <b>Bonjour !</b>\n\n"
        f"Ik overhoor je elke dag Franse woordjes, in setjes om {_join_times(batch_times)}.\n"
        "Typ het antwoord gewoon als bericht. Wil je tussendoor oefenen? Stuur /practice.\n"
        "Benieuwd hoe jullie ervoor staan? Stuur /stand.\n"
        "Weet je even niet meer hoe iets zit? /help legt alles uit."
    )


def help_text(batch_times: Sequence[time], goal: int) -> str:
    return (
        "ℹ️ <b>Hoe werkt het?</b>\n\n"
        f"Ik stuur je elke dag setjes om {_join_times(batch_times)}, tot je {goal} kaarten hebt "
        "gedaan. Typ je antwoord gewoon als bericht terug.\n\n"
        "<b>Commando's</b>\n"
        "/practice — oefen nu een setje\n"
        "/stand — hoe staan jullie er deze week voor\n"
        "/help — dit bericht\n\n"
        "<b>Wat de tekens betekenen</b>\n"
        "✅ goed\n"
        "🟡 alleen een accent verkeerd\n"
        "🟠 lidwoord of typefout\n"
        "❌ fout\n\n"
        "Hoofdletters maken niet uit, accenten wel: <i>a pied</i> is niet hetzelfde als "
        "<i>à pied</i>.\n\n"
        "Bij 🇫🇷 → 🇳🇱 hoor je het Franse woord meteen. Bij 🇳🇱 → 🇫🇷 moet je het zelf bedenken, "
        "dus dat krijg je pas te horen nadat je geantwoord hebt."
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


def summary(done_today: int, goal: int, due_now: int, streak: int, *, more_available: bool) -> str:
    if done_today >= goal:
        text = f"🎯 <b>Dagdoel gehaald!</b> {done_today}/{goal} kaarten vandaag."
        if streak:
            text += f"\n🔥 Streak: {streak} {'dag' if streak == 1 else 'dagen'}"
    else:
        text = f"🏁 <b>Setje klaar.</b> Vandaag {done_today}/{goal} kaarten."
    if due_now:
        text += f"\n📚 Er staan nog {due_now} herhalingen klaar: /practice"
    elif done_today < goal and more_available:
        text += "\nZin in meer? /practice"
    elif done_today < goal:
        text += "\nVoor nu is er niets meer te oefenen. Tot het volgende setje!"
    return text


def reminder(stats: DayStats, goal: int) -> str:
    return (
        f"⏰ Je zit vandaag op {stats.total}/{goal} kaarten.\n"
        "Nog even oefenen voor je streak? /practice"
    )


def stale_intro() -> str:
    return "Dit woord is nu niet aan de beurt"


def no_pending_card() -> str:
    return "Er staat geen vraag open. Stuur /practice om te oefenen."


def database_unavailable() -> str:
    return "⚠️ De database is even niet bereikbaar. Probeer het zo nog eens."


def peer_reached_goal(name: str, *, done: int, goal: int) -> str:
    if done >= goal:
        return f"🎯 <b>{escape(name)}</b> heeft het dagdoel ook gehaald. Jullie zijn er allebei door!"
    return f"🎯 <b>{escape(name)}</b> heeft het dagdoel gehaald. Jij zit op {done}/{goal}."


def standings(rows: Sequence[Standing]) -> str:
    lines = ["📊 <b>Deze week</b>", ""]
    for row in rows:
        days = "dag" if row.days_reached == 1 else "dagen"
        lines.append(
            f"<b>{escape(row.name)}</b> — {row.cards} kaarten, "
            f"{row.days_reached} {days} gehaald <i>(doel {row.goal})</i>"
        )
    return "\n".join(lines)
