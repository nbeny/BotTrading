"""Decision engine service: turns Haiku analyses into deterministic decisions.

Consumes market.analysis.events (which already carry correlated features) and
market.sentiment.events, applies the deterministic scoring model, and emits
decision.events when the blended score clears the decision threshold. This is
the non-AI, always-on path that complements the Sonnet analyst.
"""

from __future__ import annotations

import logging

from cmi_common.events import AnalysisEvent, BaseEvent, SentimentEvent
from cmi_common.events.base import Source
from cmi_common.events.decision import DecisionEvent, Direction
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED

from .scoring import Features, score

logger = logging.getLogger(__name__)
SERVICE = "decision-engine"


class DecisionEngine:
    def __init__(
        self, producer: EventProducer, *, decision_threshold: int = 70
    ) -> None:
        self._producer = producer
        self._threshold = decision_threshold

    async def handle(self, event: BaseEvent) -> None:
        if isinstance(event, AnalysisEvent):
            await self._on_analysis(event)
        elif isinstance(event, SentimentEvent):
            EVENTS_CONSUMED.labels(
                SERVICE, Topic.SENTIMENT.value, event.event_type
            ).inc()
            # Sentiment is folded in via the analysis features; nothing to emit.

    async def _on_analysis(self, event: AnalysisEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.ANALYSIS.value, event.event_type).inc()
        raw = event.meta.get("features", {})
        features = Features(
            price_change_pct_24h=event.price_change_pct_24h,
            volume_spike_ratio=event.volume_spike_ratio,
            liquidity_usd=raw.get("liquidity_usd"),
            sentiment_score=event.sentiment_score,
            social_growth=event.social_growth,
            news_impact=1.0 if raw.get("has_news") else None,
        )
        result = score(features)
        if result.opportunity_score < self._threshold:
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
        logger.info(
            "decision %s score=%d", event.symbol, result.opportunity_score
        )
