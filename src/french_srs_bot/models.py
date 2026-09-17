"""Plain data objects shared between the database, session logic and messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Direction = Literal["fr_nl", "nl_fr"]


@dataclass(frozen=True)
class SrsState:
    fsrs_state: int
    step: int | None
    stability: float | None
    difficulty: float | None
    due: datetime
    last_review: datetime | None


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

    @property
    def question(self) -> str:
        return self.french[0] if self.direction == "fr_nl" else self.dutch[0]

    @property
    def accepted(self) -> list[str]:
        return self.dutch if self.direction == "fr_nl" else self.french

    @property
    def answer_lang(self) -> Literal["fr", "nl"]:
        return "nl" if self.direction == "fr_nl" else "fr"


@dataclass(frozen=True)
class DayStats:
    total: int  # review rows today
    new: int  # cards introduced today
    reviews: int  # review rows today on cards introduced before today


@dataclass(frozen=True)
class BotState:
    pending_card_id: int | None
    batch_remaining: int
