# Read Endpoints — Increment 1 (Spine + Tier-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make api-gateway the JWT-protected unified read API and serve the six Tier-1 read endpoints (`/portfolio/positions`, `/portfolio/trades`, `/market/decisions`, `/market/signals`, `/risk/limits`, `/risk/exposure`) from data that already exists (DB + Redis), so the terminal's positions/trades/decisions/signals/risk-limits screens work live.

**Architecture:** api-gateway gains a Redis `Cache` and a shared `StateReader` (extracted to `cmi_common`) alongside its existing DB access, and a shared `require_principal` JWT dependency. Response mapping lives in pure functions (`app/mappers.py`) that convert Redis dicts / ORM rows into the frontend `domain.ts` shapes; routers stay thin. Pure mappers are unit-tested without DB/Redis.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, aiokafka (unchanged), Redis (aioredis via `cmi_common.cache.Cache`), pytest.

**Scope note:** This is Increment 1 of the design `docs/superpowers/specs/2026-07-22-api-gateway-read-endpoints-design.md`. Increments 2 (market persistence), 3 (portfolio aggregate + history), 4 (risk alerts) are separate plans written after this ships. Fields that depend on later increments (portfolio equity, live prices) get documented defaults here.

---

## Reference: source shapes (already verified in the codebase)

**Redis position payload** (`trading:position:{event_id}`, written by trading-engine `engine.py`):
```json
{ "symbol": "SOL", "pair": "PF_SOLUSD", "side": "buy", "size": 1.0, "entry_price": 140.0, "position_size_pct": 0.1 }
```
After Task 3 it also carries `"stop_loss"`, `"take_profit"`, `"opened_at"`. `StateReader.positions()` injects `"event_id"`.

**DB `Trade`** columns: `event_id, symbol, direction, entry_price, stop_loss, take_profit, confidence, position_size_pct, risk_reward_ratio, status, kraken_order_id, fill_price, pnl, created_at`.

**DB `Decision`** columns: `event_id, symbol, direction, opportunity_score, confidence, ai_validated, rationale, created_at, payload`.

**DB `Signal`** columns: `time, symbol, event_id, opportunity_score, confidence, reason, escalated, payload`.

**Redis `trading:runtime`**: `{ mode, trading_enabled, auto_trading_enabled, max_order_usd, max_leverage, max_orders_per_hour, entry_timeout_s, reconcile_interval_s }`.

**Frontend target types**: `frontend/src/lib/types/domain.ts` (`Position`, `Trade`, `WorkerDecision`, `RiskLimit`, `RiskExposure`, `AssetExposure`).

---

## Task 1: Extract `StateReader` into `cmi_common`

**Files:**
- Create: `libs/cmi_common/cmi_common/state.py`
- Modify: `services/control-api/app/state.py` (re-export from shared module)
- Test: `tests/test_shared_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shared_state.py
import asyncio


class FakeRedis:
    def __init__(self, members): self._members = members
    async def smembers(self, key): return set(self._members)


class FakeCache:
    def __init__(self, values, members):
        self._values = values
        self.client = FakeRedis(members)
    async def get_json(self, key): return self._values.get(key)


def test_positions_injects_event_id():
    from cmi_common.state import StateReader
    cache = FakeCache(
        values={"trading:position:e1": {"symbol": "SOL", "side": "buy", "size": 1.0, "entry_price": 140.0}},
        members=["e1"],
    )
    reader = StateReader(cache, db=None)
    out = asyncio.run(reader.positions())
    assert out == [{"event_id": "e1", "symbol": "SOL", "side": "buy", "size": 1.0, "entry_price": 140.0}]


def test_settings_defaults_empty():
    from cmi_common.state import StateReader
    reader = StateReader(FakeCache(values={}, members=[]), db=None)
    assert asyncio.run(reader.settings()) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_shared_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmi_common.state'`

- [ ] **Step 3: Create the shared module**

