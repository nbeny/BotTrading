"""Pure raw -> Features mapping, shared by the live consumer and offline replay.

engine.py builds this same mapping inline, entangled with the aiokafka consumer
loop, which makes it unusable from a calibration script without dragging in a
Kafka client. This module extracts exactly that mapping so a replay script can
import it directly and execute the same transformation production runs -- a
second, hand-written copy would calibrate a model nobody actually runs.

It reads only the features dict (``event.meta["features"]``), never the
top-level AnalysisEvent fields. ai-worker-haiku fills those four top-level
fields (price_change_pct_24h, volume_spike_ratio, sentiment_score,
social_growth) from this same dict at the one site that builds the event --
verified and locked by test_haiku_fills_the_event_fields_from_the_same_dict in
tests/test_features_from_replay.py -- so the two are identical by
construction. Reading only the dict makes the module usable on a replayed
decision_journal row, which carries the dict but not a re-synthesised event.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from .scoring import Features

logger = logging.getLogger(__name__)


def _liquidity(raw: dict) -> float | None:
    """DEX liquidity when there is a reading, 24h volume as the stand-in when not.

    Reading ``liquidity_usd`` alone left ``liquidity_score`` at zero for
    essentially the entire flow: ai-worker-haiku only writes that key for
    DexEvents, and CEX-listed pairs never produce one. Measured over the 12,183
    highest-scoring production signals, it was populated in exactly none of
    them -- 15% of the model weight permanently dead, capping the achievable
    score at 61 against a decision threshold of 70.

    The substitution is not invented here: haiku's own scorer has used 24h
    volume as the liquidity stand-in since Plan-1, normalising it identically,
    and records which of the two it used in ``liquidity_source`` so calibration
    can still tell an estimate from a measurement.
    """
    liq = raw.get("liquidity_usd")
    if liq:
        return float(liq)
    proxy = raw.get("volume_24h_usd")
    return float(proxy) if proxy else None


def _unlock_days(raw: dict, *, now: datetime) -> float | None:
    """Days until the next unlock, from the absolute date the store carries.

    Stored absolute and converted at read time: a stored "days remaining" would
    silently age between the collector's poll and the decision that reads it.

    Takes an explicit reference instant instead of calling datetime.now(): this
    function runs twice against the same row -- once live, once in replay -- and
    a wall clock would make the term vanish on the second pass. Replayed a week
    later, every stored date would be in the past, days_since_push would go
    None, and the recomputed score would silently diverge from the one
    production emitted. Live callers pass now(tz=UTC); replay passes the row's
    own timestamp.
    """
    value = raw.get("next_unlock_at")
    if not value:
        return None
    try:
        at = datetime.fromisoformat(str(value))
    except ValueError:
        # One unparseable field must not kill the consumer loop. The schedule
        # flag still stands, so the axis degrades to "nothing pending" rather
        # than to a fabricated urgency.
        logger.warning("unparseable next_unlock_at: %r", value)
        return None
    if at.tzinfo is None:
        # Nothing writes a naive timestamp today, but subtracting one from an
        # aware now() raises TypeError, which would take the consumer down.
        at = at.replace(tzinfo=UTC)
    days = (at - now).total_seconds() / 86400.0
    if days < 0:
        # A past date is a *stale* reading, not an imminent unlock, and the
        # difference is the whole axis. When an unlock passes, the collector
        # republishes next_unlock_at=None — "read, nothing pending" — but the
        # feature store drops None on merge, so the superseded date and pct
        # survive beside it. Clamping that to zero days made proximity 1.0 and
        # reported the axis at its *worst* where the truth is its best, and the
        # 900s TTL never rescued it because every cycle rewrites the hash with
        # a fresh expiry. Returning None lets the pct-XOR-days guard drop the
        # term instead: half a reading is no reading.
        logger.warning("next_unlock_at %s is in the past; treating as stale", value)
        return None
    return days


def features_from(raw: dict, *, now: datetime) -> Features:
    """Build Features from a stored features dict alone.

    ``now`` is the reference instant for ``_unlock_days``: datetime.now(tz=UTC)
    when called live, the replayed row's own timestamp when called offline --
    see _unlock_days for why the difference matters.

    Note the one deliberate divergence from engine.py's live construction:
    there, market_sentiment comes from DecisionEngine._market_sentiment(), a
    TTL-gated in-memory read of the most recent market-wide SentimentEvent, not
    from the features dict. This module reads it from ``raw`` instead, per the
    replay contract -- it has no consumer state to hold that value, and a
    replayed row has no live TTL clock to test it against. Whatever value the
    row should carry for that instant is the caller's responsibility to place
    in ``raw["market_sentiment"]`` before calling this function.
    """
    return Features(
        price_change_pct_24h=raw.get("price_change_pct_24h"),
        volume_spike_ratio=raw.get("volume_spike_ratio"),
        liquidity_usd=_liquidity(raw),
        sentiment_score=raw.get("sentiment_score"),
        social_growth=raw.get("social_growth"),
        news_impact=1.0 if raw.get("has_news") else None,
        market_sentiment=raw.get("market_sentiment"),
        funding_rate_8h=raw.get("funding_rate_8h"),
        long_short_account_ratio=raw.get("long_short_account_ratio"),
        open_interest_change_pct_24h=raw.get("open_interest_change_pct_24h"),
        tvl_change_pct_7d=raw.get("tvl_change_pct_7d"),
        fees_change_pct_7d=raw.get("fees_change_pct_7d"),
        next_unlock_pct_supply=raw.get("next_unlock_pct_supply"),
        next_unlock_days=_unlock_days(raw, now=now),
        has_unlock_schedule=bool(raw.get("has_unlock_schedule")),
        commit_ratio_4w=raw.get("commit_ratio_4w"),
        pr_ratio_4w=raw.get("pr_ratio_4w"),
        days_since_push=raw.get("days_since_push"),
        star_growth_pct_7d=raw.get("star_growth_pct_7d"),
        all_repos_archived=bool(raw.get("all_repos_archived")),
    )
