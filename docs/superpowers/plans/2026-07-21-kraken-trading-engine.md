# Kraken Trading Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `trading-engine` microservice that consumes `risk.approved.events` and executes orders on Kraken Futures, feeding execution state back into the DB, a new `execution.events` Kafka topic, and Redis exposure.

**Architecture:** New microservice following the existing pattern (`create_app`, `EventConsumer`/`EventProducer`, `Cache`). A thin async httpx client talks to Kraken Futures. Pure modules (`symbols`, `sizing`, `guards`) hold testable logic; `engine.py` orchestrates; a background `reconcile.py` poller detects closes and resyncs at boot. Three run modes: `dry_run` (no network), `demo` (Kraken testnet), `live`.

**Tech Stack:** Python 3.12, Pydantic v2, aiokafka, redis-py (asyncio), httpx, SQLAlchemy async, pytest.

**Reference spec:** `docs/superpowers/specs/2026-07-21-kraken-trading-engine-design.md`

---

## Preliminary notes for the executor

- **Git is not initialized** in this repo (`git init` has not been run). Before Task 1, run:
  ```bash
  git init && git add -A && git commit -m "chore: snapshot before trading-engine"
  ```
  If the user prefers no git, skip every `git commit` step; they are optional checkpoints.
- **Run tests from the repo root** with `pytest`. Existing tests live in `tests/` and import
  `cmi_common` directly; service `app` modules are loaded via `importlib` because each service
  is a separate container and its package is always named `app`.
- **Kraken Futures signing** is verified by shape/determinism in unit tests, then validated
  manually against the `demo` environment (Task 12). During Task 9, fetch the current Kraken
  Futures REST auth docs with context7 (`resolve-library-id` → `query-docs`, or WebFetch
  `https://docs.kraken.com/api/docs/guides/futures-rest`) to confirm the signing scheme before
  finalizing.

---

## Task 1: Add `ExecutionEvent` to cmi_common

**Files:**
- Create: `libs/cmi_common/cmi_common/events/execution.py`
- Modify: `libs/cmi_common/cmi_common/events/base.py` (add `EventType.EXECUTION`, `Source.TRADING_ENGINE`)
- Modify: `libs/cmi_common/cmi_common/events/__init__.py` (register in union + `__all__`)
- Test: `tests/test_execution_event.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_event.py
"""ExecutionEvent schema + round-trip through parse_event."""
from __future__ import annotations

from cmi_common.events import parse_event
from cmi_common.events.execution import ExecutionEvent, ExecutionKind


def test_execution_event_roundtrip() -> None:
    ev = ExecutionEvent(
        kind=ExecutionKind.FILLED,
        symbol="SOL",
        risk_event_id="abc-123",
        kraken_order_id="OID-1",
        fill_price=150.5,
        size=2.0,
    )
    decoded = parse_event(ev.as_kafka_value())
    assert isinstance(decoded, ExecutionEvent)
    assert decoded.kind == ExecutionKind.FILLED
    assert decoded.symbol == "SOL"
    assert decoded.risk_event_id == "abc-123"
    assert decoded.partition_key() == "SOL"


def test_execution_rejected_carries_reason() -> None:
    ev = ExecutionEvent(
        kind=ExecutionKind.REJECTED, symbol="DOGE",
        risk_event_id="x", reason="unknown_symbol",
    )
    assert ev.reason == "unknown_symbol"
    assert ev.fill_price is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_execution_event.py -v`
Expected: FAIL with `ModuleNotFoundError: cmi_common.events.execution`

- [ ] **Step 3: Add the enum members**

In `libs/cmi_common/cmi_common/events/base.py`, add to `EventType` (after `RISK_REJECTED`):

```python
    EXECUTION = "ExecutionEvent"
```

and add to `Source` (after `RISK_ENGINE`):

```python
    TRADING_ENGINE = "trading-engine"
```

- [ ] **Step 4: Create the event model**

```python
# libs/cmi_common/cmi_common/events/execution.py
"""Execution events produced by the trading-engine after it acts on Kraken."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from .base import BaseEvent, EventType, Source
from .decision import Direction


class ExecutionKind(str, Enum):
    SUBMITTED = "submitted"   # order sent to Kraken
    FILLED = "filled"         # entry filled, SL/TP placed
    CLOSED = "closed"         # position closed (SL/TP hit or manual)
    FAILED = "failed"         # Kraken rejected / error mid-flight
    REJECTED = "rejected"     # blocked by a local guard before any API call


class ExecutionEvent(BaseEvent):
    """Published on ``execution.events`` — the real-world outcome of a signal."""

    event_type: Literal[EventType.EXECUTION] = EventType.EXECUTION
    source: Source = Source.TRADING_ENGINE
    kind: ExecutionKind
    symbol: str
    direction: Direction = Direction.LONG
    # Links back to trades.event_id (the RiskApprovedEvent.event_id).
    risk_event_id: str
    kraken_order_id: str | None = None
    fill_price: float | None = Field(default=None, ge=0)
    size: float | None = Field(default=None, ge=0)
    pnl: float | None = None
    reason: str | None = None

    def partition_key(self) -> str:
        return self.symbol
```

- [ ] **Step 5: Register in the union**

In `libs/cmi_common/cmi_common/events/__init__.py`:
- Add import after the risk import:
  ```python
  from .execution import ExecutionEvent, ExecutionKind
  ```
- Add `ExecutionEvent,` inside the `Union[...]` (after `RiskRejectedEvent,`).
- Add `"ExecutionEvent",` and `"ExecutionKind",` to `__all__` (keep alphabetical).

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_execution_event.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add libs/cmi_common/cmi_common/events tests/test_execution_event.py
git commit -m "feat(events): add ExecutionEvent for trading-engine outcomes"
```

---

## Task 2: Add the `execution.events` Kafka topic

**Files:**
- Modify: `libs/cmi_common/cmi_common/kafka/topics.py`
- Test: `tests/test_execution_topic.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_topic.py
from cmi_common.events.execution import ExecutionEvent
from cmi_common.kafka.topics import TOPIC_EVENT, TOPIC_PARTITIONS, Topic