```python
# libs/cmi_common/cmi_common/state.py
"""Read-only platform state: runtime settings + live positions/pending from
Redis, trades from the DB. Shared by control-api (write plane) and api-gateway
(read plane) so the Redis key layout lives in exactly one place."""
from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select

RUNTIME_KEY = "trading:runtime"
POSITIONS_SET = "trading:positions"
PENDING_SET = "trading:pending"


class StateReader:
    def __init__(self, cache, *, db) -> None:
        self._cache = cache
        self._db = db

    async def settings(self) -> dict[str, Any]:
        return (await self._cache.get_json(RUNTIME_KEY)) or {}

    async def positions(self) -> list[dict[str, Any]]:
        ids = await self._cache.client.smembers(POSITIONS_SET)
        out = []
        for event_id in ids:
            pos = await self._cache.get_json(f"trading:position:{event_id}")
            if pos:
                out.append({"event_id": event_id, **pos})
        return out

    async def pending(self) -> list[dict[str, Any]]:
        ids = await self._cache.client.smembers(PENDING_SET)
        out = []
        for event_id in ids:
            sig = await self._cache.get_json(f"trading:pending:{event_id}")
            if sig:
                out.append({"event_id": event_id, **sig})
        return out

    async def trades(self, limit: int = 50) -> list[dict[str, Any]]:
        from cmi_common.db import Trade
        async with self._db.session() as s:
            rows = (await s.execute(
                select(Trade).order_by(desc(Trade.created_at)).limit(limit)
            )).scalars().all()
            return [
                {"symbol": r.symbol, "status": r.status, "entry_price": r.entry_price,
                 "fill_price": r.fill_price, "pnl": r.pnl, "created_at": r.created_at}
                for r in rows
            ]
```

- [ ] **Step 4: Point control-api at the shared module**

Replace the entire body of `services/control-api/app/state.py` with a re-export so existing imports (`from .state import StateReader`) keep working:

```python
# services/control-api/app/state.py
"""Re-export the shared StateReader (moved to cmi_common.state)."""
from __future__ import annotations

from cmi_common.state import (  # noqa: F401
    PENDING_SET,
    POSITIONS_SET,
    RUNTIME_KEY,
    StateReader,
)
```

- [ ] **Step 5: Run tests to verify green (new + existing control-api state usage)**

Run: `python -m pytest tests/test_shared_state.py tests/test_trading_control.py tests/test_control_api_auth.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add libs/cmi_common/cmi_common/state.py services/control-api/app/state.py tests/test_shared_state.py
git commit -m "refactor(cmi_common): extract shared StateReader for read/write planes"
```

---

## Task 2: Move `require_principal` JWT dependency into `cmi_common`

**Files:**
- Modify: `libs/cmi_common/cmi_common/auth.py` (append the FastAPI dependency)
- Modify: `services/control-api/app/auth_dep.py` (re-export)
- Test: `tests/test_shared_auth.py` (extend)

`cmi_common` already depends on FastAPI (it exports `create_app`), so the dependency belongs there.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_shared_auth.py
import asyncio

import pytest


def test_require_principal_rejects_bad_token(monkeypatch):
    from fastapi import HTTPException
    from cmi_common.auth import require_principal
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_principal(authorization="Bearer not.a.jwt"))
    assert exc.value.status_code == 401


def test_require_principal_accepts_valid_token(monkeypatch):
    from cmi_common.auth import encode_token, require_principal
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    tok = encode_token({"sub": "admin", "role": "admin"}, secret="s3cret")
    principal = asyncio.run(require_principal(authorization=f"Bearer {tok}"))
    assert principal.sub == "admin" and principal.role == "admin"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_shared_auth.py -k require_principal -v`
Expected: FAIL with `ImportError: cannot import name 'require_principal'`

- [ ] **Step 3: Append the dependency to `cmi_common/auth.py`**

```python
# append to libs/cmi_common/cmi_common/auth.py
from fastapi import Header, HTTPException


