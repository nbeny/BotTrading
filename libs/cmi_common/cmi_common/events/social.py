"""Social events emitted by the Reddit (and optional Twitter/X) collectors."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import BaseEvent, EventType


class SocialEvent(BaseEvent):
    """Published on ``market.social.events``.

    Aggregates community activity for a symbol over a sampling window rather
    than a single post, so downstream services reason about *trends*.
    """

    event_type: Literal[EventType.SOCIAL] = EventType.SOCIAL
    symbol: str
    platform: str = Field(default="reddit")
    subreddit: str | None = None
    window_minutes: int = Field(default=60, ge=1)
    mentions: int = Field(..., ge=0)
    # Growth of mentions vs the previous window (e.g. 0.5 == +50%).
    mentions_growth: float = 0.0
    unique_authors: int = Field(default=0, ge=0)
    engagement_score: float = Field(
        default=0.0, ge=0, description="upvotes+comments weighted"
    )
    top_posts: list[str] = Field(
        default_factory=list, description="titles / permalinks sampled"
    )
    # Raw text blob handed to the sentiment service for scoring.
    text_sample: str = ""

    def partition_key(self) -> str:
        return self.symbol