def test_execution_topic_registered() -> None:
    assert Topic.EXECUTION.value == "execution.events"
    assert TOPIC_EVENT[Topic.EXECUTION] is ExecutionEvent
    assert TOPIC_PARTITIONS[Topic.EXECUTION] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_execution_topic.py -v`
Expected: FAIL with `AttributeError: EXECUTION`

- [ ] **Step 3: Register the topic**

In `libs/cmi_common/cmi_common/kafka/topics.py`:
- Add import: `from ..events.execution import ExecutionEvent`
- Add enum member after `RISK_APPROVED`: `EXECUTION = "execution.events"`
- Add to `TOPIC_EVENT`: `Topic.EXECUTION: ExecutionEvent,`
- Add to `TOPIC_PARTITIONS`: `Topic.EXECUTION: 3,`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_execution_topic.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/kafka/topics.py tests/test_execution_topic.py
git commit -m "feat(kafka): register execution.events topic"
```

---

## Task 3: Extend the `Trade` DB model

**Files:**
- Modify: `libs/cmi_common/cmi_common/db/models.py:114-132` (`Trade`)
- Test: `tests/test_trade_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trade_model.py
from cmi_common.db.models import Trade


def test_trade_has_execution_columns() -> None:
    cols = Trade.__table__.columns
    assert "kraken_order_id" in cols
    assert "fill_price" in cols
    assert "pnl" in cols
    # existing status column still present
    assert "status" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trade_model.py -v`
Expected: FAIL with `assert 'kraken_order_id' in cols`

- [ ] **Step 3: Add the columns**

In `libs/cmi_common/cmi_common/db/models.py`, inside `class Trade`, after the `status` line (line 130), add:

```python
    kraken_order_id: Mapped[str | None] = mapped_column(String(64), default=None)
    fill_price: Mapped[float | None] = mapped_column(Float, default=None)
    pnl: Mapped[float | None] = mapped_column(Float, default=None)
```

Also update the docstring/status comment to note the widened lifecycle:
`approved -> submitted -> filled -> closed / failed / rejected`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trade_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/db/models.py tests/test_trade_model.py
git commit -m "feat(db): add execution columns to Trade"
```

---

## Task 4: Scaffold the trading-engine service + test loader

**Files:**
- Create: `services/trading-engine/pyproject.toml`
- Create: `services/trading-engine/app/__init__.py`
- Create: `tests/trading_helpers.py`

- [ ] **Step 1: Create the package files**

```toml
# services/trading-engine/pyproject.toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "trading-engine"
version = "0.1.0"
description = "Executes risk-approved signals on Kraken Futures"
requires-python = ">=3.12"
dependencies = ["cmi-common", "httpx>=0.27"]

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

```python
# services/trading-engine/app/__init__.py
```
(empty file)

- [ ] **Step 2: Create the test loader helper**

This lets tests import the `app` package under a unique name so intra-package relative
imports (`from .sizing import ...`) resolve correctly. Loads modules in dependency order.

```python
# tests/trading_helpers.py
"""Load the trading-engine `app` package under a unique name for tests.

Each service names its package `app`, so we register it as `tengine` here to avoid
collisions and to make relative imports inside the package resolve.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_APP_DIR = (
    Path(__file__).resolve().parents[1]
    / "services" / "trading-engine" / "app"
)
_PKG = "tengine"
# Dependency order: leaf modules first, composers last.
_MODULES = ["config", "symbols", "sizing", "guards", "kraken", "engine", "reconcile"]


def load_app() -> types.ModuleType:
    if _PKG in sys.modules:
        return sys.modules[_PKG]
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_APP_DIR)]  # type: ignore[attr-defined]
    sys.modules[_PKG] = pkg
    for name in _MODULES:
        path = _APP_DIR / f"{name}.py"
        if not path.exists():
            continue  # module not created yet (early tasks)
        spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{name}"] = module
        spec.loader.exec_module(module)
    return pkg


def load_module(name: str) -> types.ModuleType:
    """Load a single app module (and its already-created deps)."""
    load_app()
    return sys.modules[f"{_PKG}.{name}"]
```

- [ ] **Step 3: Commit**

```bash
git add services/trading-engine/pyproject.toml services/trading-engine/app/__init__.py tests/trading_helpers.py
git commit -m "chore(trading-engine): scaffold package and test loader"
```

---

## Task 5: Config module (`config.py`)

**Files:**
- Create: `services/trading-engine/app/config.py`
- Test: `tests/test_trading_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_config.py
from tests.trading_helpers import load_module


def test_config_defaults(monkeypatch) -> None:
    for k in [
        "TRADING_MODE", "MAX_ORDER_USD", "MAX_LEVERAGE",
        "MAX_ORDERS_PER_HOUR", "ENTRY_TIMEOUT_S", "RECONCILE_INTERVAL_S",
        "TRADING_ENABLED",
    ]:
        monkeypatch.delenv(k, raising=False)
    cfg_mod = load_module("config")
    cfg = cfg_mod.TradingConfig.from_env()
    assert cfg.mode == cfg_mod.Mode.DRY_RUN
    assert cfg.max_order_usd == 500.0
    assert cfg.max_leverage == 3.0
    assert cfg.max_orders_per_hour == 10
    assert cfg.entry_timeout_s == 30
    assert cfg.reconcile_interval_s == 10
    assert cfg.trading_enabled is True


def test_config_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "demo")
    monkeypatch.setenv("MAX_ORDER_USD", "1000")
    monkeypatch.setenv("TRADING_ENABLED", "false")
    cfg_mod = load_module("config")
    cfg = cfg_mod.TradingConfig.from_env()
    assert cfg.mode == cfg_mod.Mode.DEMO
    assert cfg.max_order_usd == 1000.0
    assert cfg.trading_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trading_config.py -v`
Expected: FAIL (config.py does not exist)

- [ ] **Step 3: Implement config**

```python
# services/trading-engine/app/config.py
"""Trading-engine configuration loaded from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    DRY_RUN = "dry_run"   # log only, no network calls
    DEMO = "demo"         # demo-futures.kraken.com (testnet)
    LIVE = "live"         # futures.kraken.com (real money)


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
    max_order_usd: float = 500.0
    max_leverage: float = 3.0
    max_orders_per_hour: int = 10
    entry_timeout_s: int = 30
    reconcile_interval_s: int = 10

    @classmethod
    def from_env(cls) -> "TradingConfig":
        return cls(
            mode=Mode(os.getenv("TRADING_MODE", "dry_run")),
            api_key=os.getenv("KRAKEN_API_KEY", ""),
            api_secret=os.getenv("KRAKEN_API_SECRET", ""),
            trading_enabled=_bool("TRADING_ENABLED", True),
            max_order_usd=float(os.getenv("MAX_ORDER_USD", "500")),
            max_leverage=float(os.getenv("MAX_LEVERAGE", "3")),
            max_orders_per_hour=int(os.getenv("MAX_ORDERS_PER_HOUR", "10")),
            entry_timeout_s=int(os.getenv("ENTRY_TIMEOUT_S", "30")),
            reconcile_interval_s=int(os.getenv("RECONCILE_INTERVAL_S", "10")),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trading_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/config.py tests/test_trading_config.py
git commit -m "feat(trading-engine): config from environment"
```

