# services/trading-engine/app/engine.py
"""Trading engine: turns RiskApprovedEvent into Kraken Futures orders."""
from __future__ import annotations

import logging

from cmi_common.events import BaseEvent, RiskApprovedEvent
from cmi_common.events.decision import Direction
from cmi_common.events.execution import ExecutionEvent, ExecutionKind
from cmi_common.kafka import Topic

from . import symbols
from .config import TradingConfig
from .guards import check_guards
from .sizing import compute_size

logger = logging.getLogger(__name__)

SUBMITTED_KEY = "trading:submitted:{event_id}"
POSITIONS_SET = "trading:positions"
# Kraken contract granularity (conservative defaults; refine per pair later).
CONTRACT_STEP = 0.0001
MIN_CONTRACTS = 0.0001


def _order_id(resp: dict) -> str | None:
    return (resp.get("sendStatus") or {}).get("order_id") or resp.get("order_id")


def _signal_payload(event) -> dict:
    return {
        "symbol": event.symbol, "direction": event.direction,
        "entry_price": event.entry_price, "stop_loss": event.stop_loss,
        "take_profit": event.take_profit, "confidence": event.confidence,
        "position_size_pct": event.position_size_pct,
        "correlation_id": event.correlation_id, "event_id": event.event_id,
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

        # 1. Idempotency (Kafka is at-least-once). Checked before guards so a
        # redelivery of an already-processed event does not consume a rate-limit
        # token. event_id and symbol are always present here.
        submitted_key = SUBMITTED_KEY.format(event_id=event.event_id)
        if await self._cache.get_json(submitted_key) is not None:
            logger.info("skip duplicate %s", event.event_id)
            return

        # 2. Guards (against effective runtime config).
        from .runtime import RuntimeConfig
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
            f"trading:pending:{event.event_id}", _signal_payload(event), ttl_seconds=86_400
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
            pair=pair, side=side, order_type="lmt", size=size,
            limit_price=event.entry_price, cli_ord_id=event.event_id,
        )
        await self._emit(event, ExecutionKind.SUBMITTED, size=size,
                         kraken_order_id=_order_id(entry))

        # SL/TP reduce-only (opposite side).
        exit_side = "sell" if side == "buy" else "buy"
        await self._kraken.send_order(
            pair=pair, side=exit_side, order_type="stp", size=size,
            stop_price=event.stop_loss, reduce_only=True,
            cli_ord_id=f"{event.event_id}-sl",
        )
        await self._kraken.send_order(
            pair=pair, side=exit_side, order_type="take_profit", size=size,
            stop_price=event.take_profit, reduce_only=True,
            cli_ord_id=f"{event.event_id}-tp",
        )

        # Track position + emit filled.
        await self._cache.client.sadd(POSITIONS_SET, event.event_id)
        await self._cache.set_json(
            f"trading:position:{event.event_id}",
            {
                "symbol": event.symbol, "pair": pair, "side": side,
                "size": size, "entry_price": event.entry_price,
                "position_size_pct": event.position_size_pct,
            },
            ttl_seconds=0,
        )
        await self._emit(event, ExecutionKind.FILLED, size=size,
                         fill_price=event.entry_price,
                         kraken_order_id=_order_id(entry))
        logger.info("EXECUTED %s size=%s @ %s", event.symbol, size, event.entry_price)

    async def close_position(self, event_id: str, *, issued_by: str | None = None) -> None:
        pos = await self._cache.get_json(f"trading:position:{event_id}")
        if not pos:
            logger.info("close_position: %s not tracked", event_id)
            return
        exit_side = "sell" if pos["side"] == "buy" else "buy"
        await self._kraken.send_order(
            pair=pos["pair"], side=exit_side, order_type="mkt", size=pos["size"],
            reduce_only=True, cli_ord_id=f"{event_id}-close",
        )
        logger.info("close_position %s by %s", event_id, issued_by)
        # reconcile will detect the closed position and emit CLOSED + free exposure

    async def adjust_sltp(
        self, event_id: str, *, stop_loss: float | None = None,
        take_profit: float | None = None, issued_by: str | None = None,
    ) -> None:
        pos = await self._cache.get_json(f"trading:position:{event_id}")
        if not pos:
            logger.info("adjust_sltp: %s not tracked", event_id)
            return
        exit_side = "sell" if pos["side"] == "buy" else "buy"
        if stop_loss is not None:
            await self._kraken.cancel_order(cli_ord_id=f"{event_id}-sl")
            await self._kraken.send_order(
                pair=pos["pair"], side=exit_side, order_type="stp", size=pos["size"],
                stop_price=stop_loss, reduce_only=True, cli_ord_id=f"{event_id}-sl",
            )
        if take_profit is not None:
            await self._kraken.cancel_order(cli_ord_id=f"{event_id}-tp")
            await self._kraken.send_order(
                pair=pos["pair"], side=exit_side, order_type="take_profit", size=pos["size"],
                stop_price=take_profit, reduce_only=True, cli_ord_id=f"{event_id}-tp",
            )
        logger.info("adjust_sltp %s sl=%s tp=%s by %s", event_id, stop_loss, take_profit, issued_by)

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
