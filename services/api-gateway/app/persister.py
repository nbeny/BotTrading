"""Persists key events into PostgreSQL/TimescaleDB for querying + backtesting."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql.elements import ColumnElement

from cmi_common.db import (
    AccountSnapshot,
    Database,
    Decision,
    DecisionJournal,
    PipelineRejection,
    Price,
    Signal,
    Token,
    Trade,
)
from cmi_common.events import (
    AnalysisEvent,
    BaseEvent,
    DecisionEvent,
    PriceEvent,
    RiskApprovedEvent,
)
from cmi_common.events.account import AccountSnapshotEvent
from cmi_common.events.base import Source
from cmi_common.events.execution import ExecutionEvent
from cmi_common.events.journal import JournalEntryEvent
from cmi_common.events.risk import RiskRejectedEvent
from cmi_common.kafka import Topic
from cmi_common.observability import EVENTS_CONSUMED

# Reused rather than duplicated: `event_type` is still an EventType member (the
# model's use_enum_values only applies to validated input), and str() on it
# yields "EventType.ACCOUNT_SNAPSHOT" — which would split one Prometheus series
# in two. The archiver already carries the fix and its rationale.
from .archiver import event_type_of

logger = logging.getLogger(__name__)
SERVICE = "api-gateway"


def _naive_utc(dt: datetime) -> datetime:
    """Naive UTC for the tz-naive TIMESTAMP columns — asyncpg rejects inserting a
    tz-aware value into `TIMESTAMP WITHOUT TIME ZONE` ("can't subtract offset-naive
    and offset-aware datetimes"). Mirrors the read-side naive-UTC handling."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


_STAGE_BY_SOURCE = {
    Source.DECISION_ENGINE: "decision_engine",
    Source.RISK_ENGINE: "risk_engine",
}


def stage_for(source: str) -> str:
    """Which pipeline stage refused.

    ``BaseEvent`` sets ``use_enum_values=True``, so ``event.source`` is a plain
    string rather than a ``Source`` member. The lookup below still resolves
    because ``Source`` is a str enum, but reaching for ``.value`` would raise
    AttributeError — inside a Kafka consumer loop, on exactly the unmapped case
    this fallback exists to survive. Unmapped producers keep their raw name: an
    unexpected rejector must stay visible in the funnel.
    """
    return _STAGE_BY_SOURCE.get(source, str(source))


class TokenMetaCache:
    """Throttle for token reference writes.

    221 price events land every minute carrying metadata that changes about once
    a day. Writing on each one is pure write amplification, so a write happens
    only when the metadata actually changed, or once per interval to heal drift.
    """

    def __init__(self, min_interval_s: float = 3600.0) -> None:
        self._min_interval = min_interval_s
        self._seen: dict[str, tuple[tuple, float]] = {}

    def should_write(self, coin_id: str, meta: tuple, *, now: float) -> bool:
        previous = self._seen.get(coin_id)
        if previous is not None:
            last_meta, last_at = previous
            if last_meta == meta and now - last_at < self._min_interval:
                return False
        self._seen[coin_id] = (meta, now)
        return True


class Persister:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._token_meta = TokenMetaCache()

    async def handle(self, event: BaseEvent) -> None:
        if isinstance(event, PriceEvent):
            await self._save_price(event)
        elif isinstance(event, AnalysisEvent):
            await self._save_signal(event)
        elif isinstance(event, JournalEntryEvent):
            await self._save_journal(event)
        elif isinstance(event, RiskRejectedEvent):
            # RiskRejectedEvent and DecisionEvent both arrive on the decision
            # topic. Checked here, ahead of DecisionEvent: they are independent
            # direct subclasses of BaseEvent (neither derives from the other),
            # so isinstance() cannot conflate them regardless of order — this
            # placement is belt-and-suspenders to keep a rejection from ever
            # being mistaken for a decision.
            await self._save_rejection(event)
        elif isinstance(event, DecisionEvent):
            await self._save_decision(event)
        elif isinstance(event, RiskApprovedEvent):
            await self._save_trade(event)
        elif isinstance(event, ExecutionEvent):
            await self._update_trade(event)
        elif isinstance(event, AccountSnapshotEvent):
            await self._save_account_snapshot(event)

    async def _save_price(self, e: PriceEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.PRICE.value, event_type_of(e)).inc()
        async with self._db._sessionmaker() as s:
            stmt = insert(Price).values(
                time=_naive_utc(e.occurred_at),
                symbol=e.symbol,
                price_usd=e.price_usd,
                market_cap_usd=e.market_cap_usd,
                volume_24h_usd=e.volume_24h_usd,
                price_change_pct_24h=e.price_change_pct_24h,
                source="coingecko",
            ).on_conflict_do_nothing()
            await s.execute(stmt)
            await s.commit()
        await self._save_token(e)

    async def _save_token(self, e: PriceEvent) -> None:
        meta = (e.name, e.market_cap_rank, e.is_trending)
        if not self._token_meta.should_write(e.coin_id, meta, now=time.monotonic()):
            return
        async with self._db._sessionmaker() as s:
            stmt = insert(Token).values(
                symbol=e.symbol,
                coin_id=e.coin_id,
                name=e.name,
                metadata_={
                    "market_cap_rank": e.market_cap_rank,
                    "is_trending": e.is_trending,
                },
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["coin_id"],
                set_={
                    "symbol": stmt.excluded.symbol,
                    "name": stmt.excluded.name,
                    "metadata": stmt.excluded.metadata,
                },
            )
            await s.execute(stmt)
            await s.commit()

    async def _save_signal(self, e: AnalysisEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.ANALYSIS.value, event_type_of(e)).inc()
        async with self._db._sessionmaker() as s:
            stmt = insert(Signal).values(
                time=_naive_utc(e.occurred_at),
                symbol=e.symbol,
                event_id=e.event_id,
                opportunity_score=e.opportunity_score,
                confidence=e.confidence,
                reason=e.reason,
                escalated=e.escalate,
                ambiguous=e.ambiguous,
                block_reason=e.block_reason,
                factors_present=e.factors_present,
                payload=e.model_dump(mode="json"),
            ).on_conflict_do_nothing()
            await s.execute(stmt)
            await s.commit()

    async def _save_journal(self, e: JournalEntryEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.JOURNAL.value, event_type_of(e)).inc()
        payload = e.model_dump(
            mode="json",
            exclude={"event_type", "schema_version", "source", "occurred_at", "meta"},
        )
        payload["time"] = _naive_utc(e.occurred_at)
        async with self._db._sessionmaker() as s:
            await s.execute(
                insert(DecisionJournal).values(**payload).on_conflict_do_nothing()
            )
            await s.commit()

    async def _complete_journal(
        self, where: ColumnElement[bool], **values: Any
    ) -> None:
        """Enrich the existing journal row rather than inserting a second one --
        a duplicate would double-count that decision in every later cohort."""
        async with self._db._sessionmaker() as s:
            await s.execute(update(DecisionJournal).where(where).values(**values))
            await s.commit()

    async def _save_rejection(self, e: RiskRejectedEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.DECISION.value, event_type_of(e)).inc()
        async with self._db._sessionmaker() as s:
            stmt = insert(PipelineRejection).values(
                time=_naive_utc(e.occurred_at),
                event_id=e.event_id,
                stage=stage_for(e.source),
                symbol=e.symbol,
                correlation_id=e.correlation_id,
                reason=e.reason,
            ).on_conflict_do_nothing()
            await s.execute(stmt)
            await s.commit()
        if e.decision_event_id:
            await self._complete_journal(
                DecisionJournal.decision_event_id == e.decision_event_id,
                risk_verdict="rejected",
                risk_reason=e.reason,
            )

    async def _save_decision(self, e: DecisionEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.DECISION.value, event_type_of(e)).inc()
        async with self._db._sessionmaker() as s:
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
        EVENTS_CONSUMED.labels(
            SERVICE, Topic.RISK_APPROVED.value, event_type_of(e)
        ).inc()
        async with self._db._sessionmaker() as s:
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
        if e.decision_event_id:
            await self._complete_journal(
                DecisionJournal.decision_event_id == e.decision_event_id,
                risk_verdict="approved",
                risk_event_id=e.event_id,
                entry_price=e.entry_price,
                stop_loss=e.stop_loss,
                take_profit=e.take_profit,
                risk_reward_ratio=e.risk_reward_ratio,
            )
        logger.info("persisted trade %s @ %s", e.symbol, e.entry_price)

    async def _save_account_snapshot(self, e: AccountSnapshotEvent) -> None:
        EVENTS_CONSUMED.labels(
            SERVICE, Topic.ACCOUNT_SNAPSHOT.value, event_type_of(e)
        ).inc()
        # The public property, not the private attribute the older handlers
        # reach through; same object, and it needs no `noqa`.
        async with self._db.sessionmaker() as s:
            stmt = insert(AccountSnapshot).values(
                event_id=e.event_id,
                venue=e.venue,
                equity_usd=e.equity_usd,
                cash_usd=e.cash_usd,
                balances=e.balances,
                fetched_at=_naive_utc(e.occurred_at),
            ).on_conflict_do_nothing(index_elements=["event_id"])
            await s.execute(stmt)
            await s.commit()

    async def _update_trade(self, e: ExecutionEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.EXECUTION.value, event_type_of(e)).inc()
        async with self._db._sessionmaker() as s:
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
        # ExecutionEvent carries risk_event_id, not decision_event_id. Joining on
        # the latter would produce an UPDATE that never matches -- silent, and
        # the journal would never receive an execution outcome.
        await self._complete_journal(
            DecisionJournal.risk_event_id == e.risk_event_id,
            execution_event_id=e.event_id,
            execution_kind=e.kind,
            fill_price=e.fill_price,
            realized_pnl=e.pnl,
        )
        logger.info("updated trade %s -> %s", e.risk_event_id, e.kind)
