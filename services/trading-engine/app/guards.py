"""Trading-engine safety guards evaluated before any Kraken API call.

Returns a machine-readable reason string when blocked, else None. Notional and
leverage caps live in sizing.py; here we handle the kill-switch and the business
rate-limit (orders per hour), both backed by Redis so they work across replicas.
"""

from __future__ import annotations

from typing import Protocol

from .config import TradingConfig

ORDERS_RATE_KEY = "trading:orders"


class GuardCache(Protocol):
    async def allow(self, key: str, limit: int, window_seconds: int) -> bool: ...


async def check_guards(cache: GuardCache, config: TradingConfig) -> str | None:
    if not config.trading_enabled:
        return "kill_switch"
    allowed = await cache.allow(ORDERS_RATE_KEY, config.max_orders_per_hour, 3600)
    if not allowed:
        return "rate_limit"
    return None
