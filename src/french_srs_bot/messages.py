"""All user-facing text (Dutch). Telegram parse mode: HTML."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import time
from html import escape

from .grading import GradeResult
from .models import CardView, DayStats, ThemeProgress
from .session import Standing

BUTTON_UNDERSTOOD = "👍 Begrepen"
BUTTON_OVERRIDE = "✅ Toch goed"

# Short release notes, pushed once to everyone who has not seen them. Numbers only go up:
# add one at the bottom and it goes out on the next start. Nothing is ever sent twice,
# because each user's highest received number is stored.
ANNOUNCEMENTS: tuple[tuple[int, str], ...] = (
    (
        1,
        "🆕 <b>Nieuw in de bot</b>\n\n"
        "<b>/volgorde</b> — welke onderwerpen eraan komen en waar je nu bent. "
        "Nieuwe woorden komen van boven naar beneden: eerst je boek, dan de werkwoorden, "
        "dan de woordenlijsten.\n\n"
        "<b>/help</b> — hoe alles werkt: de setjes, wat de tekens betekenen, en waarom "
        "accenten wel meetellen en hoofdletters niet.\n\n"
        "Typ <b>/</b> in de chat voor het hele menu.",
    ),
    (
        2,
        "🆕 <b>Grammatica erbij</b>\n\n"
        "Bij de zinnen uit je les vul je eerst het ontbrekende verbindingswoord in (tik op een "
        "knop), daarna vertaal je de zin.\n\n"
        "Vind je dat je vertaling toch goed was? Druk op <b>✅ Toch goed</b>: dan telt hij, en "
        "onthoud ik hem voor de volgende keer.\n\n"
        "<b>/grammar</b> — alleen zinnen oefenen. /practice blijft alles door elkaar doen.",
    ),
)


def unseen_announcements(last_seen: int) -> list[tuple[int, str]]:
    return [(number, text) for number, text in ANNOUNCEMENTS if number > last_seen]

_FLAGS = {"fr_nl": "🇫🇷 → 🇳🇱", "nl_fr": "🇳🇱 → 🇫🇷", "translate": "🇫🇷 → 🇳🇱"}
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
        "/grammar — oefen alleen de zinnen\n"
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


def study_order(themes: Sequence[ThemeProgress]) -> str:
    """The themes in the order new words are drawn from them, and how far along you are."""
    if not themes:
        return "Er staan nog geen woorden in de database."
    lines = ["📋 <b>Oefenvolgorde</b>", ""]
    for theme in themes:
        if theme.started == theme.cards:
            mark = "✅"
        elif theme.started:
            mark = "▶️"
        else:
            mark = "⬜"
        lines.append(f"{mark} {escape(theme.name)} — {theme.started}/{theme.cards}")
    lines.append("")
    lines.append(
        "<i>Nieuwe woorden komen van boven naar beneden. Herhalingen lopen daar dwars "
        "doorheen: die gaan altijd voor.</i>"
    )
    return "\n".join(lines)


def intro(card: CardView) -> str:
    return f"🆕 <b>Nieuw woord</b>\n\n{_answer_line(card)}"


def prompt(card: CardView) -> str:
    if card.direction == "gap":
        return (
            "🧩 <b>Vul het ontbrekende verbindingswoord in:</b>\n\n"
            f"<b>{escape(card.question)}</b>"
        )
    if card.direction == "translate":
        return f"{_FLAGS[card.direction]}  <i>Vertaal:</i>\n\n<b>{escape(card.question)}</b>"
    text = f"{_FLAGS[card.direction]}\n\n<b>{escape(card.question)}</b>"
    if card.direction == "nl_fr" and card.hint:
        text += f"\n<i>({escape(card.hint)})</i>"
    return text


def _rule_lines(card: CardView) -> str:
    lines = []
    if card.rule:
        lines.append(f"<i>{escape(card.rule)}</i>")
    if card.hint:
        lines.append(f"💡 <i>{escape(card.hint)}</i>")
    return ("\n" + "\n".join(lines)) if lines else ""


def gap_feedback(correct: bool, card: CardView) -> str:
    """The verdict on a tapped choice, always with the completed sentence and the rule."""
    sentence = escape(card.sentence).replace("___", f"<b>{escape(card.gap_answer)}</b>")
    head = "✅ <b>Juist!</b>" if correct else f"❌ Het is <b>{escape(card.gap_answer)}</b>."
    return f"{head}\n\n{sentence}{_rule_lines(card)}"


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
    if card.direction == "translate":
        return f"❌ Modelvertaling:\n<b>{escape(card.dutch[0])}</b>"
    return f"❌ Helaas. Het juiste antwoord:\n{_answer_line(card)}"


def override_applied() -> str:
    return "✅ Genoteerd, dit antwoord telt vanaf nu als goed."


def override_refused() -> str:
    return "Dit antwoord is al verwerkt"


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
