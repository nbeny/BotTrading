"""Free-tier ingestion: providers, adaptive rate-limited poll loops, persistence."""

from __future__ import annotations

from .candles import (
    INTERVALS,
    Candle,
    Depth,
    SqlCandleReader,
    interval_delta,
    is_closed,
)
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
    RUNTIME_KEY,
    TELEGRAM_SEED_CHANNELS,
    dedupe_channels,
    default_runtime,
    get_runtime,
    is_enabled,
    normalize_channel,
    parse_channels,
    set_runtime,
    source_status_key,
)

__all__ = [
    "INTERVALS",
    "KNOWN_PLATFORMS",
    "LEXICON_KEY",
    "MARKET_SYMBOL",
    "RUNTIME_KEY",
    "SEED_LEXICON",
    "TELEGRAM_SEED_CHANNELS",
    "WINDOWS",
    "AdaptivePollLoop",
    "BucketRow",
    "Candle",
    "CircuitBreaker",
    "ContentNormalizer",
    "ContentRepository",
    "Depth",
    "FakeContentRepository",
    "LexiconLoader",
    "LexiconNormalizer",
    "NormalizeResult",
    "Provider",
    "RateLimitedError",
    "RawItem",
    "SqlCandleReader",
    "SqlContentRepository",
    "SqlSentimentAggReader",
    "SymbolLexicon",
    "aggregate_buckets",
    "dedupe_channels",
    "default_runtime",
    "get_runtime",
    "interval_delta",
    "is_closed",
    "is_enabled",
    "normalize_channel",
    "parse_channels",
    "parse_retry_after",
    "raw_item_to_row",
    "set_runtime",
    "source_status_key",
    "window_delta",
]
