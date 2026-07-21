# services/trading-engine/app/runtime.py
"""Effective runtime config = env defaults overlaid with Redis `trading:runtime`.

The trading-engine is the single writer of `trading:runtime`. Env defaults are
written once at boot if absent, then the operator mutates fields via control
commands. The hot path calls `RuntimeConfig.load` on each signal/action.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .config import Mode, TradingConfig

RUNTIME_KEY = "trading:runtime"

_OVERLAY_FIELDS = (
    "trading_enabled", "auto_trading_enabled", "max_order_usd", "max_leverage",
    "max_orders_per_hour", "entry_timeout_s", "reconcile_interval_s",
)


class RuntimeConfig:
    @staticmethod
    def _to_dict(defaults: TradingConfig) -> dict[str, Any]:
        return {
            "mode": defaults.mode.value,
            "trading_enabled": defaults.trading_enabled,
            "auto_trading_enabled": defaults.auto_trading_enabled,
            "max_order_usd": defaults.max_order_usd,
            "max_leverage": defaults.max_leverage,
            "max_orders_per_hour": defaults.max_orders_per_hour,
            "entry_timeout_s": defaults.entry_timeout_s,
            "reconcile_interval_s": defaults.reconcile_interval_s,
        }

    @classmethod
    async def load(cls, cache, defaults: TradingConfig) -> TradingConfig:
        raw = await cache.get_json(RUNTIME_KEY)
        if not raw:
            return defaults
        changes: dict[str, Any] = {}
        if "mode" in raw:
            changes["mode"] = Mode(raw["mode"])
        for field in _OVERLAY_FIELDS:
            if field in raw:
                changes[field] = raw[field]
        return replace(defaults, **changes)

    @classmethod
    async def write_defaults_if_absent(cls, cache, defaults: TradingConfig) -> None:
        if await cache.get_json(RUNTIME_KEY) is None:
            await cache.set_json(RUNTIME_KEY, cls._to_dict(defaults), ttl_seconds=0)

    @classmethod
    async def set_fields(cls, cache, fields: dict[str, Any]) -> dict[str, Any]:
        current = (await cache.get_json(RUNTIME_KEY)) or {}
        current.update(fields)
        await cache.set_json(RUNTIME_KEY, current, ttl_seconds=0)
        return current
