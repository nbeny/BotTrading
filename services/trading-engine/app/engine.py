# services/trading-engine/app/engine.py
"""Trading engine: turns RiskApprovedEvent into Kraken Futures orders."""

from __future__ import annotations

import logging

from cmi_common.events import BaseEvent, RiskApprovedEvent
from cmi_common.events.decision import Direction
from cmi_common.events.execution import ExecutionEvent, ExecutionKind
from cmi_common.kafka import Topic
from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED

from . import symbols
from .config import TradingConfig
from .guards import check_guards
from .runtime import RuntimeConfig
from .sizing import compute_size

logger = logging.getLogger(__name__)

SERVICE = "trading-engine"

SUBMITTED_KEY = "trading:submitted:{event_id}"
POSITIONS_SET = "trading:positions"
# Kraken contract granularity (conservative defaults; refine per pair later).
CONTRACT_STEP = 0.0001
MIN_CONTRACTS = 0.0001


def _order_id(resp: dict) -> str | None:
    return (resp.get("sendStatus") or {}).get("order_id") or resp.get("order_id")


def _signal_payload(event) -> dict:
    """Shape stored under `trading:pending:*` — control-api's `list_pending`
    (services/control-api/app/routers/opportunities.py) returns this dict
    verbatim, and the frontend renders it as `Opportunity`
    (frontend/src/lib/types/domain.ts). Every field that type declares must be
    present here, or the terminal renders `undefined`/`NaN`.

    `correlation_id` and `event_id` are not part of that contract but are kept
    so `approve_opportunity`/`reject_opportunity` below can reconstruct the
    RiskApprovedEvent from this same Redis record.

    `source`: the mock (frontend/src/lib/mock/store.ts) — the de-facto contract
    the frontend was built against — fills this with analysis provenance
    (haiku_triage/sonnet_decision/manual), not the approving service. Prefer
    `decision_source`, propagated from the DecisionEvent this approval was
    raised for; fall back to the event's own `source` (risk-engine) only for an
    older producer that never set it, so the field is never null.
    """
    return {
        "opportunity_id": event.event_id,
        "symbol": event.symbol,
        "direction": event.direction,
        # DecisionEvent (and therefore RiskApprovedEvent) carries the score on a
        # 0..100 int scale; the whole frontend speaks 0..1 (ScoreChip renders
        # `score * 100`). Convert at this edge only, mirroring
        # api-gateway/app/read_api.py::map_decision — passing the raw int
        # through turns a real score of 79 into a rendered 7900%.
        "opportunity_score": (
            round(event.opportunity_score / 100, 2)
            if event.opportunity_score is not None
            else 0.0
        ),
        "confidence": event.confidence,
        "entry_price": event.entry_price,
        "stop_loss": event.stop_loss,
        "take_profit": event.take_profit,
        "position_size_pct": event.position_size_pct,
        "risk_reward_ratio": event.risk_reward_ratio,
        "rationale": event.rationale,
        "key_risks": event.key_risks,
        "ai_validated": event.ai_validated,
        "source": event.decision_source or event.source,
        "status": "pending",
        "created_at": event.occurred_at.isoformat(),
        "correlation_id": event.correlation_id,
        "event_id": event.event_id,
    }