async def require_principal(authorization: str | None = Header(default=None)) -> Principal:
    """FastAPI dependency: enforce a JWT bearer token (lenient in dev when no
    JWT_SECRET is set, matching decode_token)."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    try:
        return decode_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
```

- [ ] **Step 4: Point control-api at the shared dependency**

Replace the body of `services/control-api/app/auth_dep.py`:

```python
# services/control-api/app/auth_dep.py
"""Re-export the shared JWT dependency (moved to cmi_common.auth)."""
from __future__ import annotations

from cmi_common.auth import require_principal  # noqa: F401
```

- [ ] **Step 5: Run tests to verify green**

Run: `python -m pytest tests/test_shared_auth.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add libs/cmi_common/cmi_common/auth.py services/control-api/app/auth_dep.py tests/test_shared_auth.py
git commit -m "refactor(cmi_common): share require_principal JWT dependency"
```

---

## Task 3: Enrich the trading-engine position payload (stop_loss / take_profit / opened_at)

So `/portfolio/positions` and `/risk/exposure` report protection honestly.

**Files:**
- Modify: `services/trading-engine/app/engine.py` (the `trading:position:{event_id}` set_json call, currently around lines 132-140)
- Test: `tests/test_trading_position_payload.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_position_payload.py
from tests.trading_helpers import load_module


def test_position_payload_includes_protection_fields():
    engine_mod = load_module("engine")
    build = engine_mod.build_position_payload  # pure helper added in Step 3
    payload = build(
        symbol="SOL", pair="PF_SOLUSD", side="buy", size=1.0,
        entry_price=140.0, position_size_pct=0.1,
        stop_loss=130.0, take_profit=160.0, opened_at="2026-07-22T10:00:00Z",
    )
    assert payload["stop_loss"] == 130.0
    assert payload["take_profit"] == 160.0
    assert payload["opened_at"] == "2026-07-22T10:00:00Z"
    assert payload["symbol"] == "SOL" and payload["size"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading_position_payload.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'build_position_payload'`

- [ ] **Step 3: Extract a pure payload builder and use it**

Add near the top of `services/trading-engine/app/engine.py` (module level):

```python
def build_position_payload(
    *, symbol, pair, side, size, entry_price, position_size_pct,
    stop_loss=None, take_profit=None, opened_at=None,
) -> dict:
    """Canonical Redis payload for a tracked position."""
    return {
        "symbol": symbol, "pair": pair, "side": side, "size": size,
        "entry_price": entry_price, "position_size_pct": position_size_pct,
        "stop_loss": stop_loss, "take_profit": take_profit, "opened_at": opened_at,
    }
```

Replace the inline dict in `_execute` (the `set_json(f"trading:position:{event.event_id}", {...})` call) with:

```python
        await self._cache.set_json(
            f"trading:position:{event.event_id}",
            build_position_payload(
                symbol=event.symbol, pair=pair, side=side, size=size,
                entry_price=event.entry_price, position_size_pct=event.position_size_pct,
                stop_loss=event.stop_loss, take_profit=event.take_profit,
                opened_at=event.occurred_at.isoformat() if getattr(event, "occurred_at", None) else None,
            ),
            ttl_seconds=0,
        )
```

- [ ] **Step 4: Run tests to verify green**

Run: `python -m pytest tests/test_trading_position_payload.py tests/test_trading_runtime.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/engine.py tests/test_trading_position_payload.py
git commit -m "feat(trading-engine): persist stop_loss/take_profit/opened_at on positions"
```

---

## Task 4: Domain mappers (pure functions)

**Files:**
- Create: `services/api-gateway/app/mappers.py`
- Test: `tests/test_api_gateway_mappers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_gateway_mappers.py
import importlib.util
from pathlib import Path
from types import SimpleNamespace

_PATH = Path(__file__).resolve().parents[1] / "services" / "api-gateway" / "app" / "mappers.py"


def _mappers():
    spec = importlib.util.spec_from_file_location("agw_mappers", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_position_long_pnl_and_protected():
    m = _mappers()
    pos = {"event_id": "e1", "symbol": "SOL", "side": "buy", "size": 2.0,
           "entry_price": 100.0, "current_price": 110.0, "stop_loss": 90.0,
           "take_profit": None, "opened_at": "2026-07-22T10:00:00Z"}
    out = m.position_to_domain(pos, mode="demo")
    assert out["direction"] == "long"
    assert out["quantity"] == 2.0
    assert out["unrealized_pnl_usd"] == 20.0
    assert out["unrealized_pnl_pct"] == 10.0
    assert out["value_usd"] == 220.0
    assert out["protected"] is True
    assert out["mode"] == "demo"
    assert out["position_id"] == "e1"


def test_position_defaults_current_price_to_entry():
    m = _mappers()
    out = m.position_to_domain({"event_id": "e2", "symbol": "X", "side": "sell",
                                "size": 1.0, "entry_price": 50.0}, mode="dry_run")
    assert out["direction"] == "short"
    assert out["current_price"] == 50.0
    assert out["unrealized_pnl_usd"] == 0.0
    assert out["protected"] is False


def test_trade_maps_direction_to_side():
    m = _mappers()
    row = SimpleNamespace(event_id="t1", symbol="SOL", direction="long",
                          entry_price=140.0, fill_price=141.0, pnl=5.0,
                          status="filled", created_at="2026-07-22T10:00:00Z")
    out = m.trade_to_domain(row)
    assert out["trade_id"] == "t1" and out["side"] == "buy"
    assert out["price"] == 141.0 and out["pnl_usd"] == 5.0
    assert out["status"] == "filled"


def test_decision_worker_from_ai_validated():
    m = _mappers()
    row = SimpleNamespace(event_id="d1", symbol="SOL", direction="long",
                          opportunity_score=80, confidence=0.7, ai_validated=True,
                          rationale="strong", created_at="2026-07-22T10:00:00Z")
    out = m.decision_to_domain(row)
    assert out["worker"] == "sonnet" and out["escalated"] is True
    assert out["justification"] == "strong" and out["decision"] == "long"


def test_limits_from_runtime():
    m = _mappers()
    out = m.limits_from_runtime({"max_order_usd": 500, "max_leverage": 3, "max_orders_per_hour": 10})
    keys = {r["key"] for r in out}
    assert keys == {"max_order_usd", "max_leverage", "max_orders_per_hour"}
    assert all(r["breached"] is False for r in out)


def test_exposure_aggregates_positions():
    m = _mappers()
    positions = [
        m.position_to_domain({"event_id": "e1", "symbol": "SOL", "side": "buy",
                              "size": 1.0, "entry_price": 100.0, "current_price": 100.0,
                              "stop_loss": 90.0}, mode="demo"),
        m.position_to_domain({"event_id": "e2", "symbol": "SOL", "side": "buy",
                              "size": 1.0, "entry_price": 100.0, "current_price": 100.0},
                             mode="demo"),
    ]
    out = m.exposure_from_positions(positions, runtime={"max_leverage": 3})
    assert out["open_positions"] == 2
    assert out["protected_positions"] == 1
    assert out["total_exposure_usd"] == 200.0
    assert len(out["by_asset"]) == 1  # both SOL, aggregated
    assert out["by_asset"][0]["symbol"] == "SOL"
    assert out["by_asset"][0]["exposure_usd"] == 200.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_gateway_mappers.py -v`
Expected: FAIL with `FileNotFoundError` / `No module` for `mappers.py`

- [ ] **Step 3: Implement the mappers**

```python
# services/api-gateway/app/mappers.py
"""Pure mappers: Redis dicts / ORM rows -> frontend domain.ts shapes.

