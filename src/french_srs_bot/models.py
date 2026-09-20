"""Plain data objects shared between the database, session logic and messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Direction = Literal["fr_nl", "nl_fr", "gap", "translate"]
Kind = Literal["vocab", "grammar"]

# Per item kind: the card that is introduced first, and the one that follows a day later.
PRIMARY: dict[str, str] = {"vocab": "fr_nl", "grammar": "gap"}
SECONDARY: dict[str, str] = {"vocab": "nl_fr", "grammar": "translate"}


@dataclass(frozen=True)
class SrsState:
    fsrs_state: int
    step: int | None
    stability: float | None
    difficulty: float | None
    due: datetime
    last_review: datetime | None


@dataclass(frozen=True)
class User:
    id: int
    telegram_user_id: int
    name: str
    daily_goal: int | None = None  # None: follow the shared value from settings.yaml
    daily_new: int | None = None
    batch_size: int | None = None
    last_announcement: int = 0  # highest release note this user has received


@dataclass(frozen=True)
class CardView:
    card_id: int
    item_id: int
    direction: Direction
    french: list[str]
    dutch: list[str]
    gender: str | None
    hint: str | None
    introduced_at: datetime | None
    srs: SrsState | None  # None until the card has been reviewed once
    voice_file_id: str | None = None  # Telegram's handle for the French audio of this item
    voice_key: str | None = None  # voice+tempo that audio was made with
    kind: Kind = "vocab"
    sentence: str | None = None  # grammar: the sentence with the ___ gap
    gap_answer: str | None = None  # grammar: what belongs in the gap
    rule: str | None = None  # grammar: one line of explanation, shown with the feedback
    choices: list[str] = field(default_factory=list)  # grammar: the buttons of its theme

    @property
    def question(self) -> str:
        if self.direction == "gap":
            return self.sentence
        if self.direction in ("fr_nl", "translate"):
            return self.french[0]
        return self.dutch[0]

    @property
    def accepted(self) -> list[str]:
        if self.direction == "gap":
            return [self.gap_answer]
        return self.dutch if self.direction in ("fr_nl", "translate") else self.french

    @property
    def answer_lang(self) -> Literal["fr", "nl"]:
        return "fr" if self.direction in ("nl_fr", "gap") else "nl"

    @property
    def is_primary(self) -> bool:
        """The first card of its item: shown before the other one, on an earlier day."""
        return self.direction == PRIMARY[self.kind]


@dataclass(frozen=True)
class ThemeProgress:
    """How far one user is into one theme, for the study-order overview."""

    position: int
    name: str
    cards: int
    started: int  # cards that have been introduced; the rest is still ahead


@dataclass(frozen=True)
class DayStats:
    total: int  # review rows today
    new: int  # cards introduced today
    reviews: int  # review rows today on cards introduced before today


@dataclass(frozen=True)
class BotState:
    pending_card_id: int | None
    batch_remaining: int
