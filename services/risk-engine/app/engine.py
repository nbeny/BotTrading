"""Risk engine service: validates decisions and emits risk-approved signals.

Consumes decision.events. State lives in Redis:
- ``risk:blacklist``            set of blacklisted symbols
- ``features:{symbol}``         latest price (written by ai-worker-haiku)
- ``risk:exposure``             current aggregate portfolio exposure (0..1)

Approved decisions become RiskApprovedEvent on risk.approved.events — the
actionable signal for the external trading engine.
"""

from __future__ import annotations

import logging

from cmi_common.cache import Cache
from cmi_common.events import BaseEvent, DecisionEvent
from cmi_common.events.risk import RiskApprovedEvent, RiskRejectedEvent
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED

from .rules import RiskConfig, evaluate

logger = logging.getLogger(__name__)
SERVICE = "risk-engine"

BLACKLIST_KEY = "risk:blacklist"
EXPOSURE_KEY = "risk:exposure"
PRICE_KEY = "features:{symbol}"


class RiskEngine:
    def __init__(
        self, cache: Cache, producer: EventProducer, config: RiskConfig
    ) -> None:
        self._cache = cache
        self._producer = producer
        self._config = config

    async def handle(self, event: BaseEvent) -> None:
        if not isinstance(event, DecisionEvent):
            return
        EVENTS_CONSUMED.labels(SERVICE, Topic.DECISION.value, event.event_type).inc()

        entry = await self._entry_price(event)
        is_black = bool(await self._cache.client.sismember(BLACKLIST_KEY, event.symbol))
        exposure = float(await self._cache.get_json(EXPOSURE_KEY) or 0.0)

        decision = evaluate(
            entry_price=entry,
            direction=event.direction,
            confidence=event.confidence,
            opportunity_score=event.opportunity_score,
            is_blacklisted=is_black,
            current_exposure_pct=exposure,
            config=self._config,
        )

        if not decision.approved or decision.levels is None:
            await self._reject(event, decision.reason)
            return

        levels = decision.levels
        approved = RiskApprovedEvent(
            correlation_id=event.correlation_id,
            symbol=event.symbol,
            direction=event.direction,
            entry_price=levels.entry_price,
            stop_loss=levels.stop_loss,
            take_profit=levels.take_profit,
            confidence=event.confidence,
            position_size_pct=levels.position_size_pct,
            risk_reward_ratio=levels.risk_reward_ratio,
            decision_event_id=event.event_id,
        )
        await self._cache.set_json(
            EXPOSURE_KEY, round(exposure + levels.position_size_pct, 4), ttl_seconds=0
        )
        await self._producer.publish(Topic.RISK_APPROVED, approved)
        EVENTS_PRODUCED.labels(
            SERVICE, Topic.RISK_APPROVED.value, approved.event_type
        ).inc()
        logger.info(
            "APPROVED %s entry=%s sl=%s tp=%s rr=%s",
            event.symbol,
            levels.entry_price,
            levels.stop_loss,
            levels.take_profit,
            levels.risk_reward_ratio,
        )

    async def _entry_price(self, event: DecisionEvent) -> float:
        if event.suggested_entry_price:
            return event.suggested_entry_price
        features = await self._cache.get_json(PRICE_KEY.format(symbol=event.symbol))
        return float((features or {}).get("price", 0.0))

    async def _reject(self, event: DecisionEvent, reason: str) -> None:
        logger.info("REJECTED %s: %s", event.symbol, reason)
        rejected = RiskRejectedEvent(
            correlation_id=event.correlation_id,
            symbol=event.symbol,
            reason=reason,
            decision_event_id=event.event_id,
        )
        # Rejections are published on the decision topic as an audit trail.
        await self._producer.publish(Topic.DECISION, rejected)
        EVENTS_PRODUCED.labels(SERVICE, Topic.DECISION.value, rejected.event_type).inc()