Kept side-effect-free so they can be unit-tested without DB or Redis. Fields with
no Tier-1 source get explicit, documented defaults (live prices arrive with the
market-persistence increment; portfolio equity with the portfolio increment)."""
from __future__ import annotations

from typing import Any


def position_to_domain(pos: dict[str, Any], *, mode: str) -> dict[str, Any]:
    side = pos.get("side", "buy")
    direction = "long" if side == "buy" else "short"
    size = float(pos.get("size", 0) or 0)
    entry = float(pos.get("entry_price", 0) or 0)
    # No live price source in Tier 1 -> current == entry (zero unrealized PnL).
    current = float(pos.get("current_price", entry) or entry)
    sign = 1 if direction == "long" else -1
    pnl = sign * (current - entry) * size
    cost = entry * size
    sl = pos.get("stop_loss")
    tp = pos.get("take_profit")
    return {
        "position_id": pos.get("event_id", ""),
        "event_id": pos.get("event_id"),
        "symbol": pos.get("symbol", ""),
        "direction": direction,
        "quantity": size,
        "entry_price": entry,
        "current_price": current,
        "value_usd": round(current * size, 2),
        "unrealized_pnl_usd": round(pnl, 2),
        "unrealized_pnl_pct": round((pnl / cost * 100) if cost else 0.0, 2),
        "stop_loss": sl,
        "take_profit": tp,
        "protected": sl is not None or tp is not None,
        "opened_at": pos.get("opened_at"),
        "mode": mode,
    }


_TRADE_STATUS = {
    "filled": "filled", "closed": "filled", "submitted": "pending",
    "approved": "pending", "pending": "pending", "rejected": "rejected",
    "partial": "partial",
}


def trade_to_domain(row: Any) -> dict[str, Any]:
    side = "buy" if row.direction == "long" else "sell"
    price = row.fill_price if row.fill_price is not None else row.entry_price
    return {
        "trade_id": row.event_id,
        "symbol": row.symbol,
        "side": side,
        "order_type": "limit",
        "price": float(price or 0),
        "quantity": 0.0,   # size not stored on the approved-signal row
        "cost_usd": 0.0,
        "fee_usd": 0.0,
        "pnl_usd": row.pnl,
        "status": _TRADE_STATUS.get(row.status, "pending"),
        "mode": "live",    # per-trade mode not tracked in Tier 1
        "executed_at": row.created_at,
    }


def decision_to_domain(row: Any) -> dict[str, Any]:
    return {
        "id": row.event_id,
        "symbol": row.symbol,
        "worker": "sonnet" if row.ai_validated else "haiku",
        "decision": row.direction,
        "opportunity_score": row.opportunity_score,
        "confidence": row.confidence,
        "justification": row.rationale,
        "escalated": bool(row.ai_validated),
        "created_at": row.created_at,
    }


def signal_to_domain(row: Any) -> dict[str, Any]:
    if getattr(row, "payload", None):
        return row.payload
    return {
        "symbol": row.symbol,
        "opportunity_score": row.opportunity_score,
        "confidence": row.confidence,
        "reason": row.reason,
        "time": row.time,
    }


def limits_from_runtime(runtime: dict[str, Any] | None) -> list[dict[str, Any]]:
    caps = runtime or {}
    specs = [
        ("max_order_usd", "Max order size", "USD"),
        ("max_leverage", "Max leverage", "x"),
        ("max_orders_per_hour", "Orders / hour", "/h"),
    ]
    out = []
    for key, label, unit in specs:
        value = float(caps.get(key, 0) or 0)
        out.append({"key": key, "label": label, "value": value,
                    "max": value, "unit": unit, "breached": False})
    return out


def exposure_from_positions(
    positions: list[dict[str, Any]], *, runtime: dict[str, Any] | None
) -> dict[str, Any]:
    by_symbol: dict[str, dict[str, Any]] = {}
    total = 0.0
    protected = 0
    for p in positions:
        total += p["value_usd"]
        if p["protected"]:
            protected += 1
        agg = by_symbol.setdefault(
            p["symbol"], {"symbol": p["symbol"], "exposure_usd": 0.0,
                          "exposure_pct": 0.0, "limit_pct": 0.0, "protected": False}
        )
        agg["exposure_usd"] = round(agg["exposure_usd"] + p["value_usd"], 2)
        agg["protected"] = agg["protected"] or p["protected"]
    for agg in by_symbol.values():
        agg["exposure_pct"] = round((agg["exposure_usd"] / total * 100) if total else 0.0, 2)
    return {
        "total_exposure_usd": round(total, 2),
        # total_exposure_pct / daily_loss_* need portfolio equity (Increment 3).
        "total_exposure_pct": 0.0,
        "max_exposure_pct": 100.0,
        "by_asset": list(by_symbol.values()),
        "protected_positions": protected,
        "open_positions": len(positions),
        "daily_loss_usd": 0.0,
        "daily_loss_limit_usd": 0.0,
        "updated_at": None,
    }
```

- [ ] **Step 4: Run tests to verify green**

Run: `python -m pytest tests/test_api_gateway_mappers.py -v`
Expected: PASS (all 6)

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/mappers.py tests/test_api_gateway_mappers.py
git commit -m "feat(api-gateway): pure domain mappers for read endpoints"
```

---

## Task 5: Wire Redis + StateReader + JWT auth into api-gateway

**Files:**
- Modify: `services/api-gateway/app/main.py`
- Modify: `services/api-gateway/app/routers.py` (add `get_reader_dep`, protect router)

- [ ] **Step 1: Add the reader dependency + auth to `routers.py`**

At the top of `services/api-gateway/app/routers.py`, update imports and add the dependency stub + router-level auth:

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from cmi_common.auth import require_principal
from cmi_common.db import Decision, Signal, Trade

router = APIRouter(prefix="/api/v1", tags=["intelligence"],
                   dependencies=[Depends(require_principal)])


def get_session_dep():
    # Bound in main.py to the service's Database instance.
    raise NotImplementedError


def get_reader_dep():
    # Bound in main.py to a cmi_common.state.StateReader instance.
    raise NotImplementedError
```

- [ ] **Step 2: Bind Redis + reader in `main.py`**

Replace `services/api-gateway/app/main.py` `_startup`/`_shutdown` with:

```python
"""api-gateway entrypoint: REST read API + event persister."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db import Database
from cmi_common.kafka import EventConsumer, Topic
from cmi_common.state import StateReader

