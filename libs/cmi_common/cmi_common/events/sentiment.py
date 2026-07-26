"""Sentiment events produced by the hybrid sentiment service (HF level 1)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import BaseEvent, EventType, Source


class SentimentEvent(BaseEvent):
    """Published on ``market.sentiment.events``.

    ``sentiment_score`` is normalized to [-1, 1] (bearish..bullish) while
    ``confidence`` reports the model's certainty in [0, 1].
    """

    event_type: Literal[EventType.SENTIMENT] = EventType.SENTIMENT
    source: Source = Source.SENTIMENT_SERVICE
    symbol: str
    sentiment_score: float = Field(..., ge=-1, le=1)
    confidence: float = Field(..., ge=0, le=1)
    model_name: str = Field(..., description="e.g. 'ElKulako/cryptobert'")
    # What was scored: 'news' | 'social' | 'aggregate'.
    input_kind: str = "aggregate"
    sample_size: int = Field(default=1, ge=1)
    #: Mentions this hour against the symbol's own 24h mean, as a ratio: 0.0 is
    #: "as talked about as usual", 0.5 is +50%. Carried here because this service
    #: owns the mention counts and ai-worker-haiku, a pure Kafka consumer, has no
    #: database to derive it from. ``None`` means no baseline yet -- which is not
    #: the same claim as "flat", and the scorer treats the two differently.
    social_growth: float | None = None

    def partition_key(self) -> str:
        return self.symbol
