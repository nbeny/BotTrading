"""Free-tier ingestion: providers, adaptive rate-limited poll loops, persistence."""

from __future__ import annotations

from .cascade import CircuitBreaker, RateLimitedError
from .loop import AdaptivePollLoop
from .provider import Provider, parse_retry_after
from .raw import RawItem
from .reader import (
    WINDOWS,
    BucketRow,
    SqlSentimentAggReader,
    aggregate_buckets,
    window_delta,
)
from .repository import (
    ContentRepository,
    FakeContentRepository,
    SqlContentRepository,
    raw_item_to_row,
)

__all__ = [
    "WINDOWS",
    "AdaptivePollLoop",
    "BucketRow",
    "CircuitBreaker",
    "ContentRepository",
    "FakeContentRepository",
    "Provider",
    "RateLimitedError",
    "RawItem",
    "SqlContentRepository",
    "SqlSentimentAggReader",
    "aggregate_buckets",
    "parse_retry_after",
    "raw_item_to_row",
    "window_delta",
]