from . import routers
from .persister import Persister


async def _startup(app: FastAPI, settings: Settings) -> None:
    db = Database(settings.db)
    cache = Cache(settings.redis)
    reader = StateReader(cache, db=db)
    persister = Persister(db)
    consumer = EventConsumer(
        settings.kafka,
        [Topic.ANALYSIS, Topic.DECISION, Topic.RISK_APPROVED, Topic.EXECUTION],
        persister.handle,
        group_id="api-gateway-persister",
    )
    await consumer.start()
    app.state.db = db
    app.state.cache = cache
    app.state.reader = reader
    app.state.consumer = consumer
    app.state.consumer_task = asyncio.create_task(consumer.run())

    app.dependency_overrides[routers.get_session_dep] = db.session
    app.dependency_overrides[routers.get_reader_dep] = lambda: reader


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    await app.state.consumer.stop()
    await asyncio.gather(app.state.consumer_task, return_exceptions=True)
    await app.state.cache.close()
    await app.state.db.dispose()


app = create_app("api-gateway", on_startup=_startup, on_shutdown=_shutdown)
app.include_router(routers.router)
```

- [ ] **Step 3: Sanity-check the service imports**

Run: `python -c "import importlib.util, pathlib; p=pathlib.Path('services/api-gateway/app/main.py'); print('main.py present' if p.exists() else 'missing')"`
Expected: `main.py present`

Run: `python -m pytest tests/test_api_gateway_mappers.py -v`
Expected: PASS (regression check; no behavior change here)

- [ ] **Step 4: Commit**

```bash
git add services/api-gateway/app/main.py services/api-gateway/app/routers.py
git commit -m "feat(api-gateway): add Redis reader + JWT auth to read API"
```

---

## Task 6: Tier-1 read routes

Add the six routes to `services/api-gateway/app/routers.py`. Each returns mapped domain shapes. Routes read the runtime `mode` once for position/exposure mapping.

**Files:**
- Modify: `services/api-gateway/app/routers.py`
- Test: `tests/test_api_gateway_routes.py`

- [ ] **Step 1: Write the failing test (route-level, fake reader + fake session)**

```python
# tests/test_api_gateway_routes.py
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

