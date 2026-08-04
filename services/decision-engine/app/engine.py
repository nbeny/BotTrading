"""Decision engine service: turns Haiku analyses into deterministic decisions.

Consumes market.analysis.events (which already carry correlated features),
applies the deterministic scoring model, and emits decision.events when the
blended score clears the decision threshold. This is the non-AI, always-on
path that complements the Sonnet analyst.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from cmi_common.events import AnalysisEvent, BaseEvent
from cmi_common.events.base import Source
from cmi_common.events.decision import DecisionEvent, Direction
from cmi_common.events.risk import RiskRejectedEvent
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED

from .features_map import features_from
from .scoring import score

logger = logging.getLogger(__name__)
SERVICE = "decision-engine"


class DecisionEngine:
    def __init__(
        self,
        producer: EventProducer,
        *,
        decision_threshold: int = 70,
    ) -> None:
        self._producer = producer
        self._threshold = decision_threshold

    async def handle(self, event: BaseEvent) -> None:
        if isinstance(event, AnalysisEvent):
            await self._on_analysis(event)

    async def _on_analysis(self, event: AnalysisEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.ANALYSIS.value, event.event_type).inc()
        features = features_from(
            event.meta.get("features", {}), now=datetime.now(tz=UTC)
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