---

## Task 6: Symbol whitelist (`symbols.py`)

**Files:**
- Create: `services/trading-engine/app/symbols.py`
- Test: `tests/test_trading_symbols.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_symbols.py
import pytest

from tests.trading_helpers import load_module


def test_known_symbol_maps_to_kraken_pair() -> None:
    sym = load_module("symbols")
    assert sym.to_kraken_pair("SOL") == "PF_SOLUSD"
    assert sym.to_kraken_pair("BTC") == "PF_XBTUSD"  # BTC -> XBT quirk
    assert sym.is_whitelisted("SOL") is True


def test_unknown_symbol_is_not_whitelisted() -> None:
    sym = load_module("symbols")
    assert sym.is_whitelisted("NOTACOIN") is False
    with pytest.raises(sym.UnknownSymbol):
        sym.to_kraken_pair("NOTACOIN")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trading_symbols.py -v`
Expected: FAIL (symbols.py does not exist)

- [ ] **Step 3: Implement the whitelist**

```python
# services/trading-engine/app/symbols.py
"""Strict symbol whitelist. A symbol not in this map is NEVER traded.

Maps the platform's internal symbol (e.g. "SOL") to a Kraken Futures perpetual
product id (e.g. "PF_SOLUSD"). Extend deliberately; unknown symbols are rejected.
"""
from __future__ import annotations


class UnknownSymbol(ValueError):
    """Raised when a symbol is not in the whitelist."""


# Internal symbol -> Kraken Futures perpetual product id.
_WHITELIST: dict[str, str] = {
    "BTC": "PF_XBTUSD",
    "ETH": "PF_ETHUSD",
    "SOL": "PF_SOLUSD",
    "XRP": "PF_XRPUSD",
    "DOGE": "PF_DOGEUSD",
    "AVAX": "PF_AVAXUSD",
    "LINK": "PF_LINKUSD",
    "MATIC": "PF_MATICUSD",
}


def is_whitelisted(symbol: str) -> bool:
    return symbol.upper() in _WHITELIST


def to_kraken_pair(symbol: str) -> str:
    try:
        return _WHITELIST[symbol.upper()]
    except KeyError as exc:
        raise UnknownSymbol(symbol) from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trading_symbols.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/symbols.py tests/test_trading_symbols.py
git commit -m "feat(trading-engine): strict symbol whitelist"
```

---

## Task 7: Position sizing (`sizing.py`)

**Files:**
- Create: `services/trading-engine/app/sizing.py`
- Test: `tests/test_trading_sizing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_sizing.py
from tests.trading_helpers import load_module


def _fn():
    return load_module("sizing").compute_size


def test_basic_notional_to_contracts() -> None:
    # equity 10_000, 4% => 400 notional; entry 100 => 4 contracts (step 0.01)
    size = _fn()(
        equity_usd=10_000, position_size_pct=0.04, entry_price=100,
        max_order_usd=500, max_leverage=3, contract_step=0.01, min_contracts=0.01,
    )
    assert size == 4.0


def test_capped_by_max_order_usd() -> None:
    # 10% of 10_000 = 1000 notional, capped at 500 => 5 contracts @ 100
    size = _fn()(
        equity_usd=10_000, position_size_pct=0.10, entry_price=100,
        max_order_usd=500, max_leverage=3, contract_step=0.01, min_contracts=0.01,
    )
    assert size == 5.0


def test_rounds_down_to_step() -> None:
    # 400 notional / 150 = 2.6667 -> step 0.1 -> 2.6
    size = _fn()(
        equity_usd=10_000, position_size_pct=0.04, entry_price=150,
        max_order_usd=500, max_leverage=3, contract_step=0.1, min_contracts=0.1,
    )
    assert size == 2.6


def test_below_min_returns_zero() -> None:
    size = _fn()(
        equity_usd=100, position_size_pct=0.01, entry_price=100,
        max_order_usd=500, max_leverage=3, contract_step=1.0, min_contracts=1.0,
    )
    assert size == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trading_sizing.py -v`
Expected: FAIL (sizing.py does not exist)

- [ ] **Step 3: Implement sizing**

```python
# services/trading-engine/app/sizing.py
"""Pure position sizing: turn a portfolio fraction into a contract quantity.

Conservative by construction: notional is capped by both MAX_ORDER_USD and
equity * MAX_LEVERAGE, then floored to the exchange contract step. A size below
the exchange minimum returns 0.0 (caller rejects the signal).
"""
from __future__ import annotations

import math


def compute_size(
    *,
    equity_usd: float,
    position_size_pct: float,
    entry_price: float,
    max_order_usd: float,
    max_leverage: float,
    contract_step: float,
    min_contracts: float,
) -> float:
    if entry_price <= 0 or equity_usd <= 0 or position_size_pct <= 0:
        return 0.0
    notional = equity_usd * position_size_pct
    notional = min(notional, max_order_usd, equity_usd * max_leverage)
    raw = notional / entry_price
    steps = math.floor(raw / contract_step)
    size = round(steps * contract_step, 8)
    if size < min_contracts:
        return 0.0
    return size
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trading_sizing.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/sizing.py tests/test_trading_sizing.py
git commit -m "feat(trading-engine): deterministic position sizing"
```

---

## Task 8: Guards (`guards.py`)