_PATH = Path(__file__).resolve().parents[1] / "services" / "api-gateway" / "app" / "routers.py"


def _routers():
    # mappers.py sits next to routers.py; load the package dir on sys.path.
    import sys
    app_dir = _PATH.parent
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
    spec = importlib.util.spec_from_file_location("agw_routers", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeReader:
    def __init__(self, positions, settings):
        self._positions = positions
        self._settings = settings
    async def positions(self): return self._positions
    async def settings(self): return self._settings


def test_positions_route_maps_and_uses_mode():
    r = _routers()
    reader = FakeReader(
        positions=[{"event_id": "e1", "symbol": "SOL", "side": "buy",
                    "size": 1.0, "entry_price": 100.0, "stop_loss": 90.0}],
        settings={"mode": "demo"},
    )
    out = asyncio.run(r.portfolio_positions(reader=reader))
    assert out[0]["symbol"] == "SOL" and out[0]["mode"] == "demo"
    assert out[0]["protected"] is True


def test_risk_limits_route():
    r = _routers()
    reader = FakeReader(positions=[], settings={"max_order_usd": 500, "max_leverage": 3, "max_orders_per_hour": 10})
    out = asyncio.run(r.risk_limits(reader=reader))
    assert {row["key"] for row in out} == {"max_order_usd", "max_leverage", "max_orders_per_hour"}


def test_risk_exposure_route():
    r = _routers()
    reader = FakeReader(
        positions=[{"event_id": "e1", "symbol": "SOL", "side": "buy",
                    "size": 1.0, "entry_price": 100.0}],
        settings={"mode": "live"},
    )
    out = asyncio.run(r.risk_exposure(reader=reader))
    assert out["open_positions"] == 1 and out["total_exposure_usd"] == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_gateway_routes.py -v`
Expected: FAIL with `AttributeError: module 'agw_routers' has no attribute 'portfolio_positions'`

- [ ] **Step 3: Add the routes**

Append to `services/api-gateway/app/routers.py` (after the existing routes). The `from . import mappers` import must go at the top with the other imports; when loaded as a lone module in tests the `sys.path` insert in the test makes `import mappers` resolve, so use a resilient import:

```python
try:  # package context (service runtime)
    from . import mappers
except ImportError:  # standalone module load (tests)
    import mappers  # type: ignore
```

Routes:

```python
@router.get("/portfolio/positions")
async def portfolio_positions(reader=Depends(get_reader_dep)) -> list[dict]:
    mode = (await reader.settings()).get("mode", "dry_run")
    positions = await reader.positions()
    return [mappers.position_to_domain(p, mode=mode) for p in positions]


@router.get("/portfolio/trades")
async def portfolio_trades(
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session_dep),
) -> list[dict]:
    stmt = select(Trade).order_by(desc(Trade.created_at)).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [mappers.trade_to_domain(r) for r in rows]


