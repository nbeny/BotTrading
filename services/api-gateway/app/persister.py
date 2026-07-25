"""Persists key events into PostgreSQL/TimescaleDB for querying + backtesting."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from cmi_common.db import Database, Decision, Signal, Trade
from cmi_common.events import AnalysisEvent, BaseEvent, DecisionEvent, RiskApprovedEvent
from cmi_common.events.execution import ExecutionEvent
from cmi_common.kafka import Topic
from cmi_common.observability import EVENTS_CONSUMED

logger = logging.getLogger(__name__)
SERVICE = "api-gateway"


class Persister:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def handle(self, event: BaseEvent) -> None:
        if isinstance(event, AnalysisEvent):
            await self._save_signal(event)
        elif isinstance(event, DecisionEvent):
            await self._save_decision(event)
        elif isinstance(event, RiskApprovedEvent):
            await self._save_trade(event)
        elif isinstance(event, ExecutionEvent):
            await self._update_trade(event)

    async def _save_signal(self, e: AnalysisEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.ANALYSIS.value, e.event_type).inc()
        async with self._db._sessionmaker() as s:  # noqa: SLF001
            stmt = insert(Signal).values(
                time=e.occurred_at,
                symbol=e.symbol,
                event_id=e.event_id,
                opportunity_score=e.opportunity_score,
                confidence=e.confidence,
                reason=e.reason,
                escalated=e.escalate,
                payload=e.model_dump(mode="json"),
            ).on_conflict_do_nothing()
            await s.execute(stmt)
            await s.commit()

    async def _save_decision(self, e: DecisionEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.DECISION.value, e.event_type).inc()
        async with self._db._sessionmaker() as s:  # noqa: SLF001
            stmt = insert(Decision).values(
                event_id=e.event_id,
                correlation_id=e.correlation_id,
                symbol=e.symbol,
                direction=e.direction,
                opportunity_score=e.opportunity_score,
                confidence=e.confidence,
                ai_validated=e.ai_validated,
                rationale=e.rationale,
                payload=e.model_dump(mode="json"),
            ).on_conflict_do_nothing(index_elements=["event_id"])
            await s.execute(stmt)
            await s.commit()

    async def _save_trade(self, e: RiskApprovedEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.RISK_APPROVED.value, e.event_type).inc()
        async with self._db._sessionmaker() as s:  # noqa: SLF001
            stmt = insert(Trade).values(
                event_id=e.event_id,
                correlation_id=e.correlation_id,
                symbol=e.symbol,
                direction=e.direction,
                entry_price=e.entry_price,
                stop_loss=e.stop_loss,
                take_profit=e.take_profit,
                confidence=e.confidence,
                position_size_pct=e.position_size_pct,
                risk_reward_ratio=e.risk_reward_ratio,
                status="approved",
            ).on_conflict_do_nothing(index_elements=["event_id"])
            await s.execute(stmt)
            await s.commit()
        logger.info("persisted trade %s @ %s", e.symbol, e.entry_price)

    async def _update_trade(self, e: ExecutionEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.EXECUTION.value, e.event_type).inc()
        async with self._db._sessionmaker() as s:  # noqa: SLF001
            stmt = (
                update(Trade)
                .where(Trade.event_id == e.risk_event_id)
                .values(
                    status=e.kind,
                    kraken_order_id=e.kraken_order_id,
                    fill_price=e.fill_price,
                    pnl=e.pnl,
                )
            )
            await s.execute(stmt)
            await s.commit()
        logger.info("updated trade %s -> %s", e.risk_event_id, e.kind)
