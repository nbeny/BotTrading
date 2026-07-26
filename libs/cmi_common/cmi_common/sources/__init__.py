"""Free-tier ingestion: providers, adaptive rate-limited poll loops, persistence."""

from __future__ import annotations

from .cascade import CircuitBreaker, RateLimitedError
from .lexicon import LEXICON_KEY, SEED_LEXICON, LexiconLoader, SymbolLexicon
from .loop import AdaptivePollLoop
from .normalize import (
    MARKET_SYMBOL,
    ContentNormalizer,
    LexiconNormalizer,
    NormalizeResult,
)
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
from .runtime import (
    KNOWN_PLATFORMS,
    default_runtime,
    get_runtime,
    is_enabled,
    set_runtime,
)

__all__ = [
    "KNOWN_PLATFORMS",
    "LEXICON_KEY",
    "MARKET_SYMBOL",
    "SEED_LEXICON",
    "WINDOWS",
    "AdaptivePollLoop",
    "BucketRow",
    "CircuitBreaker",
    "ContentNormalizer",
    "ContentRepository",
    "FakeContentRepository",
    "LexiconLoader",
    "LexiconNormalizer",
    "NormalizeResult",
    "Provider",
    "RateLimitedError",
    "RawItem",
    "SqlContentRepository",
    "SqlSentimentAggReader",
    "SymbolLexicon",
    "aggregate_buckets",
    "default_runtime",
    "get_runtime",
    "is_enabled",
    "parse_retry_after",
    "raw_item_to_row",
    "set_runtime",
    "window_delta",
]