@router.get("/market/decisions")
async def market_decisions(
    limit: int = Query(30, ge=1, le=500),
    session: AsyncSession = Depends(get_session_dep),
) -> list[dict]:
    stmt = select(Decision).order_by(desc(Decision.created_at)).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [mappers.decision_to_domain(r) for r in rows]


@router.get("/market/signals")
async def market_signals(
    limit: int = Query(30, ge=1, le=500),
    session: AsyncSession = Depends(get_session_dep),
) -> list[dict]:
    stmt = select(Signal).order_by(desc(Signal.time)).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [mappers.signal_to_domain(r) for r in rows]


@router.get("/risk/limits")
async def risk_limits(reader=Depends(get_reader_dep)) -> list[dict]:
    return mappers.limits_from_runtime(await reader.settings())


@router.get("/risk/exposure")
async def risk_exposure(reader=Depends(get_reader_dep)) -> dict:
    mode = (await reader.settings()).get("mode", "dry_run")
    positions = [mappers.position_to_domain(p, mode=mode) for p in await reader.positions()]
    return mappers.exposure_from_positions(positions, runtime=await reader.settings())
```

- [ ] **Step 4: Run tests to verify green**

Run: `python -m pytest tests/test_api_gateway_routes.py tests/test_api_gateway_mappers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/routers.py tests/test_api_gateway_routes.py
git commit -m "feat(api-gateway): serve Tier-1 read endpoints (positions/trades/decisions/signals/risk)"
```

---

## Task 7: Point the frontend read paths at the unified gateway routes

The frontend read endpoints currently use paths like `/portfolio/positions` on the `api` base. api-gateway serves them under the `/api/v1` prefix. Align the two: add the `/api/v1` prefix to the read calls in `endpoints.ts` (mock BFF paths are unaffected because mock mode overrides the base to `/api/mock` and the mock routes live at `/api/mock/portfolio/...`).

**Decision:** Keep the frontend paths unprefixed and instead mount the new read routes at the root the frontend already expects. Simpler and avoids touching every frontend call. Change the new routes' router prefix handling: expose the Tier-1 routes without the `/api/v1` prefix.

**Files:**
- Modify: `services/api-gateway/app/routers.py` (put Tier-1 routes on a second router with no prefix)
- Modify: `services/api-gateway/app/main.py` (include the second router)

- [ ] **Step 1: Move Tier-1 routes onto an unprefixed router**

In `routers.py`, define a second router above the Tier-1 routes and attach the six routes to it instead of `router`:

```python
read_router = APIRouter(tags=["terminal-read"], dependencies=[Depends(require_principal)])
```

Change the six Tier-1 route decorators from `@router.get(...)` to `@read_router.get(...)`, keeping paths as `/portfolio/positions`, `/portfolio/trades`, `/market/decisions`, `/market/signals`, `/risk/limits`, `/risk/exposure`.

- [ ] **Step 2: Include the read router in `main.py`**

Add after `app.include_router(routers.router)`:

```python
app.include_router(routers.read_router)
```

And bind its deps too (the `dependency_overrides` in `_startup` already cover `get_session_dep`/`get_reader_dep` globally, so no extra binding needed).

- [ ] **Step 3: Update the route test loader for the new router object**

The tests call the route functions directly, so no path change is needed. Re-run:

Run: `python -m pytest tests/test_api_gateway_routes.py -v`
Expected: PASS

- [ ] **Step 4: Frontend typecheck (no code change expected, guard only)**

Run: `cd frontend && npm run typecheck`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/routers.py services/api-gateway/app/main.py
git commit -m "feat(api-gateway): mount Tier-1 read routes at terminal paths"
```

