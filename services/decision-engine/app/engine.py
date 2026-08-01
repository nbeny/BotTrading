"""Decision engine service: turns Haiku analyses into deterministic decisions.

Consumes market.analysis.events (which already carry correlated features) and
market.sentiment.events, applies the deterministic scoring model, and emits
decision.events when the blended score clears the decision threshold. This is
the non-AI, always-on path that complements the Sonnet analyst.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

from cmi_common.events import AnalysisEvent, BaseEvent, SentimentEvent
from cmi_common.events.base import Source
from cmi_common.events.decision import DecisionEvent, Direction
from cmi_common.events.risk import RiskRejectedEvent
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED

from .scoring import Features, score

logger = logging.getLogger(__name__)
SERVICE = "decision-engine"


#: Symbol the collectors assign to crypto-relevant content that names no coin --
#: regulation, macro, exchange incidents. It moves the whole market, not one book.
MARKET_SYMBOL = "MARKET"


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


def _unlock_days(raw: dict) -> float | None:
    """Days until the next unlock, from the absolute date the store carries.

    Stored absolute and converted at read time: a stored "days remaining" would
    silently age between the collector's poll and the decision that reads it.
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
    return max(0.0, (at - datetime.now(tz=UTC)).total_seconds() / 86400.0)


class DecisionEngine:
    def __init__(
        self,
        producer: EventProducer,
        *,
        decision_threshold: int = 70,
        market_ttl_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._producer = producer
        self._threshold = decision_threshold
        self._market_ttl = market_ttl_seconds
        self._clock = clock
        #: (score, observed_at) of the most recent market-wide read.
        self._market: tuple[float, float] | None = None

    async def handle(self, event: BaseEvent) -> None:
        if isinstance(event, AnalysisEvent):
            await self._on_analysis(event)
        elif isinstance(event, SentimentEvent):
            EVENTS_CONSUMED.labels(
                SERVICE, Topic.SENTIMENT.value, event.event_type
            ).inc()
            # Per-symbol sentiment is already folded in via the analysis
            # features. The market-wide read is not carried by anything else, so
            # it is held here and applied to symbols that have none of their own.
            if event.symbol == MARKET_SYMBOL:
                self._market = (event.sentiment_score, self._clock())

    def _market_sentiment(self) -> float | None:
        """The current regime read, or None once it is too old to mean anything.

        Without the expiry a quiet weekend would keep applying Friday's mood to
        Monday's decisions, and a collector outage would freeze the last value
        in place indefinitely.
        """
        if self._market is None:
            return None
        value, observed_at = self._market
        if self._clock() - observed_at > self._market_ttl:
            return None
        return value

    async def _on_analysis(self, event: AnalysisEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.ANALYSIS.value, event.event_type).inc()
        raw = event.meta.get("features", {})
        features = Features(
            price_change_pct_24h=event.price_change_pct_24h,
            volume_spike_ratio=event.volume_spike_ratio,
            liquidity_usd=_liquidity(raw),
            sentiment_score=event.sentiment_score,
            social_growth=event.social_growth,
            news_impact=1.0 if raw.get("has_news") else None,
            market_sentiment=self._market_sentiment(),
            funding_rate_8h=raw.get("funding_rate_8h"),
            long_short_account_ratio=raw.get("long_short_account_ratio"),
            open_interest_change_pct_24h=raw.get("open_interest_change_pct_24h"),
            tvl_change_pct_7d=raw.get("tvl_change_pct_7d"),
            fees_change_pct_7d=raw.get("fees_change_pct_7d"),
            next_unlock_pct_supply=raw.get("next_unlock_pct_supply"),
            next_unlock_days=_unlock_days(raw),
            has_unlock_schedule=bool(raw.get("has_unlock_schedule")),
        )
        result = score(features)
        if result.opportunity_score < self._threshold:
            # Reuse the risk engine's audit event rather than inventing a second
            # one of identical shape. Without this the deterministic path is the
            # only stage whose rejections leave no trace, so the funnel would
            # show signals vanishing here with no reason attached.
            rejected = RiskRejectedEvent(
                source=Source.DECISION_ENGINE,
                correlation_id=event.correlation_id,
                symbol=event.symbol,
                reason=(
                    f"score {result.opportunity_score} below decision threshold "
                    f"{self._threshold}"
                ),
                decision_event_id=event.event_id,
            )
            await self._producer.publish(Topic.DECISION, rejected)
            EVENTS_PRODUCED.labels(
                SERVICE, Topic.DECISION.value, rejected.event_type
            ).inc()
            return
        decision = DecisionEvent(
            source=Source.DECISION_ENGINE,
            correlation_id=event.correlation_id,
            symbol=event.symbol,
            direction=Direction.LONG,
            opportunity_score=result.opportunity_score,
            confidence=result.confidence,
            rationale=(
                f"Deterministic score {result.opportunity_score} "
                f"(breakdown={ {k: round(v, 2) for k, v in result.breakdown.items()} })"
            ),
            ai_validated=False,
            correlated_event_ids=[event.event_id],
            meta={"breakdown": result.breakdown},
        )
        await self._producer.publish(Topic.DECISION, decision)
        EVENTS_PRODUCED.labels(SERVICE, Topic.DECISION.value, decision.event_type).inc()
        logger.info("decision %s score=%d", event.symbol, result.opportunity_score)
