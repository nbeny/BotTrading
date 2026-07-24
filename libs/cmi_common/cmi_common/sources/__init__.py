"""Free-tier ingestion: providers, adaptive rate-limited poll loops, persistence."""

from __future__ import annotations

from .cascade import CircuitBreaker, RateLimitedError
from .loop import AdaptivePollLoop
from .provider import Provider, parse_retry_after
from .raw import RawItem
from .repository import (
    ContentRepository,
    FakeContentRepository,
    SqlContentRepository,
    raw_item_to_row,
)

__all__ = [
    "AdaptivePollLoop",
    "CircuitBreaker",
    "ContentRepository",
    "FakeContentRepository",
    "Provider",
    "RateLimitedError",
    "RawItem",
    "SqlContentRepository",
    "parse_retry_after",
    "raw_item_to_row",
]
