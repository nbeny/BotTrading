"""Trading-engine configuration loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class Mode(StrEnum):
    DRY_RUN = "dry_run"  # log only, no network calls
    DEMO = "demo"  # demo-futures.kraken.com (testnet)
    LIVE = "live"  # futures.kraken.com (real money)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class TradingConfig:
    mode: Mode = Mode.DRY_RUN
    api_key: str = ""
    api_secret: str = ""
    trading_enabled: bool = True
    auto_trading_enabled: bool = True
    max_order_usd: float = 500.0
    max_leverage: float = 3.0
    max_orders_per_hour: int = 10
    entry_timeout_s: int = 30
    reconcile_interval_s: int = 10
    # Read-only spot key (api.kraken.com). Distinct from the Futures trading key
    # above: different host, different signing scheme, and read paths are never
    # simulated by the trading mode.
    read_api_key: str = ""
    read_api_secret: str = ""
    account_poll_s: int = 60

    @classmethod
    def from_env(cls) -> TradingConfig:
        return cls(
            mode=Mode(os.getenv("TRADING_MODE", "dry_run")),
            api_key=os.getenv("KRAKEN_API_KEY", ""),
            api_secret=os.getenv("KRAKEN_API_SECRET", ""),
            trading_enabled=_bool("TRADING_ENABLED", True),
            auto_trading_enabled=_bool("AUTO_TRADING_ENABLED", True),
            max_order_usd=float(os.getenv("MAX_ORDER_USD", "500")),
            max_leverage=float(os.getenv("MAX_LEVERAGE", "3")),
            max_orders_per_hour=int(os.getenv("MAX_ORDERS_PER_HOUR", "10")),
            entry_timeout_s=int(os.getenv("ENTRY_TIMEOUT_S", "30")),
            reconcile_interval_s=int(os.getenv("RECONCILE_INTERVAL_S", "10")),
            read_api_key=os.getenv("KRAKEN_READ_API_KEY", ""),
            read_api_secret=os.getenv("KRAKEN_READ_API_SECRET", ""),
            account_poll_s=int(os.getenv("CMI_ACCOUNT_POLL_S", "60")),
        )