---

## Task 8: Full suite + lint gate

- [ ] **Step 1: Run the whole Python suite**

Run: `python -m pytest -q`
Expected: PASS (no regressions)

- [ ] **Step 2: Lint the touched Python**

Run: `python -m ruff check libs/cmi_common services/api-gateway services/control-api services/trading-engine`
Expected: no errors (fix any inline, re-run)

- [ ] **Step 3: Frontend gates**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: PASS

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -A
git commit -m "chore: lint fixes for read-endpoints increment 1"
```

---

## Self-review checklist (author)

- **Spec coverage:** Tier-1 endpoints from the design's §3 table are all implemented (positions/trades/decisions/signals/risk-limits/risk-exposure). Unified read API (§2) = Tasks 5+7. Shared StateReader (§2) = Task 1. JWT auth (§2) = Task 2. Portfolio-equity-dependent fields explicitly defaulted (documented in mappers).
- **Deferred to later increments:** `/portfolio`, `/portfolio/history`, `/market/tokens*`, `/market/news`, `/risk/alerts` — Increments 2-4. Not in this plan by design.
- **Type consistency:** mapper function names (`position_to_domain`, `trade_to_domain`, `decision_to_domain`, `signal_to_domain`, `limits_from_runtime`, `exposure_from_positions`) are used identically in Tasks 4 and 6. Route function names (`portfolio_positions`, `risk_limits`, `risk_exposure`) match between Task 6 impl and Task 6 tests.

## Follow-up plans (not this increment)
- **Increment 2 — market persistence:** extend api-gateway persister to consume price/dex/news/sentiment → `/market/tokens`, `/market/tokens/{s}/prices`, `/market/news`.
- **Increment 3 — portfolio aggregate + history:** trading-engine equity snapshots (Redis `trading:portfolio` + `portfolio_snapshots` hypertable) → `/portfolio`, `/portfolio/history`; backfills `total_exposure_pct` / `daily_loss_*`.
- **Increment 4 — risk alerts:** `risk.alert.events` topic + risk-engine producer + `risk_alerts` table → `/risk/alerts`.