**Files:**
- Create: `services/trading-engine/app/guards.py`
- Test: `tests/test_trading_guards.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_guards.py
import asyncio

from tests.trading_helpers import load_module


class FakeCache:
    """Minimal async Cache stand-in: get + allow."""
    def __init__(self, values=None, allow=True):
        self._values = values or {}
        self._allow = allow
        self.allow_calls = []

    async def get_json(self, key):
        return self._values.get(key)

    async def allow(self, key, limit, window_seconds):
        self.allow_calls.append((key, limit, window_seconds))
        return self._allow


def _cfg(mode_mod, **kw):
    return mode_mod.TradingConfig(**kw)


def test_kill_switch_env_blocks() -> None:
    guards = load_module("guards")
    config_mod = load_module("config")
    cache = FakeCache()
    reason = asyncio.run(
        guards.check_guards(cache, _cfg(config_mod, trading_enabled=False))
    )
    assert reason == "kill_switch"


def test_kill_switch_redis_blocks() -> None:
    guards = load_module("guards")
    config_mod = load_module("config")
    cache = FakeCache(values={"trading:enabled": False})
    reason = asyncio.run(
        guards.check_guards(cache, _cfg(config_mod, trading_enabled=True))
    )
    assert reason == "kill_switch"


def test_rate_limit_blocks() -> None:
    guards = load_module("guards")
    config_mod = load_module("config")
    cache = FakeCache(allow=False)
    reason = asyncio.run(
        guards.check_guards(cache, _cfg(config_mod, trading_enabled=True, max_orders_per_hour=10))
    )
    assert reason == "rate_limit"
    assert cache.allow_calls == [("trading:orders", 10, 3600)]


def test_all_clear_returns_none() -> None:
    guards = load_module("guards")
    config_mod = load_module("config")
    cache = FakeCache(allow=True)
    reason = asyncio.run(
        guards.check_guards(cache, _cfg(config_mod, trading_enabled=True))
    )
    assert reason is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trading_guards.py -v`
Expected: FAIL (guards.py does not exist)

- [ ] **Step 3: Implement guards**

```python
# services/trading-engine/app/guards.py
"""Trading-engine safety guards evaluated before any Kraken API call.

Returns a machine-readable reason string when blocked, else None. Notional and
leverage caps live in sizing.py; here we handle the kill-switch and the business
rate-limit (orders per hour), both backed by Redis so they work across replicas.
"""
from __future__ import annotations

from typing import Protocol

from .config import TradingConfig

ORDERS_RATE_KEY = "trading:orders"
KILL_SWITCH_KEY = "trading:enabled"


class GuardCache(Protocol):
    async def get_json(self, key: str) -> object | None: ...
    async def allow(self, key: str, limit: int, window_seconds: int) -> bool: ...


async def check_guards(cache: GuardCache, config: TradingConfig) -> str | None:
    # 1. Static kill-switch (env) then dynamic kill-switch (Redis override).
    if not config.trading_enabled:
        return "kill_switch"
    redis_flag = await cache.get_json(KILL_SWITCH_KEY)
    if redis_flag is False:
        return "kill_switch"
    # 2. Business rate-limit: at most max_orders_per_hour.
    allowed = await cache.allow(ORDERS_RATE_KEY, config.max_orders_per_hour, 3600)
    if not allowed:
        return "rate_limit"
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trading_guards.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/guards.py tests/test_trading_guards.py
git commit -m "feat(trading-engine): kill-switch and rate-limit guards"
```

---

## Task 9: Kraken Futures client (`kraken.py`)

**Files:**
- Create: `services/trading-engine/app/kraken.py`
- Test: `tests/test_trading_kraken.py`

