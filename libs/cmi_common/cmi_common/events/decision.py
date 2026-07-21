"""Decision events produced by the Sonnet analyst / decision engine."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from .base import BaseEvent, EventType, Source


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    WATCH = "watch"


class DecisionEvent(BaseEvent):
    """Published on ``decision.events`` and consumed by the risk-engine.

    A decision is a *candidate* trade idea: direction, conviction and the
    supporting narrative. It is NOT yet risk-approved.
    """

    event_type: Literal[EventType.DECISION] = EventType.DECISION
    source: Source = Source.AI_SONNET
    symbol: str
    direction: Direction = Direction.LONG
    # Deterministic decision-engine score (0..100) blended with AI conviction.
    opportunity_score: int = Field(..., ge=0, le=100)
    confidence: float = Field(..., ge=0, le=1)
    suggested_entry_price: float | None = Field(default=None, gt=0)
    rationale: str = Field(..., description="Human-readable decision explanation")
    key_risks: list[str] = Field(default_factory=list)
    # Whether a senior (Sonnet) analyst validated this, vs pure deterministic.
    ai_validated: bool = False
    correlated_event_ids: list[str] = Field(default_factory=list)

    def partition_key(self) -> str:
        return self.symbol
