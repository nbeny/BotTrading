"""Analysis events produced by the Claude Haiku worker and decision engine."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import BaseEvent, EventType, Source


class SignalRef(BaseEvent):
    """Not a Kafka event by itself; a lightweight reference embedded in
    :class:`AnalysisEvent` to record which raw events fed the analysis."""

    model_config = BaseEvent.model_config
    event_type: Literal[EventType.ANALYSIS] = EventType.ANALYSIS
    source: Source = Source.AI_HAIKU


class AnalysisEvent(BaseEvent):
    """Published on ``market.analysis.events`` by ai-worker-haiku.

    Carries a cheap, fast opportunity score plus the correlated evidence so
    the senior Sonnet analyst (and the decision engine) can act on it.
    """

    event_type: Literal[EventType.ANALYSIS] = EventType.ANALYSIS
    source: Source = Source.AI_HAIKU
    symbol: str
    # 0..100 opportunity score from the fast triage model.
    opportunity_score: int = Field(..., ge=0, le=100)
    confidence: float = Field(..., ge=0, le=1)
    reason: str = Field(..., description="Short natural-language rationale")
    summary: str = ""
    # Structured evidence that produced the score.
    price_change_pct_24h: float | None = None
    volume_spike_ratio: float | None = None
    sentiment_score: float | None = Field(default=None, ge=-1, le=1)
    news_impact: float | None = Field(default=None, ge=0, le=1)
    social_growth: float | None = None
    # Ids of the source events that were correlated into this analysis.
    correlated_event_ids: list[str] = Field(default_factory=list)
    # True when the score crosses the escalation threshold for Sonnet.
    escalate: bool = False

    def partition_key(self) -> str:
        return self.symbol