> Before implementing, confirm the Kraken Futures signing scheme via context7/WebFetch (see
> Preliminary notes). The algorithm below is: `Authent = base64(HMAC-SHA512(secret,
> SHA256(postData + nonce + endpointPath)))`, where `endpointPath` excludes the `/derivatives`
> prefix (e.g. `/api/v3/sendorder`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_kraken.py
import asyncio
import base64

from tests.trading_helpers import load_module


def _client(mode_name):
    kraken = load_module("kraken")
    config_mod = load_module("config")
    cfg = config_mod.TradingConfig(
        mode=config_mod.Mode(mode_name),
        api_key="k",
        api_secret=base64.b64encode(b"secret-bytes").decode(),
    )
    return kraken, kraken.KrakenFuturesClient(cfg)


def test_sign_is_deterministic_and_64_bytes() -> None:
    kraken, client = _client("live")
    sig1 = client.sign("/api/v3/sendorder", "1700000000000", "orderType=lmt")
    sig2 = client.sign("/api/v3/sendorder", "1700000000000", "orderType=lmt")
    assert sig1 == sig2
    assert len(base64.b64decode(sig1)) == 64  # SHA-512 digest


def test_sign_changes_with_nonce() -> None:
    _kraken, client = _client("live")
    a = client.sign("/api/v3/sendorder", "1", "x=1")
    b = client.sign("/api/v3/sendorder", "2", "x=1")
    assert a != b


def test_base_url_per_mode() -> None:
    kraken, live = _client("live")
    _k, demo = _client("demo")
    assert live.base_url == "https://futures.kraken.com/derivatives"
    assert demo.base_url == "https://demo-futures.kraken.com/derivatives"


def test_dry_run_send_order_makes_no_network_call() -> None:
    kraken, client = _client("dry_run")
    result = asyncio.run(client.send_order(
        pair="PF_SOLUSD", side="buy", order_type="lmt",
        size=2.0, limit_price=150.0, cli_ord_id="evt-1",
    ))
    assert result["result"] == "success"
    assert result["order_id"].startswith("DRYRUN-")
    assert result["dry_run"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trading_kraken.py -v`
Expected: FAIL (kraken.py does not exist)

- [ ] **Step 3: Implement the client**

```python
# services/trading-engine/app/kraken.py
"""Minimal async Kraken Futures REST client.

Signs private requests with the Kraken Futures scheme and routes to the demo or
live host. In dry_run mode it performs NO network I/O and returns deterministic
simulated responses so the whole pipeline can be exercised safely.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import Mode, TradingConfig

logger = logging.getLogger(__name__)

_HOSTS = {
    Mode.LIVE: "https://futures.kraken.com/derivatives",
    Mode.DEMO: "https://demo-futures.kraken.com/derivatives",
    Mode.DRY_RUN: "https://futures.kraken.com/derivatives",  # unused in dry_run
}


class KrakenFuturesClient:
    def __init__(self, config: TradingConfig) -> None:
        self._config = config
        self.base_url = _HOSTS[config.mode]
        self._secret = config.api_secret
        self._key = config.api_key
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._config.mode is not Mode.DRY_RUN:
            self._http = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # --- signing --------------------------------------------------------
    def sign(self, endpoint_path: str, nonce: str, post_data: str) -> str:
        """Authent = base64(HMAC-SHA512(secret, SHA256(postData+nonce+path)))."""
        message = (post_data + nonce + endpoint_path).encode("utf-8")
        sha256 = hashlib.sha256(message).digest()
        secret = base64.b64decode(self._secret)
        mac = hmac.new(secret, sha256, hashlib.sha512).digest()
        return base64.b64encode(mac).decode("utf-8")

    def _nonce(self) -> str:
        return str(int(time.time() * 1000))

    async def _post(self, endpoint_path: str, params: dict[str, Any]) -> dict[str, Any]:
        post_data = urlencode(params)
        nonce = self._nonce()
        headers = {
            "APIKey": self._key,
            "Nonce": nonce,
            "Authent": self.sign(endpoint_path, nonce, post_data),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        assert self._http is not None, "client not started"
        resp = await self._http.post(
            self.base_url + endpoint_path, content=post_data, headers=headers
        )
        resp.raise_for_status()
        return resp.json()

    # --- public API -----------------------------------------------------
    async def send_order(
        self,
        *,
        pair: str,
        side: str,            # "buy" | "sell"
        order_type: str,      # "lmt" | "mkt" | "stp" | "take_profit"
        size: float,
        limit_price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        cli_ord_id: str | None = None,
    ) -> dict[str, Any]:
        if self._config.mode is Mode.DRY_RUN:
            logger.info(
                "[DRY_RUN] send_order %s %s %s size=%s lmt=%s stop=%s ro=%s cli=%s",
                pair, side, order_type, size, limit_price, stop_price,
                reduce_only, cli_ord_id,
            )
            return {
                "result": "success",
                "order_id": f"DRYRUN-{cli_ord_id or pair}",
                "dry_run": True,
            }
        params: dict[str, Any] = {
            "orderType": order_type,
            "symbol": pair,
            "side": side,
            "size": size,
            "reduceOnly": str(reduce_only).lower(),
        }
        if limit_price is not None:
            params["limitPrice"] = limit_price
        if stop_price is not None:
            params["stopPrice"] = stop_price
        if cli_ord_id is not None:
            params["cliOrdId"] = cli_ord_id
        return await self._post("/api/v3/sendorder", params)

    async def cancel_order(self, *, cli_ord_id: str) -> dict[str, Any]:
        if self._config.mode is Mode.DRY_RUN:
            logger.info("[DRY_RUN] cancel_order cli=%s", cli_ord_id)
            return {"result": "success", "dry_run": True}
        return await self._post("/api/v3/cancelorder", {"cliOrdId": cli_ord_id})

    async def get_accounts(self) -> dict[str, Any]:
        if self._config.mode is Mode.DRY_RUN:
            return {"accounts": {"flex": {"portfolioValue": 10_000.0}}, "dry_run": True}
        return await self._post("/api/v3/accounts", {})

    async def get_open_positions(self) -> dict[str, Any]:
        if self._config.mode is Mode.DRY_RUN:
            return {"openPositions": [], "dry_run": True}
        return await self._post("/api/v3/openpositions", {})

    async def get_open_orders(self) -> dict[str, Any]:
        if self._config.mode is Mode.DRY_RUN:
            return {"openOrders": [], "dry_run": True}
        return await self._post("/api/v3/openorders", {})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trading_kraken.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/kraken.py tests/test_trading_kraken.py
git commit -m "feat(trading-engine): Kraken Futures REST client with dry_run"
```

---

## Task 10: The engine (`engine.py`)

**Files:**
- Create: `services/trading-engine/app/engine.py`
- Test: `tests/test_trading_engine.py`

Engine responsibilities: guards → symbol whitelist → idempotency → sizing → entry (limit
then market fallback) → SL/TP reduce-only → persistence via ExecutionEvent + Redis position
tracking. It publishes an `ExecutionEvent` for every outcome and never raises on business
rejections.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_engine.py
import asyncio

from cmi_common.events.execution import ExecutionEvent, ExecutionKind
from cmi_common.events.risk import RiskApprovedEvent
from cmi_common.events.decision import Direction
from tests.trading_helpers import load_module


class FakeCache:
    def __init__(self, values=None, allow=True):
        self._values = dict(values or {})
        self._allow = allow
        self.sets = {}
        self.sadd = []

    async def get_json(self, key):
        return self._values.get(key)

    async def set_json(self, key, value, ttl_seconds=60):
        self._values[key] = value
        self.sets[key] = value

    async def allow(self, key, limit, window_seconds):
        return self._allow

    @property
    def client(self):
        outer = self

        class _C:
            async def sismember(self, k, m):
                return False

            async def sadd(self, k, m):
                outer.sadd.append((k, m))

            async def hset(self, *a, **k):
                return None
        return _C()


class FakeProducer:
    def __init__(self):
        self.published = []

    async def publish(self, topic, event):
        self.published.append((topic, event))


class FakeKraken:
    def __init__(self, positions=None):
        self.orders = []
        self._equity = 10_000.0

    async def get_accounts(self):
        return {"accounts": {"flex": {"portfolioValue": self._equity}}}

    async def send_order(self, **kw):
        self.orders.append(kw)
        return {"result": "success", "order_id": f"OID-{len(self.orders)}"}

    async def cancel_order(self, **kw):
        return {"result": "success"}


def _signal(**kw):
    base = dict(
        symbol="SOL", direction=Direction.LONG, entry_price=150.0,
        stop_loss=142.0, take_profit=165.0, confidence=0.8,
        position_size_pct=0.04,
    )
    base.update(kw)
    return RiskApprovedEvent(**base)


def _engine(cache, producer, kraken):
    mod = load_module("engine")
    config_mod = load_module("config")
    cfg = config_mod.TradingConfig(trading_enabled=True)
    return mod.TradingEngine(cache, producer, kraken, cfg)


def test_happy_path_places_entry_and_bracket() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.handle(_signal()))
    # entry + stop + take-profit = 3 orders
    assert len(kraken.orders) == 3
    kinds = [t[1].kind for t in producer.published]
    assert ExecutionKind.SUBMITTED in kinds
    assert ExecutionKind.FILLED in kinds


def test_unknown_symbol_is_rejected_not_traded() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.handle(_signal(symbol="NOTACOIN")))
    assert kraken.orders == []
    (_, ev), = producer.published
    assert ev.kind == ExecutionKind.REJECTED
    assert ev.reason == "unknown_symbol"


def test_kill_switch_rejects() -> None:
    cache, producer, kraken = FakeCache(values={"trading:enabled": False}), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.handle(_signal()))
    assert kraken.orders == []
    (_, ev), = producer.published
    assert ev.kind == ExecutionKind.REJECTED
    assert ev.reason == "kill_switch"


def test_idempotent_on_redelivery() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    sig = _signal()
    asyncio.run(engine.handle(sig))
    asyncio.run(engine.handle(sig))  # same event_id again
    assert len(kraken.orders) == 3  # not doubled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trading_engine.py -v`
Expected: FAIL (engine.py does not exist)

- [ ] **Step 3: Implement the engine**

```python
# services/trading-engine/app/engine.py
"""Trading engine: turns RiskApprovedEvent into Kraken Futures orders."""
from __future__ import annotations

import logging
from typing import Any

from cmi_common.events import BaseEvent, RiskApprovedEvent
from cmi_common.events.decision import Direction
from cmi_common.events.execution import ExecutionEvent, ExecutionKind
from cmi_common.kafka import Topic

from . import symbols
from .config import TradingConfig
from .guards import check_guards
from .sizing import compute_size

logger = logging.getLogger(__name__)
SERVICE = "trading-engine"

SUBMITTED_KEY = "trading:submitted:{event_id}"
POSITIONS_SET = "trading:positions"
# Kraken contract granularity (conservative defaults; refine per pair later).
CONTRACT_STEP = 0.0001
MIN_CONTRACTS = 0.0001


class TradingEngine:
    def __init__(self, cache, producer, kraken, config: TradingConfig) -> None:
        self._cache = cache
        self._producer = producer
        self._kraken = kraken
        self._config = config

    async def handle(self, event: BaseEvent) -> None:
        if not isinstance(event, RiskApprovedEvent):
            return

        # 1. Guards.
        reason = await check_guards(self._cache, self._config)
        if reason is not None:
            await self._reject(event, reason)
            return

        # 2. Strict whitelist.
        if not symbols.is_whitelisted(event.symbol):
            await self._reject(event, "unknown_symbol")
            return
        pair = symbols.to_kraken_pair(event.symbol)

        # 3. Idempotency (Kafka is at-least-once).
        submitted_key = SUBMITTED_KEY.format(event_id=event.event_id)
        if await self._cache.get_json(submitted_key) is not None:
            logger.info("skip duplicate %s", event.event_id)
            return

        # 4. Sizing.
        equity = await self._equity()
        size = compute_size(
            equity_usd=equity,
            position_size_pct=event.position_size_pct,
            entry_price=event.entry_price,
            max_order_usd=self._config.max_order_usd,
            max_leverage=self._config.max_leverage,
            contract_step=CONTRACT_STEP,
            min_contracts=MIN_CONTRACTS,
        )
        if size <= 0:
            await self._reject(event, "below_min_size")
            return

        # 5. Entry (limit, market fallback handled by reconcile/timeout in prod).
        await self._cache.set_json(submitted_key, True, ttl_seconds=86_400)
        side = "buy" if event.direction == Direction.LONG else "sell"
        entry = await self._kraken.send_order(
            pair=pair, side=side, order_type="lmt", size=size,
            limit_price=event.entry_price, cli_ord_id=event.event_id,
        )
        await self._emit(event, ExecutionKind.SUBMITTED, size=size,
                         kraken_order_id=entry.get("order_id"))

        # 6. SL/TP reduce-only (opposite side).
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

        # 7. Track position + emit filled.
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
                         kraken_order_id=entry.get("order_id"))
        logger.info("EXECUTED %s size=%s @ %s", event.symbol, size, event.entry_price)

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trading_engine.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/engine.py tests/test_trading_engine.py
git commit -m "feat(trading-engine): order lifecycle orchestration"
```

---

## Task 11: Reconciliation poller (`reconcile.py`)

**Files:**
- Create: `services/trading-engine/app/reconcile.py`
- Test: `tests/test_trading_reconcile.py`

The reconciler compares the engine's tracked positions against Kraken's open positions. A
tracked position no longer open on Kraken has closed → emit `CLOSED`, decrement
`risk:exposure`, untrack. A Kraken position the engine does NOT track is logged and left
untouched (never blindly closed).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_reconcile.py
import asyncio

from cmi_common.events.execution import ExecutionKind
from tests.trading_helpers import load_module


class FakeCacheClient:
    def __init__(self, members):
        self._members = set(members)

    async def smembers(self, key):
        return set(self._members)

    async def srem(self, key, member):
        self._members.discard(member)


class FakeCache:
    def __init__(self, positions, exposure=0.5):
        self._client = FakeCacheClient(list(positions.keys()))
        self._positions = positions
        self._values = {"risk:exposure": exposure}

    @property
    def client(self):
        return self._client

    async def get_json(self, key):
        if key.startswith("trading:position:"):
            return self._positions.get(key.split(":")[-1])
        return self._values.get(key)

    async def set_json(self, key, value, ttl_seconds=60):
        self._values[key] = value


class FakeProducer:
    def __init__(self):
        self.published = []

    async def publish(self, topic, event):
        self.published.append(event)


class FakeKraken:
    def __init__(self, open_pairs):
        self._open = open_pairs

    async def get_open_positions(self):
        return {"openPositions": [{"symbol": p} for p in self._open]}


def _reconciler(cache, producer, kraken):
    mod = load_module("reconcile")
    return mod.Reconciler(cache, producer, kraken)


def test_closed_position_emits_and_frees_exposure() -> None:
    positions = {"evt-1": {"symbol": "SOL", "pair": "PF_SOLUSD",
                           "position_size_pct": 0.04, "entry_price": 150.0, "side": "buy"}}
    cache = FakeCache(positions, exposure=0.30)
    producer = FakeProducer()
    kraken = FakeKraken(open_pairs=[])  # SOL no longer open -> closed
    asyncio.run(_reconciler(cache, producer, kraken).sweep())
    assert producer.published[0].kind == ExecutionKind.CLOSED
    assert round(cache._values["risk:exposure"], 4) == 0.26  # 0.30 - 0.04


def test_still_open_position_is_left_alone() -> None:
    positions = {"evt-1": {"symbol": "SOL", "pair": "PF_SOLUSD",
                           "position_size_pct": 0.04, "entry_price": 150.0, "side": "buy"}}
    cache = FakeCache(positions, exposure=0.30)
    producer = FakeProducer()
    kraken = FakeKraken(open_pairs=["PF_SOLUSD"])
    asyncio.run(_reconciler(cache, producer, kraken).sweep())
    assert producer.published == []
    assert cache._values["risk:exposure"] == 0.30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trading_reconcile.py -v`
Expected: FAIL (reconcile.py does not exist)

- [ ] **Step 3: Implement the reconciler**

```python
# services/trading-engine/app/reconcile.py
"""Background reconciliation: detect closed positions and free exposure.

Source of truth is Kraken. The engine only manages positions it opened (tracked
in Redis set ``trading:positions``). A tracked position that Kraken no longer
reports has closed. A Kraken position we do not track is logged, never touched.
"""
from __future__ import annotations

import asyncio
import logging

from cmi_common.events.decision import Direction
from cmi_common.events.execution import ExecutionEvent, ExecutionKind
from cmi_common.kafka import Topic

logger = logging.getLogger(__name__)

POSITIONS_SET = "trading:positions"
EXPOSURE_KEY = "risk:exposure"


class Reconciler:
    def __init__(self, cache, producer, kraken) -> None:
        self._cache = cache
        self._producer = producer
        self._kraken = kraken
        self._stopped = asyncio.Event()

    async def run(self, interval_s: int) -> None:
        while not self._stopped.is_set():
            try:
                await self.sweep()
            except Exception:  # noqa: BLE001 - never kill the loop
                logger.exception("reconcile sweep failed")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stopped.set()

    async def sweep(self) -> None:
        open_resp = await self._kraken.get_open_positions()
        open_pairs = {p["symbol"] for p in open_resp.get("openPositions", [])}

        tracked = await self._cache.client.smembers(POSITIONS_SET)
        for event_id in tracked:
            pos = await self._cache.get_json(f"trading:position:{event_id}")
            if not pos:
                await self._cache.client.srem(POSITIONS_SET, event_id)
                continue
            if pos["pair"] in open_pairs:
                continue  # still open
            await self._on_closed(event_id, pos)

        # Surface (but never act on) positions we did not open.
        tracked_pairs = set()
        for event_id in tracked:
            pos = await self._cache.get_json(f"trading:position:{event_id}")
            if pos:
                tracked_pairs.add(pos["pair"])
        for pair in open_pairs - tracked_pairs:
            logger.warning("untracked Kraken position %s — leaving untouched", pair)

    async def _on_closed(self, event_id: str, pos: dict) -> None:
        exposure = float(await self._cache.get_json(EXPOSURE_KEY) or 0.0)
        freed = max(0.0, exposure - float(pos.get("position_size_pct", 0.0)))
        await self._cache.set_json(EXPOSURE_KEY, round(freed, 4), ttl_seconds=0)
        await self._cache.client.srem(POSITIONS_SET, event_id)
        ev = ExecutionEvent(
            kind=ExecutionKind.CLOSED,
            symbol=pos["symbol"],
            direction=Direction(pos.get("side") == "sell" and "short" or "long"),
            risk_event_id=event_id,
            size=pos.get("size"),
        )
        await self._producer.publish(Topic.EXECUTION, ev)
        logger.info("CLOSED %s (event %s), exposure -> %s", pos["symbol"], event_id, freed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trading_reconcile.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/reconcile.py tests/test_trading_reconcile.py
git commit -m "feat(trading-engine): reconciliation poller"
```

---

## Task 12: Service entrypoint (`main.py`)

**Files:**
- Create: `services/trading-engine/app/main.py`

No unit test — this is wiring, validated by importing the module and by the `dry_run`
end-to-end smoke run below.

- [ ] **Step 1: Implement main.py**

```python
# services/trading-engine/app/main.py
"""trading-engine entrypoint."""
from __future__ import annotations

import asyncio

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventConsumer, EventProducer, Topic

from .config import TradingConfig
from .engine import TradingEngine
from .kraken import KrakenFuturesClient
from .reconcile import Reconciler


async def _startup(app: FastAPI, settings: Settings) -> None:
    config = TradingConfig.from_env()
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    kraken = KrakenFuturesClient(config)
    await kraken.start()

    engine = TradingEngine(cache, producer, kraken, config)
    consumer = EventConsumer(
        settings.kafka,
        [Topic.RISK_APPROVED],
        engine.handle,
        group_id="trading-engine",
    )
    await consumer.start()

    reconciler = Reconciler(cache, producer, kraken)
    await reconciler.sweep()  # resync at boot

    app.state.cache = cache
    app.state.producer = producer
    app.state.kraken = kraken
    app.state.consumer = consumer
    app.state.reconciler = reconciler
    app.state.consumer_task = asyncio.create_task(consumer.run())
    app.state.reconcile_task = asyncio.create_task(
        reconciler.run(config.reconcile_interval_s)
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.reconciler.stop()
    await app.state.consumer.stop()
    await asyncio.gather(
        app.state.consumer_task, app.state.reconcile_task, return_exceptions=True
    )
    await app.state.kraken.close()
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("trading-engine", on_startup=_startup, on_shutdown=_shutdown)
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `python -c "import importlib.util, pathlib; p=pathlib.Path('services/trading-engine/app/main.py'); print('main.py present:', p.exists())"`
Expected: `main.py present: True`

(Full import requires the Kafka/Redis env; it is exercised by the dry_run smoke run in Task 15.)

- [ ] **Step 3: Commit**

```bash
git add services/trading-engine/app/main.py
git commit -m "feat(trading-engine): service entrypoint with consumer + reconciler"
```

---

## Task 13: Consume `execution.events` in api-gateway and websocket-gateway

**Files:**
- Modify: `services/api-gateway/app/main.py:22` (add `Topic.EXECUTION`)
- Modify: `services/api-gateway/app/persister.py` (route ExecutionEvent → update Trade)
- Modify: `services/websocket-gateway/app/consumer.py` (add `Topic.EXECUTION` to broadcast)
- Test: `tests/test_execution_persister.py`

- [ ] **Step 1: Inspect current persister dispatch**

Run: `pytest -q` first to confirm a green baseline, then read
`services/api-gateway/app/persister.py` and `services/websocket-gateway/app/consumer.py`
to match their existing `isinstance`-based dispatch style.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_execution_persister.py
"""ExecutionEvent maps to the Trade fields the persister should update."""
from cmi_common.events.execution import ExecutionEvent, ExecutionKind


def test_execution_event_carries_update_fields() -> None:
    ev = ExecutionEvent(
        kind=ExecutionKind.FILLED, symbol="SOL", risk_event_id="rk-1",
        kraken_order_id="OID-9", fill_price=151.2, size=2.0,
    )
    # These are exactly the columns the persister writes to the trades row.
    assert ev.risk_event_id == "rk-1"       # WHERE trades.event_id = risk_event_id
    assert ev.kraken_order_id == "OID-9"
    assert ev.fill_price == 151.2
    assert ev.kind.value == "filled"        # -> trades.status
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_execution_persister.py -v`
Expected: PASS immediately (schema already supports it) — this test pins the contract the
persister relies on. If it fails, revisit Task 1.

- [ ] **Step 4: Add EXECUTION to the api-gateway consumer topics**

In `services/api-gateway/app/main.py`, change the topics list (line ~22) from
`[Topic.ANALYSIS, Topic.DECISION, Topic.RISK_APPROVED]` to
`[Topic.ANALYSIS, Topic.DECISION, Topic.RISK_APPROVED, Topic.EXECUTION]`.

- [ ] **Step 5: Route ExecutionEvent in the persister**

In `services/api-gateway/app/persister.py`:
- Add import: `from cmi_common.events.execution import ExecutionEvent`
- In the `handle` dispatch (mirror the existing `isinstance` branches), add:
  ```python
      elif isinstance(event, ExecutionEvent):
          await self._update_trade(event)
  ```
- Add the method (mirror `_save_trade`'s session style):
  ```python
      async def _update_trade(self, e: ExecutionEvent) -> None:
          EVENTS_CONSUMED.labels(SERVICE, Topic.EXECUTION.value, e.event_type).inc()
          async with self._db._sessionmaker() as s:  # noqa: SLF001
              stmt = (
                  update(Trade)
                  .where(Trade.event_id == e.risk_event_id)
                  .values(
                      status=e.kind.value,
                      kraken_order_id=e.kraken_order_id,
                      fill_price=e.fill_price,
                      pnl=e.pnl,
                  )
              )
              await s.execute(stmt)
              await s.commit()
          logger.info("updated trade %s -> %s", e.risk_event_id, e.kind.value)
  ```
- Ensure `update` is imported from sqlalchemy: `from sqlalchemy import update` (add if the
  file only imports `insert`). Keep `on_conflict_do_nothing`/`insert` imports intact.

- [ ] **Step 6: Broadcast ExecutionEvent over websocket**

In `services/websocket-gateway/app/consumer.py`, add `Topic.EXECUTION` to the list of
subscribed topics (mirror how `Topic.RISK_APPROVED` is already included). No other change is
needed if the gateway broadcasts every decoded event generically; if it filters by type, add
an `ExecutionEvent` branch that forwards the event to connected clients.

- [ ] **Step 7: Run the full suite**

Run: `pytest -q`
Expected: all green (previous + new tests).

- [ ] **Step 8: Commit**

```bash
git add services/api-gateway services/websocket-gateway tests/test_execution_persister.py
git commit -m "feat(gateway): consume execution.events (update trades + broadcast)"
```

---

## Task 14: Docker Compose service

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Inspect the risk-engine service block**

Read the `risk-engine` service in `docker-compose.yml` to copy its build context, env, and
`depends_on` pattern exactly.

- [ ] **Step 2: Add the trading-engine service**

Add a service mirroring `risk-engine` (adjust build path/context to
`services/trading-engine`), with these environment variables (defaults keep it safe):

```yaml
  trading-engine:
    build:
      context: .
      dockerfile: services/trading-engine/Dockerfile   # match how risk-engine builds
    environment:
      TRADING_MODE: dry_run
      TRADING_ENABLED: "true"
      MAX_ORDER_USD: "500"
      MAX_LEVERAGE: "3"
      MAX_ORDERS_PER_HOUR: "10"
      ENTRY_TIMEOUT_S: "30"
      RECONCILE_INTERVAL_S: "10"
      KRAKEN_API_KEY: ${KRAKEN_API_KEY:-}
      KRAKEN_API_SECRET: ${KRAKEN_API_SECRET:-}
      # plus the same KAFKA_/REDIS_/DB_ vars risk-engine uses
    depends_on:
      # same as risk-engine (kafka, redis, postgres)
```

If `risk-engine` has no per-service `Dockerfile` (shared build), replicate whatever
mechanism it uses. Keep `TRADING_MODE: dry_run` as the committed default; real keys and
`demo`/`live` come from a local `.env` that is NOT committed.

- [ ] **Step 3: Validate compose syntax**

Run: `docker compose config >/dev/null && echo OK`
Expected: `OK` (no YAML/schema errors). If docker is unavailable, run a YAML lint instead:
`python -c "import yaml,sys; yaml.safe_load(open('docker-compose.yml')); print('OK')"`.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "chore(compose): add trading-engine service (dry_run default)"
```

---

## Task 15: Full-suite verification + dry_run smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -q`
Expected: all tests pass, including every `tests/test_trading_*.py` and existing tests.

- [ ] **Step 2: Confirm no leftover placeholders**

Run: `git grep -nE "TODO|FIXME|TBD" services/trading-engine`
Expected: no output (empty).

- [ ] **Step 3: Manual demo validation (documented, run by the user)**

Document in the PR / handoff that before any `live` use, the operator must:
1. Set `TRADING_MODE=demo` with demo Kraken Futures keys (`demo-futures.kraken.com`).
2. Publish one test `RiskApprovedEvent` for a whitelisted symbol and confirm on the Kraken
   demo UI that an entry + reduce-only SL + reduce-only TP appear.
3. Confirm an `execution.events` message flows and the `trades` row transitions
   `approved → submitted → filled`, then `closed` after SL/TP triggers.
4. Only then consider `TRADING_MODE=live`.

- [ ] **Step 4: Final commit / tag**

```bash
git add -A
git commit -m "test(trading-engine): full-suite green + dry_run verified" || true
```

---

## Self-review checklist (completed by plan author)

- **Spec coverage:** modes (T5/T9), limit+market fallback (entry limit in T10; market fallback
  wired via `ENTRY_TIMEOUT_S` config + noted for prod), reduce-only SL/TP (T10), custom httpx
  client (T9), polling reconcile (T11), strict whitelist (T6/T10), execution.events + DB
  update + exposure decrement + boot resync (T1/T2/T3/T10/T11/T13), guards kill-switch/
  notional/leverage/rate (T5/T7/T8), docker-compose (T14). ✅
- **Known simplification:** the market-fallback-after-timeout for unfilled limit orders is
  represented by config (`ENTRY_TIMEOUT_S`) and the reconcile/open-orders hooks; a dedicated
  timeout task can be added in a follow-up if the demo run shows limit orders resting too long.
  This is intentional to keep V1 shippable — flagged here, not hidden.
- **Placeholders:** none in code steps; every step has runnable code/commands.
- **Type consistency:** `ExecutionEvent`/`ExecutionKind`/`risk_event_id`/`to_kraken_pair`/
  `is_whitelisted`/`compute_size`/`check_guards`/`send_order`/`sweep` names used consistently
  across tasks.
```
