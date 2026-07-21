"""News events emitted by the CryptoCompare collector."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, HttpUrl

from .base import BaseEvent, EventType


class NewsEvent(BaseEvent):
    """Published on ``market.news.events``."""

    event_type: Literal[EventType.NEWS] = EventType.NEWS
    article_id: str
    title: str
    body: str = ""
    url: HttpUrl
    published_at: int = Field(..., description="epoch seconds")
    source_name: str = Field(..., description="e.g. 'CoinDesk'")
    # Symbols the article is tagged with / mentions. Drives fan-out downstream.
    symbols: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    # Provider-supplied sentiment/impact if available (0..1); refined later.
    provider_sentiment: float | None = Field(default=None, ge=0, le=1)

    def partition_key(self) -> str:
        # Key by primary symbol so a token's news lands on one partition.
        return self.symbols[0] if self.symbols else self.article_id
