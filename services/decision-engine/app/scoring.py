"""Deterministic opportunity scoring — pure functions, no I/O.

Implements the weighted model from the spec::

    score = 100 * SUM(sub_i * w_i over PRESENT axes) / SUM(w_i over PRESENT axes)

over seven axes: volume_growth 0.1875, social_score 0.15, news_score 0.15,
market_trend 0.15, liquidity_score 0.1125, positioning 0.15, fundamentals 0.10.

Every sub-score is normalized to [0, 1] before weighting, so the final
``opportunity_score`` is a clean 0-100. ``confidence`` is the share of model
weight backed by real evidence.

**The denominator is the point.** Dividing by a constant 1.0 meant an axis whose
data we failed to collect scored identically to one measured at its worst — so a
symbol missing three of five signals was not "scored on what we know", it was
penalised for what we failed to gather. That is the documented
RISK_MIN_SCORE=70 / max-observed-score-68 deadlock: the scale itself was
unreachable. Every ``_norm_*`` now returns ``None`` for absent input, and the
sum divides by the weight actually present.

The corollary matters when reading anything downstream: an absent axis is
*excluded*, never penalised, so an unmeasured value that leaks in as a confident
reading always moves the score in the direction of the reading. Keeping ``None``
and a measured ``0`` distinct is therefore load-bearing at every layer that
feeds this module, not a stylistic preference.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: The five legacy axes are rescaled by x0.75, preserving their relative
#: proportions exactly — this expresses no new opinion about the old model, it
#: only makes room for the two new ones.
WEIGHTS = {
    "volume_growth": 0.1875,
    "social_score": 0.1500,
    "news_score": 0.1500,
    "market_trend": 0.1500,
    "liquidity_score": 0.1125,
    "positioning": 0.1500,
    "fundamentals": 0.1000,
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
    #: Binance perp positioning. funding is the raw 8h fraction, not a percent.
    funding_rate_8h: float | None = None
    long_short_account_ratio: float | None = None
    open_interest_change_pct_24h: float | None = None
    #: DefiLlama fundamentals. has_unlock_schedule False means "not tracked",
    #: which is a different statement from "tracked, nothing pending" — see
    #: _norm_fundamentals.
    tvl_change_pct_7d: float | None = None
    fees_change_pct_7d: float | None = None
    next_unlock_pct_supply: float | None = None
    next_unlock_days: float | None = None
    has_unlock_schedule: bool = False
    present: set[str] = field(default_factory=set)


@dataclass(slots=True)
class ScoreResult:
    opportunity_score: int  # 0-100
    confidence: float  # 0-1
    breakdown: dict[str, float]


def _sigmoid(x: float, k: float = 1.0) -> float:
    return 1.0 / (1.0 + math.exp(-k * x))


def _norm_volume(ratio: float | None) -> float | None:
    if ratio is None:
        return None
    # ratio 1 -> ~0.27, 3 -> ~0.6, 5+ -> ~0.8, saturating.
    return max(0.0, min(1.0, _sigmoid(ratio - 3, k=0.7)))


def _norm_social(growth: float | None) -> float | None:
    if growth is None:
        return None
    return max(0.0, min(1.0, _sigmoid(growth, k=1.5)))


#: A market-wide read is real but not about this symbol, so it is pulled halfway
#: to neutral before use: it nudges a score, it never decides one.
_MARKET_DAMPING = 0.5


def _norm_news(
    impact: float | None,
    sentiment: float | None,
    market_sentiment: float | None = None,
) -> float | None:
    # Absence of news is neutral, not bearish — the original fix, which
    # short-circuited to 0.0 and so gave a silent symbol the same news score as
    # one everybody is panicking about. Under renormalisation, *excluding* the
    # axis expresses that better than the 0.5 this used to return: a neutral
    # constant still drags a strong symbol toward the middle, where an absent
    # axis leaves the score to the evidence that exists.
    if impact is None and sentiment is None and market_sentiment is None:
        return None
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


def _norm_trend(change_24h: float | None) -> float | None:
    if change_24h is None:
        return None
    # +20% -> ~0.75, 0% -> 0.5, -20% -> ~0.25.
    return max(0.0, min(1.0, _sigmoid(change_24h / 15.0)))


def _norm_liquidity(liq: float | None) -> float | None:
    # A measured zero is the worst score; an unknown is no score at all. The two
    # used to collapse onto 0.0, which is what made "we did not collect it"
    # indistinguishable from "there is none".
    if liq is None:
        return None
    if liq <= 0:
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
#: Below this share of model weight, no score is emitted at all. Set just above
#: the heaviest single axis (volume_growth, 0.1875) so one axis can never speak
#: for seven, and just below the lightest legitimate pair (fundamentals +
#: liquidity, 0.2125). Renormalisation exists so absent data stops dragging a
#: score down; without this floor it also lets a sliver of data manufacture
#: certainty.
_MIN_PRESENT_WEIGHT = 0.20
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
        if unlock_pct is None and unlock_days is None:
            # Schedule read, nothing pending: a measurement, and a good one.
            unlock_term = 1.0
        elif unlock_pct is None or unlock_days is None:
            # Half a reading is not a reading. An unlock we can size but not
            # date, or date but not size, must leave the term absent — the
            # earlier version landed both on 1.0, so a known 40% unlock whose
            # date failed to parse scored the axis at its *maximum* and pulled
            # the whole score up. That is the same conflation as a fabricated
            # zero, with the sign flipped: fabricated safety.
            unlock_term = None
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
    sub: dict[str, float | None] = {
        "volume_growth": _norm_volume(features.volume_spike_ratio),
        "social_score": _norm_social(features.social_growth),
        "news_score": _norm_news(
            features.news_impact,
            features.sentiment_score,
            features.market_sentiment,
        ),
        "market_trend": _norm_trend(features.price_change_pct_24h),
        "liquidity_score": _norm_liquidity(features.liquidity_usd),
        "positioning": _norm_positioning(
            funding=features.funding_rate_8h,
            ratio=features.long_short_account_ratio,
            oi_change=features.open_interest_change_pct_24h,
        ),
        "fundamentals": _norm_fundamentals(
            tvl_change=features.tvl_change_pct_7d,
            fees_change=features.fees_change_pct_7d,
            unlock_pct=features.next_unlock_pct_supply,
            unlock_days=features.next_unlock_days,
            has_schedule=features.has_unlock_schedule,
        ),
    }
    # Presence is read off the normalised values rather than re-derived from the
    # raw fields, so the two can no longer disagree -- the old _signal_present
    # was a second, hand-maintained copy of the same rule.
    breakdown = {k: v for k, v in sub.items() if v is not None}
    present_weight = sum(WEIGHTS[k] for k in breakdown)
    if present_weight < _MIN_PRESENT_WEIGHT:
        # Too little evidence to renormalise honestly. Dividing by a sliver of
        # weight manufactures certainty: before this gate, a lone
        # has_unlock_schedule=True — "DefiLlama tracks this token and nothing
        # is due" — scored a perfect 100 on 0.10 of the model, and funding
        # alone scored 99. Renormalisation is meant to stop absent data
        # dragging a score down, not to let one axis speak for seven.
        return ScoreResult(0, 0.0, {})
    weighted = sum(breakdown[k] * WEIGHTS[k] for k in breakdown)
    opportunity = round(100 * weighted / present_weight)
    return ScoreResult(opportunity, _confidence(breakdown, features), breakdown)


def _confidence(breakdown: dict[str, float], f: Features) -> float:
    """Share of model weight backed by *symbol-specific* evidence.

    Not simply the present weight, and the difference is one axis: the news axis
    fires on the market-wide regime read when a symbol has no sentiment of its
    own. That read genuinely belongs in the score — damped, as a nudge — but it
    is the same number for every symbol, so counting it here would lift
    confidence across the whole book at once, which is precisely what confidence
    must not do.
    """
    keys = set(breakdown)
    if f.news_impact is None and f.sentiment_score is None:
        keys.discard("news_score")
    return round(sum(WEIGHTS[k] for k in keys), 3)
