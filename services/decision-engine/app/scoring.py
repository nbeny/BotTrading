"""Deterministic opportunity scoring — pure functions, no I/O.

Implements the weighted model from the spec::

    score = volume_growth   * 0.25
          + social_score    * 0.20
          + news_score      * 0.20
          + market_trend    * 0.20
          + liquidity_score * 0.15

Every sub-score is normalized to [0, 1] before weighting, so the final
``opportunity_score`` is a clean 0-100. ``confidence`` reflects how many of the
signals were actually present (missing signals reduce confidence).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

WEIGHTS = {
    "volume_growth": 0.25,
    "social_score": 0.20,
    "news_score": 0.20,
    "market_trend": 0.20,
    "liquidity_score": 0.15,
}


@dataclass(slots=True)
class Features:
    """Raw, un-normalized inputs collected for a symbol."""

    price_change_pct_24h: float | None = None
    volume_spike_ratio: float | None = None
    liquidity_usd: float | None = None
    sentiment_score: float | None = None  # [-1, 1]
    social_growth: float | None = None  # ratio, e.g. 0.5 == +50%
    news_impact: float | None = None  # [0, 1]
    #: Market-wide regime read [-1, 1], from crypto content naming no coin
    #: (regulation, macro, exchange incidents). Used only when the symbol has no
    #: sentiment of its own — see _norm_news.
    market_sentiment: float | None = None
    present: set[str] = field(default_factory=set)


@dataclass(slots=True)
class ScoreResult:
    opportunity_score: int  # 0-100
    confidence: float  # 0-1
    breakdown: dict[str, float]


def _sigmoid(x: float, k: float = 1.0) -> float:
    return 1.0 / (1.0 + math.exp(-k * x))


def _norm_volume(ratio: float | None) -> float:
    if ratio is None:
        return 0.0
    # ratio 1 -> ~0.27, 3 -> ~0.6, 5+ -> ~0.8, saturating.
    return max(0.0, min(1.0, _sigmoid(ratio - 3, k=0.7)))


def _norm_social(growth: float | None) -> float:
    if growth is None:
        return 0.0
    return max(0.0, min(1.0, _sigmoid(growth, k=1.5)))


#: A market-wide read is real but not about this symbol, so it is pulled halfway
#: to neutral before use: it nudges a score, it never decides one.
_MARKET_DAMPING = 0.5


def _norm_news(
    impact: float | None,
    sentiment: float | None,
    market_sentiment: float | None = None,
) -> float:
    if impact is None and sentiment is None and market_sentiment is None:
        return 0.0
    base = impact if impact is not None else 0.0
    # Fold sentiment (-1..1) into a positive news contribution. The symbol's own
    # sentiment always wins; the market regime is a fallback for symbols that
    # have none, which is most of them most of the time.
    if sentiment is not None:
        raw = sentiment
    elif market_sentiment is not None:
        raw = market_sentiment * _MARKET_DAMPING
    else:
        raw = 0.0
    return max(0.0, min(1.0, 0.5 * base + 0.5 * ((raw + 1) / 2)))


def _norm_trend(change_24h: float | None) -> float:
    if change_24h is None:
        return 0.0
    # +20% -> ~0.75, 0% -> 0.5, -20% -> ~0.25.
    return max(0.0, min(1.0, _sigmoid(change_24h / 15.0)))


def _norm_liquidity(liq: float | None) -> float:
    if not liq or liq <= 0:
        return 0.0
    # log-scaled: $10k -> ~0.25, $100k -> ~0.5, $1M -> ~0.75, $10M+ -> ~1.
    return max(0.0, min(1.0, (math.log10(liq) - 3) / 4))


def score(features: Features) -> ScoreResult:
    breakdown = {
        "volume_growth": _norm_volume(features.volume_spike_ratio),
        "social_score": _norm_social(features.social_growth),
        "news_score": _norm_news(
            features.news_impact,
            features.sentiment_score,
            features.market_sentiment,
        ),
        "market_trend": _norm_trend(features.price_change_pct_24h),
        "liquidity_score": _norm_liquidity(features.liquidity_usd),
    }
    weighted = sum(breakdown[k] * WEIGHTS[k] for k in WEIGHTS)
    opportunity = int(round(weighted * 100))

    # Confidence = fraction of weight backed by a present signal.
    present_weight = sum(
        WEIGHTS[k] for k in WEIGHTS if _signal_present(k, features)
    )
    confidence = round(present_weight, 3)
    return ScoreResult(opportunity, confidence, breakdown)


def _signal_present(key: str, f: Features) -> bool:
    # market_sentiment is deliberately absent here. Confidence measures how much
    # symbol-specific evidence backs the score, and a market-wide read is the
    # same number for every symbol -- counting it would lift confidence across
    # the whole book at once, which is precisely what confidence should not do.
    return {
        "volume_growth": f.volume_spike_ratio is not None,
        "social_score": f.social_growth is not None,
        "news_score": f.news_impact is not None or f.sentiment_score is not None,
        "market_trend": f.price_change_pct_24h is not None,
        "liquidity_score": bool(f.liquidity_usd),
    }[key]
