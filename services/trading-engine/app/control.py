# services/trading-engine/app/control.py
"""Applies ControlCommandEvent from control.commands. Phase A: settings only.

Later phases add position actions (close/adjust), manual orders, and opportunity
approve/reject by extending the dispatch table.
"""
from __future__ import annotations

import logging

from cmi_common.events import BaseEvent
from cmi_common.events.control import ControlCommand, ControlCommandEvent

from .config import TradingConfig
from .runtime import RuntimeConfig

logger = logging.getLogger(__name__)

_CAPS_FIELDS = (
    "max_order_usd", "max_leverage", "max_orders_per_hour",
    "entry_timeout_s", "reconcile_interval_s",
)


class ControlHandler:
    def __init__(self, cache, *, engine, kraken, defaults: TradingConfig) -> None:
        self._cache = cache
        self._engine = engine
        self._kraken = kraken
        self._defaults = defaults

    async def handle(self, event: BaseEvent) -> None:
        if not isinstance(event, ControlCommandEvent):
            return
        cmd, p = event.command, event.payload
        logger.info("control command %s by %s: %s", cmd, event.issued_by, p)
        if cmd == ControlCommand.SET_MODE:
            await RuntimeConfig.set_fields(self._cache, {"mode": str(p["mode"])})
        elif cmd == ControlCommand.SET_KILL_SWITCH:
            await RuntimeConfig.set_fields(self._cache, {"trading_enabled": bool(p["enabled"])})
        elif cmd == ControlCommand.SET_AUTO_TRADING:
            await RuntimeConfig.set_fields(
                self._cache, {"auto_trading_enabled": bool(p["enabled"])}
            )
        elif cmd == ControlCommand.SET_CAPS:
            fields = {k: p[k] for k in _CAPS_FIELDS if k in p}
            if fields:
                await RuntimeConfig.set_fields(self._cache, fields)
        elif cmd == ControlCommand.CLOSE_POSITION:
            await self._engine.close_position(p["event_id"], issued_by=event.issued_by)
        elif cmd == ControlCommand.ADJUST_SLTP:
            await self._engine.adjust_sltp(
                p["event_id"], stop_loss=p.get("stop_loss"),
                take_profit=p.get("take_profit"), issued_by=event.issued_by,
            )
        elif cmd == ControlCommand.APPROVE_OPPORTUNITY:
            await self._engine.approve_opportunity(p["event_id"], issued_by=event.issued_by)
        elif cmd == ControlCommand.REJECT_OPPORTUNITY:
            await self._engine.reject_opportunity(
                p["event_id"], reason=p.get("reason", "operator_reject"),
                issued_by=event.issued_by,
            )
        else:
            logger.info("command %s not handled in this phase", cmd)
