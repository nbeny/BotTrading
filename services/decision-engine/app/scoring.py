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
    # No early return for "nothing known". Absence of news is neutral, not
    # bearish: short-circuiting to 0.0 gave a silent symbol the same news score
    # as one everybody is panicking about, since sentiment -1.0 also lands on
    # 0.0. Falling through with base=0 and raw=0 puts "unknown" exactly where a
    # genuinely neutral reading sits, and leaves bearish strictly below it.
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


#: Funding rate at which the crowding read saturates, measured rather than
#: guessed: across all 854 Binance perps on 2026-07-31 the 5th percentile sits
#: at -0.000156 and the 95th at +0.000159, with a median of +0.000050. An
#: earlier draft used 0.0004 and spanned only 0.19 between those percentiles --
#: an axis that varies by a fifth of its range over 90% of the book is not
#: discriminating, it is decoration. This scale spans 0.66.
#:
#: Note the median lands at 0.378, below neutral: positive funding is the
#: normal state of crypto perps, so the typical symbol reads mildly crowded.
#: That is a property of the market, not a bias to calibrate away.
_FUNDING_SCALE = 0.0001
#: An unlock of this share of supply is treated as maximally severe.
_UNLOCK_FULL_SEVERITY_PCT = 5.0
#: Unlocks further out than this do not bear on a position opened today.
_UNLOCK_HORIZON_DAYS = 30.0


def _mean_present(terms: list[float | None]) -> float | None:
    """Average of the terms that exist, or None when none do.

    Averaging over present terms rather than over all of them is what lets an
    axis be partially observed: funding alone is a real reading, not a reading
    dragged toward zero by the two calls we did not make.
    """
    values = [t for t in terms if t is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _norm_positioning(
    *, funding: float | None, ratio: float | None, oi_change: float | None
) -> float | None:
    """How favourably the derivatives crowd is positioned, in [0, 1].

    Contrarian on crowding, confirmatory on engagement. Positive funding means
    longs are paying shorts -- the crowded side -- so it *lowers* the score; a
    long/short account ratio above 1 does the same. Rising open interest raises
    it: conviction is entering the book.
    """
    return _mean_present(
        [
            None if funding is None else _sigmoid(-funding / _FUNDING_SCALE),
            # A zero or negative ratio cannot happen and log(0) would raise. The
            # event schema rejects it at construction (gt=0); this is the second
            # gate, for features arriving from anywhere else.
            None if not ratio or ratio <= 0 else _sigmoid(-math.log(ratio), k=1.5),
            None if oi_change is None else _sigmoid(oi_change / 20.0),
        ]
    )


def _norm_fundamentals(
    *,
    tvl_change: float | None,
    fees_change: float | None,
    unlock_pct: float | None,
    unlock_days: float | None,
    has_schedule: bool,
) -> float | None:
    """Protocol health net of scheduled dilution, in [0, 1].

    The unlock term exists only when a schedule is actually known. A token
    DefiLlama does not track contributes nothing here: silence is not a clean
    bill of health, and treating it as 1.0 would reward being unmeasured -- in
    a model that already excludes an absent axis rather than penalising it.
    """
    unlock_term: float | None = None
    if has_schedule:
        if unlock_pct is None or unlock_days is None:
            # Schedule read, nothing pending: a measurement, and a good one.
            unlock_term = 1.0
        else:
            severity = max(0.0, min(1.0, unlock_pct / _UNLOCK_FULL_SEVERITY_PCT))
            proximity = max(0.0, min(1.0, 1.0 - unlock_days / _UNLOCK_HORIZON_DAYS))
            unlock_term = 1.0 - severity * proximity
    return _mean_present(
        [
            None if tvl_change is None else _sigmoid(tvl_change / 15.0),
            None if fees_change is None else _sigmoid(fees_change / 25.0),
            unlock_term,
        ]
    )


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
    present_weight = sum(WEIGHTS[k] for k in WEIGHTS if _signal_present(k, features))
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