class TradingEngine:
    def __init__(self, cache, producer, kraken, defaults: TradingConfig) -> None:
        self._cache = cache
        self._producer = producer
        self._kraken = kraken
        self._defaults = defaults

    async def handle(self, event: BaseEvent) -> None:
        if not isinstance(event, RiskApprovedEvent):
            return

        # Counted before the idempotency check: a redelivery is still an event
        # this service consumed, and dropping it would under-report precisely
        # when Kafka is redelivering most.
        EVENTS_CONSUMED.labels(
            SERVICE, Topic.RISK_APPROVED.value, event.event_type
        ).inc()

        # 1. Idempotency (Kafka is at-least-once). Checked before guards so a
        # redelivery of an already-processed event does not consume a rate-limit
        # token. event_id and symbol are always present here.
        submitted_key = SUBMITTED_KEY.format(event_id=event.event_id)
        if await self._cache.get_json(submitted_key) is not None:
            logger.info("skip duplicate %s", event.event_id)
            return

        # 2. Guards (against effective runtime config).
        config = await RuntimeConfig.load(self._cache, self._defaults)
        reason = await check_guards(self._cache, config)
        if reason is not None:
            await self._reject(event, reason)
            return

        # 3. Strict whitelist.
        if not symbols.is_whitelisted(event.symbol):
            await self._reject(event, "unknown_symbol")
            return

        # 4. Sizing.
        equity = await self._equity()
        size = compute_size(
            equity_usd=equity,
            position_size_pct=event.position_size_pct,
            entry_price=event.entry_price,
            max_order_usd=config.max_order_usd,
            max_leverage=config.max_leverage,
            contract_step=CONTRACT_STEP,
            min_contracts=MIN_CONTRACTS,
        )
        if size <= 0:
            await self._reject(event, "below_min_size")
            return

        # 5. Auto-trading gate: if off, queue for human approval and stop.
        if not config.auto_trading_enabled:
            await self._queue_pending(event)
            return
        await self._execute(event, config, size)

    PENDING_SET = "trading:pending"

    async def _queue_pending(self, event) -> None:
        await self._cache.set_json(
            f"trading:pending:{event.event_id}",
            _signal_payload(event),
            ttl_seconds=86_400,
        )
        await self._cache.client.sadd("trading:pending", event.event_id)
        await self._emit(event, ExecutionKind.PENDING)
        logger.info("PENDING %s (auto-trading off)", event.symbol)

    async def _execute(self, event, config, size) -> None:
        pair = symbols.to_kraken_pair(event.symbol)

        # Entry (limit, market fallback handled by reconcile/timeout in prod).
        submitted_key = SUBMITTED_KEY.format(event_id=event.event_id)
        await self._cache.set_json(submitted_key, True, ttl_seconds=86_400)
        side = "buy" if event.direction == Direction.LONG else "sell"
        entry = await self._kraken.send_order(
            pair=pair,
            side=side,
            order_type="lmt",
            size=size,
            limit_price=event.entry_price,
            cli_ord_id=event.event_id,
        )
        await self._emit(
            event, ExecutionKind.SUBMITTED, size=size, kraken_order_id=_order_id(entry)
        )

        # SL/TP reduce-only (opposite side).
        exit_side = "sell" if side == "buy" else "buy"
        await self._kraken.send_order(
            pair=pair,
            side=exit_side,
            order_type="stp",
            size=size,
            stop_price=event.stop_loss,
            reduce_only=True,
            cli_ord_id=f"{event.event_id}-sl",
        )
        await self._kraken.send_order(
            pair=pair,
            side=exit_side,
            order_type="take_profit",
            size=size,
            stop_price=event.take_profit,
            reduce_only=True,
            cli_ord_id=f"{event.event_id}-tp",
        )

        # Track position + emit filled.
        await self._cache.client.sadd(POSITIONS_SET, event.event_id)
        await self._cache.set_json(
            f"trading:position:{event.event_id}",
            {
                "symbol": event.symbol,
                "pair": pair,
                "side": side,
                "size": size,
                "entry_price": event.entry_price,
                "position_size_pct": event.position_size_pct,
            },
            ttl_seconds=0,
        )
        await self._emit(
            event,
            ExecutionKind.FILLED,
            size=size,
            fill_price=event.entry_price,
            kraken_order_id=_order_id(entry),
        )
        logger.info("EXECUTED %s size=%s @ %s", event.symbol, size, event.entry_price)

    async def approve_opportunity(
        self, event_id: str, *, issued_by: str | None = None
    ) -> None:
        payload = await self._cache.get_json(f"trading:pending:{event_id}")
        if not payload:
            logger.info("approve: %s not pending", event_id)
            return
        await self._cache.client.srem("trading:pending", event_id)
        event = RiskApprovedEvent(
            event_id=payload["event_id"],
            correlation_id=payload["correlation_id"],
            symbol=payload["symbol"],
            direction=payload["direction"],
            entry_price=payload["entry_price"],
            stop_loss=payload["stop_loss"],
            take_profit=payload["take_profit"],
            confidence=payload["confidence"],
            position_size_pct=payload["position_size_pct"],
        )
        config = await RuntimeConfig.load(self._cache, self._defaults)
        # re-run guards + sizing at approval time
        reason = await check_guards(self._cache, config)
        if reason is not None:
            await self._reject(event, reason)
            return
        size = compute_size(
            equity_usd=await self._equity(),
            position_size_pct=event.position_size_pct,
            entry_price=event.entry_price,
            max_order_usd=config.max_order_usd,
            max_leverage=config.max_leverage,
            contract_step=CONTRACT_STEP,
            min_contracts=MIN_CONTRACTS,
        )
        if size <= 0:
            await self._reject(event, "below_min_size")
            return
        await self._execute(event, config, size)
        logger.info("APPROVED %s by %s", event.symbol, issued_by)

    async def reject_opportunity(
        self,
        event_id: str,
        *,
        reason: str = "operator_reject",
        issued_by: str | None = None,
    ) -> None:
        payload = await self._cache.get_json(f"trading:pending:{event_id}")
        if not payload:
            return
        await self._cache.client.srem("trading:pending", event_id)
        event = RiskApprovedEvent(
            event_id=payload["event_id"],
            correlation_id=payload["correlation_id"],
            symbol=payload["symbol"],
            direction=payload["direction"],
            entry_price=payload["entry_price"],
            stop_loss=payload["stop_loss"],
            take_profit=payload["take_profit"],
            confidence=payload["confidence"],
            position_size_pct=payload["position_size_pct"],
        )
        await self._emit(event, ExecutionKind.REJECTED, reason=reason)
        logger.info("operator rejected %s: %s", event.symbol, reason)

    async def close_position(
        self, event_id: str, *, issued_by: str | None = None
    ) -> None:
        pos = await self._cache.get_json(f"trading:position:{event_id}")
        if not pos:
            logger.info("close_position: %s not tracked", event_id)
            return
        exit_side = "sell" if pos["side"] == "buy" else "buy"
        await self._kraken.send_order(
            pair=pos["pair"],
            side=exit_side,
            order_type="mkt",
            size=pos["size"],
            reduce_only=True,
            cli_ord_id=f"{event_id}-close",
        )
        logger.info("close_position %s by %s", event_id, issued_by)
        # reconcile will detect the closed position and emit CLOSED + free exposure

    async def adjust_sltp(
        self,
        event_id: str,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        issued_by: str | None = None,
    ) -> None:
        pos = await self._cache.get_json(f"trading:position:{event_id}")
        if not pos:
            logger.info("adjust_sltp: %s not tracked", event_id)
            return
        exit_side = "sell" if pos["side"] == "buy" else "buy"
        if stop_loss is not None:
            await self._kraken.cancel_order(cli_ord_id=f"{event_id}-sl")
            await self._kraken.send_order(
                pair=pos["pair"],
                side=exit_side,
                order_type="stp",
                size=pos["size"],
                stop_price=stop_loss,
                reduce_only=True,
                cli_ord_id=f"{event_id}-sl",
            )
        if take_profit is not None:
            await self._kraken.cancel_order(cli_ord_id=f"{event_id}-tp")
            await self._kraken.send_order(
                pair=pos["pair"],
                side=exit_side,
                order_type="take_profit",
                size=pos["size"],
                stop_price=take_profit,
                reduce_only=True,
                cli_ord_id=f"{event_id}-tp",
            )
        logger.info(
            "adjust_sltp %s sl=%s tp=%s by %s",
            event_id,
            stop_loss,
            take_profit,
            issued_by,
        )

    async def manual_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        issued_by: str | None = None,
    ) -> None:
        config = await RuntimeConfig.load(self._cache, self._defaults)
        reason = await check_guards(self._cache, config)
        if reason is not None:
            logger.info("manual_order blocked: %s", reason)
            return
        if not symbols.is_whitelisted(symbol):
            logger.info("manual_order rejected: unknown symbol %s", symbol)
            return
        base = symbols.normalize(symbol)
        pair = symbols.to_kraken_pair(symbol)
        kraken_type = "mkt" if order_type == "market" else "lmt"
        # Notional cap: for a limit order use its price; for a market order fall
        # back to the latest known price (features:{base}, written by the pipeline)
        # so market orders can't silently exceed MAX_ORDER_USD. If no price can be
        # resolved we can't compute notional — proceed but warn.
        ref_price = price or 0.0
        if ref_price <= 0:
            features = await self._cache.get_json(f"features:{base}")
            ref_price = float((features or {}).get("price", 0.0))
        if ref_price > 0:
            notional = quantity * ref_price
            if notional > config.max_order_usd:
                logger.info("manual_order rejected: notional %.2f over cap", notional)
                return
        else:
            logger.warning(
                "manual_order %s: no reference price, notional cap not enforced", base
            )
        await self._kraken.send_order(
            pair=pair,
            side=side,
            order_type=kraken_type,
            size=quantity,
            limit_price=price,
            cli_ord_id=f"manual-{base}-{side}",
        )
        logger.info(
            "MANUAL ORDER %s %s %s qty=%s by %s",
            base,
            side,
            order_type,
            quantity,
            issued_by,
        )

    async def _equity(self) -> float:
        accounts = await self._kraken.get_accounts()
        flex = accounts.get("accounts", {}).get("flex", {})
        return float(flex.get("portfolioValue", 0.0))

    async def _reject(self, event: RiskApprovedEvent, reason: str) -> None:
        logger.info("REJECT %s: %s", event.symbol, reason)
        await self._emit(event, ExecutionKind.REJECTED, reason=reason)

    async def _emit(
        self,
        event: RiskApprovedEvent,
        kind: ExecutionKind,
        *,
        reason: str | None = None,
        size: float | None = None,
        fill_price: float | None = None,
        kraken_order_id: str | None = None,
    ) -> None:
        ev = ExecutionEvent(
            correlation_id=event.correlation_id,
            kind=kind,
            symbol=event.symbol,
            direction=event.direction,
            risk_event_id=event.event_id,
            reason=reason,
            size=size,
            fill_price=fill_price,
            kraken_order_id=kraken_order_id,
        )
        await self._producer.publish(Topic.EXECUTION, ev)
        EVENTS_PRODUCED.labels(SERVICE, Topic.EXECUTION.value, ev.event_type).inc()
