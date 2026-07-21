# Control-API Trading Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the operator full control of the trading bot from the Next.js frontend through a new decoupled `control-api` service that issues Kafka commands the `trading-engine` applies, with runtime settings in Redis, human-in-the-loop approval, manual actions, and JWT auth.

**Architecture:** New `services/control-api` (front-facing, JWT-protected) publishes `ControlCommandEvent` on `control.commands` and reads state (Redis live + DB read-only). The `trading-engine` is the sole authority: it consumes commands, maintains `trading:runtime` config in Redis (env values become defaults), gates auto-trading, and executes manual actions through the same guards. Shared JWT lives in `cmi_common`.

**Tech Stack:** Python 3.12, Pydantic v2, aiokafka, redis-py (asyncio), SQLAlchemy async, FastAPI, httpx; Next.js 14 / TypeScript / MUI frontend.

**Reference spec:** `docs/superpowers/specs/2026-07-21-control-api-trading-control-design.md`

---

## Preliminary notes for the executor

- Repo is git-initialized on branch `feat/kraken-trading-engine` (the trading-engine work is
  already committed). Continue on this branch. Run tests from repo root with `python -m pytest`.
  The repo's pytest suppresses the summary line via `addopts`; use `python -m pytest -o addopts="" -q`
  when you need the pass count. Baseline is green.
- The plan is organised in **4 phases (A→D)**, each independently shippable. Implement in order.
- Follow existing patterns: services use `create_app` + `on_startup`/`on_shutdown`, `EventConsumer`/
  `EventProducer`, `Cache`, `Database(settings.db)` with `db.session` dependency. Trading-engine app
  modules are loaded in tests via `tests/trading_helpers.py` (loads the `app` package as `tengine`).
- Do NOT deviate from the code below; it is authoritative. End every commit message with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- If a step's expected result diverges from reality, STOP and report precisely. Small obvious fixes
  (typo, clearly-intended import) are fine — note them.

---

# PHASE A — Réglages (runtime config, commands, control-api settings, auth, front)

## Task A1: `ControlCommandEvent` in cmi_common

**Files:**
- Create: `libs/cmi_common/cmi_common/events/control.py`
- Modify: `libs/cmi_common/cmi_common/events/base.py` (add `EventType.CONTROL_COMMAND`, `Source.CONTROL_API`)
- Modify: `libs/cmi_common/cmi_common/events/__init__.py` (union + `__all__`)
- Test: `tests/test_control_event.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_event.py
from cmi_common.events import parse_event
from cmi_common.events.control import ControlCommand, ControlCommandEvent


def test_control_command_roundtrip() -> None:
    ev = ControlCommandEvent(
        command=ControlCommand.SET_MODE,
        payload={"mode": "demo"},
        issued_by="admin",
    )
    decoded = parse_event(ev.as_kafka_value())
    assert isinstance(decoded, ControlCommandEvent)
    assert decoded.command == ControlCommand.SET_MODE
    assert decoded.payload == {"mode": "demo"}
    assert decoded.issued_by == "admin"


def test_control_command_partition_key_is_stable() -> None:
    ev = ControlCommandEvent(command=ControlCommand.SET_KILL_SWITCH, payload={"enabled": False})
    # All control commands share one partition for global ordering.
    assert ev.partition_key() == "control"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_control_event.py -v`
Expected: FAIL with `ModuleNotFoundError: cmi_common.events.control`

- [ ] **Step 3: Add enum members**

In `libs/cmi_common/cmi_common/events/base.py`, add to `EventType` (after `EXECUTION`):
```python
    CONTROL_COMMAND = "ControlCommandEvent"
```
add to `Source` (after `TRADING_ENGINE`):
```python
    CONTROL_API = "control-api"
```

- [ ] **Step 4: Create the event model**

```python
# libs/cmi_common/cmi_common/events/control.py
"""Control commands issued by control-api and applied by the trading-engine."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field

from .base import BaseEvent, EventType, Source


class ControlCommand(str, Enum):
    SET_MODE = "set_mode"
    SET_KILL_SWITCH = "set_kill_switch"
    SET_AUTO_TRADING = "set_auto_trading"
    SET_CAPS = "set_caps"
    CLOSE_POSITION = "close_position"
    ADJUST_SLTP = "adjust_sltp"
    MANUAL_ORDER = "manual_order"
    APPROVE_OPPORTUNITY = "approve_opportunity"
    REJECT_OPPORTUNITY = "reject_opportunity"


class ControlCommandEvent(BaseEvent):
    """Published on ``control.commands`` — an operator intent for the engine."""

    event_type: Literal[EventType.CONTROL_COMMAND] = EventType.CONTROL_COMMAND
    source: Source = Source.CONTROL_API
    command: ControlCommand
    payload: dict[str, Any] = Field(default_factory=dict)
    issued_by: str | None = None

    def partition_key(self) -> str:
        # Single partition so commands apply in a total order.
        return "control"
```

- [ ] **Step 5: Register in the union**

In `libs/cmi_common/cmi_common/events/__init__.py`:
- Import after the execution import: `from .control import ControlCommand, ControlCommandEvent`
- Add `ControlCommandEvent,` inside the `Union[...]`.
- Add `"ControlCommand",` and `"ControlCommandEvent",` to `__all__`.

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_control_event.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add libs/cmi_common/cmi_common/events tests/test_control_event.py
git commit -m "feat(events): add ControlCommandEvent"
```

---

## Task A2: `control.commands` topic

**Files:**
- Modify: `libs/cmi_common/cmi_common/kafka/topics.py`
- Test: `tests/test_control_topic.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_topic.py
from cmi_common.events.control import ControlCommandEvent
from cmi_common.kafka.topics import TOPIC_EVENT, TOPIC_PARTITIONS, Topic


def test_control_topic_registered() -> None:
    assert Topic.CONTROL.value == "control.commands"
    assert TOPIC_EVENT[Topic.CONTROL] is ControlCommandEvent
    assert TOPIC_PARTITIONS[Topic.CONTROL] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_control_topic.py -v`
Expected: FAIL with `AttributeError: CONTROL`

- [ ] **Step 3: Register the topic**

In `libs/cmi_common/cmi_common/kafka/topics.py`:
- Import: `from ..events.control import ControlCommandEvent`
- Add member after `EXECUTION`: `CONTROL = "control.commands"`
- Add `Topic.CONTROL: ControlCommandEvent,` to `TOPIC_EVENT`
- Add `Topic.CONTROL: 3,` to `TOPIC_PARTITIONS`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_control_topic.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/kafka/topics.py tests/test_control_topic.py
git commit -m "feat(kafka): register control.commands topic"
```

---

## Task A3: Shared JWT in cmi_common + `ExecutionKind.PENDING`

**Files:**
- Create: `libs/cmi_common/cmi_common/auth.py`
- Modify: `libs/cmi_common/cmi_common/events/execution.py` (add `PENDING`)
- Modify: `services/websocket-gateway/app/auth.py` (re-export from shared)
- Test: `tests/test_shared_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shared_auth.py
import pytest

from cmi_common.auth import InvalidTokenError, decode_token, encode_token


def test_encode_decode_roundtrip_verified(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    token = encode_token({"sub": "admin", "role": "operator"}, secret="s3cret", ttl_seconds=60)
    p = decode_token(token)
    assert p.sub == "admin"
    assert p.role == "operator"
    assert p.verified is True


def test_bad_signature_rejected_when_secret_set(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    token = encode_token({"sub": "x"}, secret="different", ttl_seconds=60)
    with pytest.raises(InvalidTokenError):
        decode_token(token)


def test_pending_execution_kind_exists() -> None:
    from cmi_common.events.execution import ExecutionKind
    assert ExecutionKind.PENDING.value == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_shared_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: cmi_common.auth`

- [ ] **Step 3: Create the shared auth module**

Copy the existing `services/websocket-gateway/app/auth.py` verbatim into
`libs/cmi_common/cmi_common/auth.py`, then ADD an `encode_token` function using the existing
`_b64url_encode` helper:

```python
# append to libs/cmi_common/cmi_common/auth.py (after decode_token)
import json as _json
import time as _time


def encode_token(claims: dict[str, Any], *, secret: str, ttl_seconds: int = 3600) -> str:
    """Mint an HS256 JWT. `exp` is added from ttl_seconds (informational; decode_token
    does not enforce expiry, matching the existing lenient decoder)."""
    header = {"alg": "HS256", "typ": "JWT"}
    body = dict(claims)
    body.setdefault("iat", int(_time.time()))
    body["exp"] = int(_time.time()) + ttl_seconds
    header_b64 = _b64url_encode(_json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(_json.dumps(body, separators=(",", ":")).encode())
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{header_b64}.{payload_b64}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"
```

Ensure the module's top imports include `hashlib`, `hmac`, `base64` (already present from the copy).

- [ ] **Step 4: Point websocket-gateway at the shared module**

Replace the body of `services/websocket-gateway/app/auth.py` with a re-export so its callers keep
working unchanged:

```python
# services/websocket-gateway/app/auth.py
"""Re-export of the shared JWT helpers (moved to cmi_common.auth)."""
from cmi_common.auth import (  # noqa: F401
    InvalidTokenError,
    Principal,
    decode_token,
    encode_token,
)

__all__ = ["InvalidTokenError", "Principal", "decode_token", "encode_token"]
```

- [ ] **Step 5: Add `ExecutionKind.PENDING`**

In `libs/cmi_common/cmi_common/events/execution.py`, add to `ExecutionKind` (after `SUBMITTED`):
```python
    PENDING = "pending"       # queued awaiting operator approval (auto-trading off)
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_shared_auth.py -v`
Expected: PASS (3 passed)
Run: `python -m pytest -o addopts="" -q`
Expected: all green (existing websocket-gateway tests still pass via the re-export).

- [ ] **Step 7: Commit**

```bash
git add libs/cmi_common/cmi_common/auth.py libs/cmi_common/cmi_common/events/execution.py services/websocket-gateway/app/auth.py tests/test_shared_auth.py
git commit -m "feat(auth): shared JWT in cmi_common + ExecutionKind.PENDING"
```

---

## Task A4: Runtime config in the trading-engine

**Files:**
- Create: `services/trading-engine/app/runtime.py`
- Test: `tests/test_trading_runtime.py`

Effective config = env defaults overlaid with the `trading:runtime` Redis JSON.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_runtime.py
import asyncio

from tests.trading_helpers import load_module


class FakeCache:
    def __init__(self, values=None):
        self._values = dict(values or {})
        self.sets = {}

    async def get_json(self, key):
        return self._values.get(key)

    async def set_json(self, key, value, ttl_seconds=60):
        self._values[key] = value
        self.sets[key] = value


def _mods():
    return load_module("runtime"), load_module("config")


def test_load_returns_defaults_when_absent() -> None:
    runtime, config = _mods()
    defaults = config.TradingConfig(max_order_usd=500.0)
    cache = FakeCache()
    eff = asyncio.run(runtime.RuntimeConfig.load(cache, defaults))
    assert eff.max_order_usd == 500.0
    assert eff.mode == config.Mode.DRY_RUN


def test_redis_overlay_wins() -> None:
    runtime, config = _mods()
    defaults = config.TradingConfig(max_order_usd=500.0, trading_enabled=True)
    cache = FakeCache(values={"trading:runtime": {
        "mode": "demo", "trading_enabled": False, "max_order_usd": 250.0,
    }})
    eff = asyncio.run(runtime.RuntimeConfig.load(cache, defaults))
    assert eff.mode == config.Mode.DEMO
    assert eff.trading_enabled is False
    assert eff.max_order_usd == 250.0
    # unspecified fields keep defaults
    assert eff.max_leverage == defaults.max_leverage


def test_write_defaults_only_if_absent() -> None:
    runtime, config = _mods()
    defaults = config.TradingConfig()
    cache = FakeCache()
    asyncio.run(runtime.RuntimeConfig.write_defaults_if_absent(cache, defaults))
    assert "trading:runtime" in cache.sets
    # second call must not overwrite an operator-modified value
    cache._values["trading:runtime"]["mode"] = "live"
    asyncio.run(runtime.RuntimeConfig.write_defaults_if_absent(cache, defaults))
    assert cache._values["trading:runtime"]["mode"] == "live"


def test_set_field_updates_runtime() -> None:
    runtime, config = _mods()
    defaults = config.TradingConfig()
    cache = FakeCache()
    asyncio.run(runtime.RuntimeConfig.write_defaults_if_absent(cache, defaults))
    asyncio.run(runtime.RuntimeConfig.set_fields(cache, {"auto_trading_enabled": False}))
    assert cache._values["trading:runtime"]["auto_trading_enabled"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading_runtime.py -v`
Expected: FAIL (runtime.py does not exist)

- [ ] **Step 3: Add `auto_trading_enabled` to TradingConfig**

In `services/trading-engine/app/config.py`, add a field to `TradingConfig` (after `trading_enabled`):
```python
    auto_trading_enabled: bool = True
```
and in `from_env` (after the `trading_enabled=...` line):
```python
            auto_trading_enabled=_bool("AUTO_TRADING_ENABLED", True),
```

- [ ] **Step 4: Implement runtime.py**

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_trading_runtime.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add services/trading-engine/app/config.py services/trading-engine/app/runtime.py tests/test_trading_runtime.py
git commit -m "feat(trading-engine): Redis runtime config overlay"
```

---

## Task A5: Engine reads effective runtime config; Kraken client mode-provider

**Files:**
- Modify: `services/trading-engine/app/kraken.py` (mode-provider)
- Modify: `services/trading-engine/app/engine.py` (load runtime config per handle)
- Modify: `services/trading-engine/app/guards.py` (drop separate kill-switch Redis read)
- Modify: `tests/test_trading_kraken.py`, `tests/test_trading_guards.py`, `tests/test_trading_engine.py`
- Test: `tests/test_trading_mode_switch.py`

Runtime mode switching requires the Kraken client to resolve its mode dynamically instead of at
construction. We give it a `mode_provider` callable and always create the httpx client on start.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_mode_switch.py
import asyncio

from tests.trading_helpers import load_module


def test_client_mode_follows_provider() -> None:
    kraken = load_module("kraken")
    config_mod = load_module("config")
    mode = {"m": config_mod.Mode.DRY_RUN}
    cfg = config_mod.TradingConfig(api_key="k", api_secret="c2VjcmV0")
    client = kraken.KrakenFuturesClient(cfg, mode_provider=lambda: mode["m"])
    # dry_run -> no network, simulated
    r = asyncio.run(client.send_order(
        pair="PF_SOLUSD", side="buy", order_type="lmt", size=1.0,
        limit_price=100.0, cli_ord_id="e1"))
    assert r["dry_run"] is True
    # switch to live -> base_url resolves live host
    mode["m"] = config_mod.Mode.LIVE
    assert client.current_base_url() == "https://futures.kraken.com/derivatives"
    mode["m"] = config_mod.Mode.DEMO
    assert client.current_base_url() == "https://demo-futures.kraken.com/derivatives"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading_mode_switch.py -v`
Expected: FAIL (KrakenFuturesClient has no `mode_provider` / `current_base_url`)

- [ ] **Step 3: Refactor kraken.py to a mode-provider**

In `services/trading-engine/app/kraken.py`:
- Change the constructor signature and internals:
```python
    def __init__(self, config: TradingConfig, *, mode_provider=None) -> None:
        self._config = config
        self._mode_provider = mode_provider or (lambda: config.mode)
        self._secret = config.api_secret
        self._key = config.api_key
        self._http: httpx.AsyncClient | None = None

    def _mode(self) -> Mode:
        return self._mode_provider()

    def current_base_url(self) -> str:
        return _HOSTS[self._mode()]
```
- Remove the fixed `self.base_url` assignment. In tests that referenced `client.base_url`, they now
  use `current_base_url()` (updated in Step 6).
- `start()` always creates the client (cheap; unused in dry_run):
```python
    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=10.0)
```
- In every method, replace `self._config.mode is Mode.DRY_RUN` with `self._mode() is Mode.DRY_RUN`,
  and in `_post` use `self.current_base_url() + endpoint_path` instead of `self.base_url + ...`.

- [ ] **Step 4: Simplify guards.py to use the effective config only**

In `services/trading-engine/app/guards.py`, the effective config already carries the runtime
`trading_enabled` (loaded via RuntimeConfig), so drop the separate `trading:enabled` Redis read.
Replace `check_guards` with:
```python
async def check_guards(cache: GuardCache, config: TradingConfig) -> str | None:
    if not config.trading_enabled:
        return "kill_switch"
    allowed = await cache.allow(ORDERS_RATE_KEY, config.max_orders_per_hour, 3600)
    if not allowed:
        return "rate_limit"
    return None
```
Remove the now-unused `KILL_SWITCH_KEY` constant and the `get_json` line. Keep the `GuardCache`
Protocol (it still needs `allow`; you may drop `get_json` from the Protocol).

- [ ] **Step 5: Engine loads effective config each handle**

In `services/trading-engine/app/engine.py`:
- The constructor currently takes `config`. Rename the stored attribute to `self._defaults` and add
  a `mode_provider`. Change `__init__`:
```python
    def __init__(self, cache, producer, kraken, defaults: TradingConfig) -> None:
        self._cache = cache
        self._producer = producer
        self._kraken = kraken
        self._defaults = defaults
```
- At the top of `handle`, after the `isinstance` check and BEFORE guards, load the effective config:
```python
        from .runtime import RuntimeConfig
        config = await RuntimeConfig.load(self._cache, self._defaults)
```
  Then use `config` everywhere the method previously used `self._config` (guards call, sizing caps).
  (The idempotency check stays first, before loading config is fine either order; keep idempotency
  first per the earlier decision.)
- Wherever sizing reads caps, pass `config.max_order_usd`, `config.max_leverage`.

- [ ] **Step 6: Update the affected existing tests**

- `tests/test_trading_kraken.py`: change the two references to `client.base_url` /
  `live.base_url` / `demo.base_url` to `client.current_base_url()` etc., and construct the client
  as `kraken.KrakenFuturesClient(cfg)` (mode_provider defaults to the config's mode, so
  `test_base_url_per_mode` still holds with a fixed-mode config).
- `tests/test_trading_guards.py`: remove `test_kill_switch_redis_blocks` (the Redis kill key no
  longer exists; the runtime overlay carries it). Keep `test_kill_switch_env_blocks` (now "config
  disabled"), `test_rate_limit_blocks`, `test_all_clear_returns_none`. Update FakeCache to drop the
  `get_json` usage if unused.
- `tests/test_trading_engine.py`: the engine constructor is now `TradingEngine(cache, producer,
  kraken, cfg)` where `cfg` is the defaults — unchanged call shape. Its FakeCache already returns
  `None` for `trading:runtime` (so defaults apply). All four tests must still pass.

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/test_trading_mode_switch.py tests/test_trading_kraken.py tests/test_trading_guards.py tests/test_trading_engine.py -v`
Expected: PASS
Run: `python -m pytest -o addopts="" -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add services/trading-engine/app/kraken.py services/trading-engine/app/engine.py services/trading-engine/app/guards.py tests/test_trading_mode_switch.py tests/test_trading_kraken.py tests/test_trading_guards.py tests/test_trading_engine.py
git commit -m "refactor(trading-engine): effective runtime config + dynamic Kraken mode"
```

---

## Task A6: ControlHandler — settings commands

**Files:**
- Create: `services/trading-engine/app/control.py`
- Test: `tests/test_trading_control.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_control.py
import asyncio

from cmi_common.events.control import ControlCommand, ControlCommandEvent
from tests.trading_helpers import load_module


class FakeCache:
    def __init__(self, values=None):
        self._values = dict(values or {})

    async def get_json(self, key):
        return self._values.get(key)

    async def set_json(self, key, value, ttl_seconds=60):
        self._values[key] = value


def _handler(cache):
    control = load_module("control")
    config = load_module("config")
    # kraken/engine not needed for settings commands; pass None.
    return control.ControlHandler(cache, engine=None, kraken=None,
                                  defaults=config.TradingConfig())


def _cmd(command, **payload):
    return ControlCommandEvent(command=command, payload=payload, issued_by="admin")


def test_set_mode_writes_runtime() -> None:
    cache = FakeCache()
    asyncio.run(_handler(cache).handle(_cmd(ControlCommand.SET_MODE, mode="demo")))
    assert cache._values["trading:runtime"]["mode"] == "demo"


def test_set_kill_switch() -> None:
    cache = FakeCache()
    asyncio.run(_handler(cache).handle(_cmd(ControlCommand.SET_KILL_SWITCH, enabled=False)))
    assert cache._values["trading:runtime"]["trading_enabled"] is False


def test_set_auto_trading() -> None:
    cache = FakeCache()
    asyncio.run(_handler(cache).handle(_cmd(ControlCommand.SET_AUTO_TRADING, enabled=False)))
    assert cache._values["trading:runtime"]["auto_trading_enabled"] is False


def test_set_caps_partial() -> None:
    cache = FakeCache(values={"trading:runtime": {"max_order_usd": 500.0, "max_leverage": 3.0}})
    asyncio.run(_handler(cache).handle(_cmd(ControlCommand.SET_CAPS, max_order_usd=250.0)))
    assert cache._values["trading:runtime"]["max_order_usd"] == 250.0
    assert cache._values["trading:runtime"]["max_leverage"] == 3.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading_control.py -v`
Expected: FAIL (control.py does not exist)

- [ ] **Step 3: Implement control.py (settings only for Phase A)**

```python
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
        else:
            logger.info("command %s not handled in this phase", cmd)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trading_control.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/control.py tests/test_trading_control.py
git commit -m "feat(trading-engine): control handler for settings commands"
```

---

## Task A7: Wire the control consumer into the trading-engine

**Files:**
- Modify: `services/trading-engine/app/main.py`

- [ ] **Step 1: Update main.py wiring**

In `services/trading-engine/app/main.py` `_startup`:
- After building `config` and `cache`, write runtime defaults and use a mode-provider that reads
  the runtime mode. Replace the kraken construction + engine + consumer block with:
```python
    from .runtime import RuntimeConfig
    from .control import ControlHandler
    from cmi_common.events.control import ControlCommandEvent  # noqa: F401 (topic import)

    await RuntimeConfig.write_defaults_if_absent(cache, config)

    # Cache the latest mode so the Kraken client resolves it cheaply per call.
    app.state.mode = config.mode

    async def _current_mode():
        eff = await RuntimeConfig.load(cache, config)
        app.state.mode = eff.mode
        return eff.mode

    kraken = KrakenFuturesClient(config, mode_provider=lambda: app.state.mode)
    await kraken.start()

    engine = TradingEngine(cache, producer, kraken, config)
    signals = EventConsumer(
        settings.kafka, [Topic.RISK_APPROVED], engine.handle, group_id="trading-engine",
    )
    await signals.start()

    control = ControlHandler(cache, engine=engine, kraken=kraken, defaults=config)

    async def _control_handle(event):
        await control.handle(event)
        await _current_mode()  # refresh cached mode after any settings command

    # Each engine replica must apply every command -> unique group per instance.
    import os
    replica = os.getenv("HOSTNAME", "local")
    commands = EventConsumer(
        settings.kafka, [Topic.CONTROL], _control_handle,
        group_id=f"trading-engine-control-{replica}",
    )
    await commands.start()

    reconciler = Reconciler(cache, producer, kraken)
    await reconciler.sweep()

    app.state.cache = cache
    app.state.producer = producer
    app.state.kraken = kraken
    app.state.signals = signals
    app.state.commands = commands
    app.state.reconciler = reconciler
    app.state.signals_task = asyncio.create_task(signals.run())
    app.state.commands_task = asyncio.create_task(commands.run())
    app.state.reconcile_task = asyncio.create_task(reconciler.run(config.reconcile_interval_s))
```
- Update `_shutdown` to stop both consumers and await both tasks:
```python
async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.reconciler.stop()
    await app.state.signals.stop()
    await app.state.commands.stop()
    await asyncio.gather(
        app.state.signals_task, app.state.commands_task, app.state.reconcile_task,
        return_exceptions=True,
    )
    await app.state.kraken.close()
    await app.state.producer.stop()
    await app.state.cache.close()
```

> **Note on control consumer group:** control commands must reach EVERY engine replica (broadcast),
> not be load-balanced, so each replica uses a unique consumer group (`...-control-<HOSTNAME>`).
> Settings live in Redis so all replicas converge regardless; the per-replica group only refreshes
> each instance's cached Kraken mode promptly.

- [ ] **Step 2: Verify import presence**

Run: `python -c "import pathlib; print(pathlib.Path('services/trading-engine/app/main.py').read_text().count('Topic.CONTROL') >= 1)"`
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add services/trading-engine/app/main.py
git commit -m "feat(trading-engine): consume control.commands + runtime mode refresh"
```

---

## Task A8: control-api scaffold + CommandPublisher

**Files:**
- Create: `services/control-api/pyproject.toml`, `services/control-api/app/__init__.py`
- Create: `services/control-api/app/commands.py`
- Create: `tests/control_api_helpers.py`
- Test: `tests/test_control_api_commands.py`

- [ ] **Step 1: Create package + test loader**

```toml
# services/control-api/pyproject.toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "control-api"
version = "0.1.0"
description = "Front-facing control plane for the trading bot"
requires-python = ">=3.12"
dependencies = ["cmi-common"]

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

```python
# services/control-api/app/__init__.py
```
(empty)

```python
# tests/control_api_helpers.py
"""Load the control-api `app` package under a unique name for tests."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1] / "services" / "control-api" / "app"
_PKG = "capi"
_MODULES = ["commands", "state", "auth_dep"]
_ROUTERS = ["auth", "settings", "positions", "opportunities", "orders"]


def load_app() -> types.ModuleType:
    if _PKG in sys.modules:
        return sys.modules[_PKG]
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_APP_DIR)]  # type: ignore[attr-defined]
    sys.modules[_PKG] = pkg
    routers_dir = _APP_DIR / "routers"
    if routers_dir.exists():
        rpkg = types.ModuleType(f"{_PKG}.routers")
        rpkg.__path__ = [str(routers_dir)]  # type: ignore[attr-defined]
        sys.modules[f"{_PKG}.routers"] = rpkg
    for name in _MODULES:
        _load_one(name, _APP_DIR / f"{name}.py")
    for name in _ROUTERS:
        _load_one(f"routers.{name}", routers_dir / f"{name}.py")
    return pkg


def _load_one(dotted: str, path: Path) -> None:
    if not path.exists():
        return
    spec = importlib.util.spec_from_file_location(f"{_PKG}.{dotted}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG}.{dotted}"] = module
    spec.loader.exec_module(module)


def load_module(name: str) -> types.ModuleType:
    load_app()
    return sys.modules[f"{_PKG}.{name}"]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_control_api_commands.py
import asyncio

from cmi_common.events.control import ControlCommand
from cmi_common.kafka import Topic
from tests.control_api_helpers import load_module


class FakeProducer:
    def __init__(self):
        self.published = []

    async def publish(self, topic, event):
        self.published.append((topic, event))


def test_publish_builds_control_event() -> None:
    commands = load_module("commands")
    producer = FakeProducer()
    pub = commands.CommandPublisher(producer)
    asyncio.run(pub.publish(ControlCommand.SET_MODE, {"mode": "live"}, issued_by="admin"))
    topic, ev = producer.published[0]
    assert topic == Topic.CONTROL
    assert ev.command == ControlCommand.SET_MODE
    assert ev.payload == {"mode": "live"}
    assert ev.issued_by == "admin"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_control_api_commands.py -v`
Expected: FAIL (commands.py does not exist)

- [ ] **Step 4: Implement commands.py**

```python
# services/control-api/app/commands.py
"""Publishes ControlCommandEvent to control.commands. The only writer path."""
from __future__ import annotations

from typing import Any

from cmi_common.events.control import ControlCommand, ControlCommandEvent
from cmi_common.kafka import Topic


class CommandPublisher:
    def __init__(self, producer) -> None:
        self._producer = producer

    async def publish(
        self, command: ControlCommand, payload: dict[str, Any], *, issued_by: str | None
    ) -> ControlCommandEvent:
        event = ControlCommandEvent(command=command, payload=payload, issued_by=issued_by)
        await self._producer.publish(Topic.CONTROL, event)
        return event
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_control_api_commands.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/control-api/pyproject.toml services/control-api/app/__init__.py services/control-api/app/commands.py tests/control_api_helpers.py tests/test_control_api_commands.py
git commit -m "feat(control-api): scaffold + command publisher"
```

---

## Task A9: control-api StateReader

**Files:**
- Create: `services/control-api/app/state.py`
- Test: `tests/test_control_api_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_api_state.py
import asyncio

from tests.control_api_helpers import load_module


class FakeCache:
    def __init__(self, values):
        self._values = values

    async def get_json(self, key):
        return self._values.get(key)


def test_read_settings_returns_runtime() -> None:
    state = load_module("state")
    cache = FakeCache({"trading:runtime": {"mode": "demo", "trading_enabled": True,
                                           "auto_trading_enabled": False,
                                           "max_order_usd": 500.0}})
    reader = state.StateReader(cache, db=None)
    settings = asyncio.run(reader.settings())
    assert settings["mode"] == "demo"
    assert settings["auto_trading_enabled"] is False


def test_read_settings_empty_when_absent() -> None:
    state = load_module("state")
    reader = state.StateReader(FakeCache({}), db=None)
    assert asyncio.run(reader.settings()) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_control_api_state.py -v`
Expected: FAIL (state.py does not exist)

- [ ] **Step 3: Implement state.py**

```python
# services/control-api/app/state.py
"""Read-only state: runtime settings + live positions/pending from Redis, trades from DB."""
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

> Note: `positions()`/`pending()`/`trades()` aren't unit-tested here (they need Redis client / DB);
> they're covered by the router tests (mocked reader) and the dry_run integration. `settings()` is
> the pure read that the settings router depends on.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_control_api_state.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add services/control-api/app/state.py tests/test_control_api_state.py
git commit -m "feat(control-api): read-only state reader"
```

---

## Task A10: control-api auth dependency + /auth/login

**Files:**
- Create: `services/control-api/app/auth_dep.py`
- Create: `services/control-api/app/routers/__init__.py` (empty), `services/control-api/app/routers/auth.py`
- Test: `tests/test_control_api_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_api_auth.py
import asyncio

from cmi_common.auth import decode_token
from tests.control_api_helpers import load_module


def test_login_issues_decodable_token(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.setenv("CONTROL_ADMIN_USER", "admin")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "pw")
    auth = load_module("routers.auth")
    token = asyncio.run(auth.issue_token("admin", "pw"))
    p = decode_token(token)
    assert p.sub == "admin" and p.verified is True


def test_login_rejects_bad_credentials(monkeypatch) -> None:
    monkeypatch.setenv("CONTROL_ADMIN_USER", "admin")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "pw")
    auth = load_module("routers.auth")
    import pytest
    with pytest.raises(auth.AuthError):
        asyncio.run(auth.issue_token("admin", "wrong"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_control_api_auth.py -v`
Expected: FAIL (routers/auth.py does not exist)

- [ ] **Step 3: Implement auth_dep.py and routers/auth.py**

```python
# services/control-api/app/auth_dep.py
"""FastAPI dependency enforcing a JWT bearer token (lenient in dev if no secret)."""
from __future__ import annotations

from fastapi import Header, HTTPException

from cmi_common.auth import InvalidTokenError, Principal, decode_token


async def require_principal(authorization: str | None = Header(default=None)) -> Principal:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    try:
        return decode_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
```

```python
# services/control-api/app/routers/auth.py
"""Login endpoint minting an HS256 JWT for the configured admin operator."""
from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cmi_common.auth import encode_token

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthError(Exception):
    pass


class LoginInput(BaseModel):
    username: str
    password: str


async def issue_token(username: str, password: str) -> str:
    exp_user = os.getenv("CONTROL_ADMIN_USER", "admin")
    exp_pw = os.getenv("CONTROL_ADMIN_PASSWORD", "")
    ok = hmac.compare_digest(username, exp_user) and hmac.compare_digest(password, exp_pw)
    if not ok:
        raise AuthError("invalid credentials")
    secret = os.getenv("JWT_SECRET", "")
    return encode_token({"sub": username, "role": "operator"}, secret=secret, ttl_seconds=3600)


@router.post("/login")
async def login(body: LoginInput) -> dict[str, str]:
    try:
        token = await issue_token(body.username, body.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail="invalid credentials") from exc
    return {"access_token": token, "token_type": "bearer"}
```

Also create empty `services/control-api/app/routers/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_control_api_auth.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add services/control-api/app/auth_dep.py services/control-api/app/routers/__init__.py services/control-api/app/routers/auth.py tests/test_control_api_auth.py
git commit -m "feat(control-api): JWT auth dependency + login"
```

---

## Task A11: control-api settings router

**Files:**
- Create: `services/control-api/app/routers/settings.py`
- Test: `tests/test_control_api_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_api_settings.py
import asyncio

from cmi_common.events.control import ControlCommand
from tests.control_api_helpers import load_module


class FakePublisher:
    def __init__(self):
        self.calls = []

    async def publish(self, command, payload, *, issued_by):
        self.calls.append((command, payload, issued_by))


class FakeReader:
    async def settings(self):
        return {"mode": "dry_run", "trading_enabled": True, "auto_trading_enabled": True}


def _svc():
    return load_module("routers.settings")


def test_set_mode_publishes_command() -> None:
    settings = _svc()
    pub = FakePublisher()
    svc = settings.SettingsService(pub, FakeReader())
    asyncio.run(svc.set_mode("demo", issued_by="admin"))
    assert pub.calls == [(ControlCommand.SET_MODE, {"mode": "demo"}, "admin")]


def test_set_mode_rejects_invalid() -> None:
    settings = _svc()
    svc = settings.SettingsService(FakePublisher(), FakeReader())
    import pytest
    with pytest.raises(ValueError):
        asyncio.run(svc.set_mode("banana", issued_by="admin"))


def test_set_caps_publishes_only_given_fields() -> None:
    settings = _svc()
    pub = FakePublisher()
    svc = settings.SettingsService(pub, FakeReader())
    asyncio.run(svc.set_caps({"max_order_usd": 250.0}, issued_by="admin"))
    assert pub.calls == [(ControlCommand.SET_CAPS, {"max_order_usd": 250.0}, "admin")]


def test_status_reads_reader() -> None:
    settings = _svc()
    svc = settings.SettingsService(FakePublisher(), FakeReader())
    status = asyncio.run(svc.status())
    assert status["mode"] == "dry_run"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_control_api_settings.py -v`
Expected: FAIL (settings.py does not exist)

- [ ] **Step 3: Implement settings.py**

```python
# services/control-api/app/routers/settings.py
"""Engine settings: read status + publish set_mode/kill/auto/caps commands."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from cmi_common.auth import Principal
from cmi_common.events.control import ControlCommand

from ..auth_dep import require_principal

_VALID_MODES = {"dry_run", "demo", "live"}
_CAPS_FIELDS = {"max_order_usd", "max_leverage", "max_orders_per_hour",
                "entry_timeout_s", "reconcile_interval_s"}


class SettingsService:
    def __init__(self, publisher, reader) -> None:
        self._pub = publisher
        self._reader = reader

    async def status(self) -> dict[str, Any]:
        return await self._reader.settings()

    async def set_mode(self, mode: str, *, issued_by: str | None) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(f"invalid mode {mode}")
        await self._pub.publish(ControlCommand.SET_MODE, {"mode": mode}, issued_by=issued_by)

    async def set_kill_switch(self, enabled: bool, *, issued_by: str | None) -> None:
        await self._pub.publish(
            ControlCommand.SET_KILL_SWITCH, {"enabled": enabled}, issued_by=issued_by
        )

    async def set_auto_trading(self, enabled: bool, *, issued_by: str | None) -> None:
        await self._pub.publish(
            ControlCommand.SET_AUTO_TRADING, {"enabled": enabled}, issued_by=issued_by
        )

    async def set_caps(self, caps: dict[str, Any], *, issued_by: str | None) -> None:
        fields = {k: v for k, v in caps.items() if k in _CAPS_FIELDS and v is not None}
        await self._pub.publish(ControlCommand.SET_CAPS, fields, issued_by=issued_by)


router = APIRouter(prefix="/trading", tags=["settings"])


def _svc(request: Request) -> SettingsService:
    return request.app.state.settings_service


class ModeInput(BaseModel):
    mode: str


class EnabledInput(BaseModel):
    enabled: bool


class CapsInput(BaseModel):
    max_order_usd: float | None = None
    max_leverage: float | None = None
    max_orders_per_hour: int | None = None
    entry_timeout_s: int | None = None
    reconcile_interval_s: int | None = None


@router.get("/status")
async def status(request: Request, principal: Principal = Depends(require_principal)) -> dict:
    return await _svc(request).status()


@router.post("/mode")
async def set_mode(body: ModeInput, request: Request,
                   principal: Principal = Depends(require_principal)) -> dict:
    await _svc(request).set_mode(body.mode, issued_by=principal.sub)
    return {"ok": True, "mode": body.mode}


@router.post("/kill")
async def set_kill(body: EnabledInput, request: Request,
                   principal: Principal = Depends(require_principal)) -> dict:
    await _svc(request).set_kill_switch(body.enabled, issued_by=principal.sub)
    return {"ok": True, "trading_enabled": body.enabled}


@router.post("/auto")
async def set_auto(body: EnabledInput, request: Request,
                   principal: Principal = Depends(require_principal)) -> dict:
    await _svc(request).set_auto_trading(body.enabled, issued_by=principal.sub)
    return {"ok": True, "auto_trading_enabled": body.enabled}


@router.post("/caps")
async def set_caps(body: CapsInput, request: Request,
                   principal: Principal = Depends(require_principal)) -> dict:
    await _svc(request).set_caps(body.model_dump(exclude_none=True), issued_by=principal.sub)
    return {"ok": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_control_api_settings.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/control-api/app/routers/settings.py tests/test_control_api_settings.py
git commit -m "feat(control-api): settings router (mode/kill/auto/caps)"
```

---

## Task A12: control-api entrypoint

**Files:**
- Create: `services/control-api/app/main.py`

- [ ] **Step 1: Implement main.py**

```python
# services/control-api/app/main.py
"""control-api entrypoint: front-facing control plane (JWT-protected)."""
from __future__ import annotations

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db import Database
from cmi_common.kafka import EventProducer

from .commands import CommandPublisher
from .state import StateReader
from .routers import auth as auth_router
from .routers import settings as settings_router


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    producer = EventProducer(settings.kafka)
    await producer.start()
    publisher = CommandPublisher(producer)
    reader = StateReader(cache, db=db)
    app.state.cache = cache
    app.state.db = db
    app.state.producer = producer
    app.state.settings_service = settings_router.SettingsService(publisher, reader)
    app.state.publisher = publisher
    app.state.reader = reader


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    await app.state.producer.stop()
    await app.state.cache.close()
    await app.state.db.dispose()


app = create_app("control-api", on_startup=_startup, on_shutdown=_shutdown)
app.include_router(auth_router.router)
app.include_router(settings_router.router)
```

- [ ] **Step 2: Verify import presence**

Run: `python -c "import pathlib; print(pathlib.Path('services/control-api/app/main.py').exists())"`
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add services/control-api/app/main.py
git commit -m "feat(control-api): service entrypoint"
```

---

## Task A13: docker-compose control-api service

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add the service**

Read the `api-gateway` and `trading-engine` blocks in `docker-compose.yml` to match the shared
`docker/Dockerfile` + `SERVICE_PATH` + `*service-defaults`/`*common-env` anchor style. Add a
`control-api` service mirroring `api-gateway` (it needs kafka + redis + postgres), with:
```yaml
    environment:
      JWT_SECRET: ${JWT_SECRET:-}
      CONTROL_ADMIN_USER: ${CONTROL_ADMIN_USER:-admin}
      CONTROL_ADMIN_PASSWORD: ${CONTROL_ADMIN_PASSWORD:-admin}
```
merged with `*common-env`, `build.args.SERVICE_PATH: services/control-api`, and a Traefik/host
label consistent with how other FastAPI services are exposed (copy api-gateway's exposure).

- [ ] **Step 2: Validate YAML**

Run: `python -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "chore(compose): add control-api service"
```

---

## Task A14: Frontend — 3 modes + engine settings wiring

**Files:**
- Modify: `frontend/src/lib/types/domain.ts` (TradingMode, TradingStatus, EngineSettings)
- Modify: `frontend/src/lib/api/endpoints.ts` (settingsApi + setMode)
- Modify: `frontend/src/components/trading/EngineControlCard.tsx` (mode/kill/auto)
- Modify: `frontend/src/components/settings/` (caps panel — the existing preferences/connection panel)
- Test: `frontend` typecheck (`npm run -s typecheck` or `npx tsc --noEmit` in `frontend/`)

- [ ] **Step 1: Update domain types**

In `frontend/src/lib/types/domain.ts`:
- Change `export type TradingMode = 'paper' | 'live';` to
  `export type TradingMode = 'dry_run' | 'demo' | 'live';`
- Extend `TradingStatus`:
```typescript
export interface EngineCaps {
  max_order_usd: number;
  max_leverage: number;
  max_orders_per_hour: number;
  entry_timeout_s: number;
  reconcile_interval_s: number;
}

export interface TradingStatus {
  auto_trading_enabled: boolean;
  trading_enabled: boolean;
  mode: TradingMode;
  updated_at?: string;
}

export interface EngineSettings extends EngineCaps {
  mode: TradingMode;
  trading_enabled: boolean;
  auto_trading_enabled: boolean;
}
```
- Fix any resulting type errors where `'paper'` was referenced (search the frontend for `'paper'`).

- [ ] **Step 2: Add settingsApi + update tradingApi.setMode**

In `frontend/src/lib/api/endpoints.ts`, add:
```typescript
import type { EngineSettings, EngineCaps } from '@/lib/types/domain';

export const settingsApi = {
  status: () => api.get<TradingStatus>('/trading/status').then((r) => r.data),
  setMode: (mode: TradingMode) => api.post('/trading/mode', { mode }).then((r) => r.data),
  setKill: (enabled: boolean) => api.post('/trading/kill', { enabled }).then((r) => r.data),
  setAuto: (enabled: boolean) => api.post('/trading/auto', { enabled }).then((r) => r.data),
  setCaps: (caps: Partial<EngineCaps>) => api.post('/trading/caps', caps).then((r) => r.data),
};
```
(`TradingStatus`, `TradingMode` are already imported at the top of the file.)

- [ ] **Step 3: Wire the mock BFF routes to the new shape**

Update the mock routes so `USE_MOCK=1` still works with 3 modes and the new endpoints:
- `frontend/src/app/api/mock/trading/mode/route.ts`: accept `dry_run|demo|live`.
- Add mock routes `frontend/src/app/api/mock/trading/kill/route.ts`,
  `.../trading/auto/route.ts` (already exists — keep), `.../trading/caps/route.ts` returning
  `{ ok: true }`. Match the existing mock route style in that folder.

- [ ] **Step 4: Wire EngineControlCard + caps panel**

- `frontend/src/components/trading/EngineControlCard.tsx`: render a 3-way mode selector
  (`dry_run`/`demo`/`live`), a kill-switch toggle (calls `settingsApi.setKill`), an auto-trading
  toggle (`settingsApi.setAuto`). Selecting `live` opens a confirmation dialog (double-confirm)
  before calling `settingsApi.setMode('live')`. Use the existing MUI components/patterns in that file.
- Add an engine-caps form in the settings page (extend an existing panel under
  `frontend/src/components/settings/`) with number inputs for the five caps calling
  `settingsApi.setCaps(...)`.

- [ ] **Step 5: Typecheck**

Run (in `frontend/`): `npx tsc --noEmit`
Expected: no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): 3-mode selector + engine settings controls"
```

---

## Task A15: Phase A verification

- [ ] **Step 1: Full backend suite**

Run: `python -m pytest -o addopts="" -q`
Expected: all green.

- [ ] **Step 2: No placeholders**

Run: `git grep -nE "TODO|FIXME|TBD" services/control-api services/trading-engine/app/runtime.py services/trading-engine/app/control.py`
Expected: empty.

- [ ] **Step 3: Document the Phase-A manual validation (dry_run)**

In the handoff, note: with `TRADING_MODE=dry_run`, start control-api + trading-engine + kafka +
redis; `POST /auth/login` → token; `POST /trading/mode {mode:"demo"}` → confirm `trading:runtime`
updates and `GET /trading/status` reflects it; toggle kill/auto; adjust caps. No Kraken calls in
dry_run.

- [ ] **Step 4: Commit (if any doc added)**

```bash
git add -A && git commit -m "docs(control-api): phase A manual validation notes" || true
```

---

# PHASE B — Actions sur positions (close, adjust SL/TP)

## Task B1: Engine close/adjust methods (shared guards)

**Files:**
- Modify: `services/trading-engine/app/engine.py` (add `close_position`, `adjust_sltp`)
- Test: `tests/test_trading_engine_actions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_engine_actions.py
import asyncio

from cmi_common.events.execution import ExecutionKind
from tests.trading_helpers import load_module
from tests.test_trading_engine import FakeCache, FakeProducer, FakeKraken


def _engine(cache, producer, kraken):
    mod = load_module("engine")
    config = load_module("config")
    return mod.TradingEngine(cache, producer, kraken, config.TradingConfig(trading_enabled=True))


def _seed_position(cache):
    # mirror what engine.handle stores on fill
    asyncio.run(cache.set_json("trading:position:e1", {
        "symbol": "SOL", "pair": "PF_SOLUSD", "side": "buy", "size": 2.0,
        "entry_price": 150.0, "position_size_pct": 0.04,
    }))


def test_close_position_sends_reduce_only_market() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    _seed_position(cache)
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.close_position("e1", issued_by="admin"))
    # a reduce-only market order on the opposite side (sell) was sent
    assert any(o["order_type"] == "mkt" and o["reduce_only"] and o["side"] == "sell"
               for o in kraken.orders)


def test_close_unknown_position_is_noop_reject() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.close_position("missing", issued_by="admin"))
    assert kraken.orders == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading_engine_actions.py -v`
Expected: FAIL (`close_position` not defined)

- [ ] **Step 3: Implement close_position + adjust_sltp**

Add to `TradingEngine` in `services/trading-engine/app/engine.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trading_engine_actions.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/engine.py tests/test_trading_engine_actions.py
git commit -m "feat(trading-engine): close_position + adjust_sltp actions"
```

---

## Task B2: ControlHandler dispatch for position actions

**Files:**
- Modify: `services/trading-engine/app/control.py`
- Test: `tests/test_trading_control.py` (extend)

- [ ] **Step 1: Add the failing test**

Append to `tests/test_trading_control.py`:
```python
class FakeEngine:
    def __init__(self):
        self.closed = []
        self.adjusted = []

    async def close_position(self, event_id, *, issued_by=None):
        self.closed.append((event_id, issued_by))

    async def adjust_sltp(self, event_id, *, stop_loss=None, take_profit=None, issued_by=None):
        self.adjusted.append((event_id, stop_loss, take_profit, issued_by))


def _handler_with_engine(cache, engine):
    control = load_module("control")
    config = load_module("config")
    return control.ControlHandler(cache, engine=engine, kraken=None,
                                  defaults=config.TradingConfig())


def test_close_position_dispatches_to_engine() -> None:
    engine = FakeEngine()
    asyncio.run(_handler_with_engine(FakeCache(), engine).handle(
        _cmd(ControlCommand.CLOSE_POSITION, event_id="e1")))
    assert engine.closed == [("e1", "admin")]


def test_adjust_sltp_dispatches_to_engine() -> None:
    engine = FakeEngine()
    asyncio.run(_handler_with_engine(FakeCache(), engine).handle(
        _cmd(ControlCommand.ADJUST_SLTP, event_id="e1", stop_loss=140.0)))
    assert engine.adjusted == [("e1", 140.0, None, "admin")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading_control.py -v`
Expected: FAIL (CLOSE_POSITION falls into the "not handled" branch, engine.closed empty)

- [ ] **Step 3: Extend the dispatch**

In `services/trading-engine/app/control.py` `handle`, before the final `else`, add:
```python
        elif cmd == ControlCommand.CLOSE_POSITION:
            await self._engine.close_position(p["event_id"], issued_by=event.issued_by)
        elif cmd == ControlCommand.ADJUST_SLTP:
            await self._engine.adjust_sltp(
                p["event_id"], stop_loss=p.get("stop_loss"),
                take_profit=p.get("take_profit"), issued_by=event.issued_by,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trading_control.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/control.py tests/test_trading_control.py
git commit -m "feat(trading-engine): dispatch close/adjust commands"
```

---

## Task B3: control-api positions router

**Files:**
- Create: `services/control-api/app/routers/positions.py`
- Modify: `services/control-api/app/main.py` (include router + reader on state)
- Test: `tests/test_control_api_positions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_api_positions.py
import asyncio

from cmi_common.events.control import ControlCommand
from tests.control_api_helpers import load_module
from tests.test_control_api_settings import FakePublisher


class FakeReader:
    async def positions(self):
        return [{"event_id": "e1", "symbol": "SOL", "size": 2.0}]


def _svc():
    positions = load_module("routers.positions")
    return positions, positions.PositionsService(FakePublisher(), FakeReader())


def test_list_positions() -> None:
    _mod, svc = _svc()
    assert asyncio.run(svc.list())[0]["event_id"] == "e1"


def test_close_publishes_command() -> None:
    positions = load_module("routers.positions")
    pub = FakePublisher()
    svc = positions.PositionsService(pub, FakeReader())
    asyncio.run(svc.close("e1", issued_by="admin"))
    assert pub.calls == [(ControlCommand.CLOSE_POSITION, {"event_id": "e1"}, "admin")]


def test_adjust_publishes_command() -> None:
    positions = load_module("routers.positions")
    pub = FakePublisher()
    svc = positions.PositionsService(pub, FakeReader())
    asyncio.run(svc.adjust("e1", stop_loss=140.0, take_profit=None, issued_by="admin"))
    assert pub.calls == [(ControlCommand.ADJUST_SLTP,
                          {"event_id": "e1", "stop_loss": 140.0}, "admin")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_control_api_positions.py -v`
Expected: FAIL (positions.py does not exist)

- [ ] **Step 3: Implement positions.py**

```python
# services/control-api/app/routers/positions.py
"""Positions: list live positions + publish close / adjust-SLTP commands."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from cmi_common.auth import Principal
from cmi_common.events.control import ControlCommand

from ..auth_dep import require_principal


class PositionsService:
    def __init__(self, publisher, reader) -> None:
        self._pub = publisher
        self._reader = reader

    async def list(self) -> list[dict]:
        return await self._reader.positions()

    async def close(self, event_id: str, *, issued_by: str | None) -> None:
        await self._pub.publish(
            ControlCommand.CLOSE_POSITION, {"event_id": event_id}, issued_by=issued_by
        )

    async def adjust(self, event_id: str, *, stop_loss, take_profit, issued_by: str | None) -> None:
        payload = {"event_id": event_id}
        if stop_loss is not None:
            payload["stop_loss"] = stop_loss
        if take_profit is not None:
            payload["take_profit"] = take_profit
        await self._pub.publish(ControlCommand.ADJUST_SLTP, payload, issued_by=issued_by)


router = APIRouter(prefix="/trading/positions", tags=["positions"])


def _svc(request: Request) -> PositionsService:
    return request.app.state.positions_service


class SlTpInput(BaseModel):
    stop_loss: float | None = None
    take_profit: float | None = None


@router.get("")
async def list_positions(request: Request,
                         principal: Principal = Depends(require_principal)) -> list[dict]:
    return await _svc(request).list()


@router.post("/{event_id}/close")
async def close(event_id: str, request: Request,
                principal: Principal = Depends(require_principal)) -> dict:
    await _svc(request).close(event_id, issued_by=principal.sub)
    return {"ok": True}


@router.patch("/{event_id}/sltp")
async def adjust(event_id: str, body: SlTpInput, request: Request,
                 principal: Principal = Depends(require_principal)) -> dict:
    await _svc(request).adjust(event_id, stop_loss=body.stop_loss,
                               take_profit=body.take_profit, issued_by=principal.sub)
    return {"ok": True}
```

- [ ] **Step 4: Wire into main.py**

In `services/control-api/app/main.py`:
- import `from .routers import positions as positions_router`
- in `_startup`, add `app.state.positions_service = positions_router.PositionsService(publisher, reader)`
- after the settings router include: `app.include_router(positions_router.router)`

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_control_api_positions.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add services/control-api/app/routers/positions.py services/control-api/app/main.py tests/test_control_api_positions.py
git commit -m "feat(control-api): positions router (close/adjust)"
```

---

## Task B4: Frontend — positions actions

**Files:**
- Modify: `frontend/src/lib/api/endpoints.ts` (already has close/adjust; ensure paths match)
- Modify: `frontend/src/components/trading/PositionsTable.tsx` (+ portfolio one if shared)
- Test: `npx tsc --noEmit` in `frontend/`

- [ ] **Step 1: Ensure endpoint paths match control-api**

In `frontend/src/lib/api/endpoints.ts`, confirm `tradingApi.closePosition` calls
`POST /trading/positions/{id}/close` and `adjustSlTp` calls `PATCH /trading/positions/{id}/sltp`
(they already do). No change needed unless the paths differ.

- [ ] **Step 2: Wire the buttons**

In `frontend/src/components/trading/PositionsTable.tsx`, ensure the Close button calls
`tradingApi.closePosition(row.event_id)` and the SL/TP editor calls
`tradingApi.adjustSlTp(row.event_id, {...})`, using the row's `event_id` as the id. Use the
existing mutation/query-invalidation pattern in that file.

- [ ] **Step 3: Typecheck + commit**

Run (in `frontend/`): `npx tsc --noEmit` → no errors.
```bash
git add frontend/src
git commit -m "feat(frontend): wire position close/adjust to control-api"
```

---

# PHASE C — Human-in-the-loop (auto-trading gate, approve/reject)

## Task C1: Engine auto-trading gate → pending queue

**Files:**
- Modify: `services/trading-engine/app/engine.py`
- Test: `tests/test_trading_engine_gate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_engine_gate.py
import asyncio

from cmi_common.events.execution import ExecutionKind
from tests.trading_helpers import load_module
from tests.test_trading_engine import FakeCache, FakeProducer, FakeKraken, _signal


def _engine(cache, producer, kraken):
    mod = load_module("engine")
    config = load_module("config")
    return mod.TradingEngine(cache, producer, kraken,
                             config.TradingConfig(trading_enabled=True))


def test_auto_off_queues_pending_no_orders() -> None:
    cache = FakeCache(values={"trading:runtime": {"auto_trading_enabled": False,
                                                  "trading_enabled": True}})
    producer, kraken = FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.handle(_signal()))
    assert kraken.orders == []
    (_, ev), = producer.published
    assert ev.kind == ExecutionKind.PENDING
    # pending payload stored
    assert cache._values.get("trading:pending:" + _signal().event_id) is None  # different id
    assert any(k.startswith("trading:pending:") for k in cache._values)


def test_auto_on_executes() -> None:
    cache = FakeCache(values={"trading:runtime": {"auto_trading_enabled": True,
                                                  "trading_enabled": True}})
    producer, kraken = FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.handle(_signal()))
    assert len(kraken.orders) == 3  # entry + sl + tp
```

> Note: extend the `FakeCache` in `tests/test_trading_engine.py` if needed so `client.sadd` records
> to a dict the gate test can read; the existing FakeCache already provides `client.sadd`. Store
> pending via `set_json` + `client.sadd("trading:pending", event_id)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading_engine_gate.py -v`
Expected: FAIL (no PENDING branch; orders get placed)

- [ ] **Step 3: Add the gate + a reusable `_execute` method**

In `services/trading-engine/app/engine.py`, refactor `handle` so the order-placement body (steps
5–7 in the original: entry, SL/TP, tracking, FILLED emit) becomes a method `_execute(self, event,
config)`. Then in `handle`, after loading `config`, whitelist and sizing, insert the gate:
```python
        if not config.auto_trading_enabled:
            await self._queue_pending(event)
            return
        await self._execute(event, config, size)
```
Add:
```python
    PENDING_SET = "trading:pending"

    async def _queue_pending(self, event) -> None:
        await self._cache.set_json(
            f"trading:pending:{event.event_id}", _signal_payload(event), ttl_seconds=86_400
        )
        await self._cache.client.sadd("trading:pending", event.event_id)
        await self._emit(event, ExecutionKind.PENDING)
        logger.info("PENDING %s (auto-trading off)", event.symbol)
```
where `_signal_payload(event)` is a module-level helper serialising the fields needed to replay:
```python
def _signal_payload(event) -> dict:
    return {
        "symbol": event.symbol, "direction": event.direction,
        "entry_price": event.entry_price, "stop_loss": event.stop_loss,
        "take_profit": event.take_profit, "confidence": event.confidence,
        "position_size_pct": event.position_size_pct,
        "correlation_id": event.correlation_id, "event_id": event.event_id,
    }
```
Move the existing sizing computation so `size` is available before the gate (the gate doesn't need
size, but `_execute` does — compute size before the gate, or inside `_execute`; keep it inside
`_execute` and drop `size` from the gate call). Ensure `test_trading_engine.py` still passes
(auto-trading defaults to True, so its FakeCache with no runtime key → default True → executes).

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_trading_engine_gate.py tests/test_trading_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/engine.py tests/test_trading_engine_gate.py
git commit -m "feat(trading-engine): auto-trading gate queues pending"
```

---

## Task C2: Engine approve/reject pending

**Files:**
- Modify: `services/trading-engine/app/engine.py`
- Test: `tests/test_trading_engine_approve.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_engine_approve.py
import asyncio

from cmi_common.events.execution import ExecutionKind
from tests.trading_helpers import load_module
from tests.test_trading_engine import FakeCache, FakeProducer, FakeKraken, _signal


def _engine(cache, producer, kraken):
    mod = load_module("engine")
    config = load_module("config")
    return mod.TradingEngine(cache, producer, kraken,
                             config.TradingConfig(trading_enabled=True))


def _seed_pending(cache, event_id):
    asyncio.run(cache.set_json(f"trading:pending:{event_id}", {
        "symbol": "SOL", "direction": "long", "entry_price": 150.0, "stop_loss": 142.0,
        "take_profit": 165.0, "confidence": 0.8, "position_size_pct": 0.04,
        "correlation_id": "c1", "event_id": event_id,
    }))
    asyncio.run(cache.client.sadd("trading:pending", event_id))


def test_approve_executes_and_clears_pending() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    _seed_pending(cache, "e1")
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.approve_opportunity("e1", issued_by="admin"))
    assert len(kraken.orders) == 3


def test_reject_emits_and_clears() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    _seed_pending(cache, "e1")
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.reject_opportunity("e1", reason="not now", issued_by="admin"))
    kinds = [ev.kind for _, ev in producer.published]
    assert ExecutionKind.REJECTED in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading_engine_approve.py -v`
Expected: FAIL (`approve_opportunity` not defined)

- [ ] **Step 3: Implement approve/reject**

Add to `TradingEngine`:
```python
    async def approve_opportunity(self, event_id: str, *, issued_by: str | None = None) -> None:
        payload = await self._cache.get_json(f"trading:pending:{event_id}")
        if not payload:
            logger.info("approve: %s not pending", event_id)
            return
        await self._cache.client.srem("trading:pending", event_id)
        event = RiskApprovedEvent(
            event_id=payload["event_id"], correlation_id=payload["correlation_id"],
            symbol=payload["symbol"], direction=payload["direction"],
            entry_price=payload["entry_price"], stop_loss=payload["stop_loss"],
            take_profit=payload["take_profit"], confidence=payload["confidence"],
            position_size_pct=payload["position_size_pct"],
        )
        config = await RuntimeConfig.load(self._cache, self._defaults)
        # re-run guards + sizing at approval time
        reason = await check_guards(self._cache, config)
        if reason is not None:
            await self._reject(event, reason)
            return
        size = compute_size(
            equity_usd=await self._equity(), position_size_pct=event.position_size_pct,
            entry_price=event.entry_price, max_order_usd=config.max_order_usd,
            max_leverage=config.max_leverage, contract_step=CONTRACT_STEP,
            min_contracts=MIN_CONTRACTS,
        )
        if size <= 0:
            await self._reject(event, "below_min_size")
            return
        await self._execute(event, config, size)
        logger.info("APPROVED %s by %s", event.symbol, issued_by)

    async def reject_opportunity(
        self, event_id: str, *, reason: str = "operator_reject", issued_by: str | None = None
    ) -> None:
        payload = await self._cache.get_json(f"trading:pending:{event_id}")
        if not payload:
            return
        await self._cache.client.srem("trading:pending", event_id)
        event = RiskApprovedEvent(
            event_id=payload["event_id"], correlation_id=payload["correlation_id"],
            symbol=payload["symbol"], direction=payload["direction"],
            entry_price=payload["entry_price"], stop_loss=payload["stop_loss"],
            take_profit=payload["take_profit"], confidence=payload["confidence"],
            position_size_pct=payload["position_size_pct"],
        )
        await self._emit(event, ExecutionKind.REJECTED, reason=reason)
        logger.info("operator rejected %s: %s", event.symbol, reason)
```
Ensure `_execute(event, config, size)` is the method extracted in Task C1 that places entry + SL/TP,
tracks the position, and emits SUBMITTED/FILLED. Import `RuntimeConfig`, `check_guards`,
`compute_size` at module top (they're already imported except `RuntimeConfig` — add it).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trading_engine_approve.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/engine.py tests/test_trading_engine_approve.py
git commit -m "feat(trading-engine): approve/reject pending opportunities"
```

---

## Task C3: ControlHandler dispatch for approve/reject

**Files:**
- Modify: `services/trading-engine/app/control.py`
- Test: `tests/test_trading_control.py` (extend)

- [ ] **Step 1: Add the failing test**

Append to `tests/test_trading_control.py` (extend `FakeEngine` with approve/reject recorders):
```python
def test_approve_reject_dispatch() -> None:
    control = load_module("control")
    config = load_module("config")

    class E:
        def __init__(self): self.calls = []
        async def approve_opportunity(self, event_id, *, issued_by=None):
            self.calls.append(("approve", event_id, issued_by))
        async def reject_opportunity(self, event_id, *, reason="operator_reject", issued_by=None):
            self.calls.append(("reject", event_id, reason, issued_by))

    e = E()
    h = control.ControlHandler(FakeCache(), engine=e, kraken=None, defaults=config.TradingConfig())
    asyncio.run(h.handle(_cmd(ControlCommand.APPROVE_OPPORTUNITY, event_id="e1")))
    asyncio.run(h.handle(_cmd(ControlCommand.REJECT_OPPORTUNITY, event_id="e2", reason="no")))
    assert ("approve", "e1", "admin") in e.calls
    assert ("reject", "e2", "no", "admin") in e.calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading_control.py::test_approve_reject_dispatch -v`
Expected: FAIL (falls to "not handled")

- [ ] **Step 3: Extend dispatch**

In `control.py` `handle`, before the final `else`, add:
```python
        elif cmd == ControlCommand.APPROVE_OPPORTUNITY:
            await self._engine.approve_opportunity(p["event_id"], issued_by=event.issued_by)
        elif cmd == ControlCommand.REJECT_OPPORTUNITY:
            await self._engine.reject_opportunity(
                p["event_id"], reason=p.get("reason", "operator_reject"),
                issued_by=event.issued_by,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trading_control.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/control.py tests/test_trading_control.py
git commit -m "feat(trading-engine): dispatch approve/reject commands"
```

---

## Task C4: control-api opportunities router + frontend

**Files:**
- Create: `services/control-api/app/routers/opportunities.py`
- Modify: `services/control-api/app/main.py`
- Modify: `frontend/src/components/trading/OpportunitiesSection.tsx`, `frontend/src/lib/api/endpoints.ts`
- Test: `tests/test_control_api_opportunities.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_api_opportunities.py
import asyncio

from cmi_common.events.control import ControlCommand
from tests.control_api_helpers import load_module
from tests.test_control_api_settings import FakePublisher


class FakeReader:
    async def pending(self):
        return [{"event_id": "e1", "symbol": "SOL"}]


def test_list_pending() -> None:
    opp = load_module("routers.opportunities")
    svc = opp.OpportunitiesService(FakePublisher(), FakeReader())
    assert asyncio.run(svc.list())[0]["event_id"] == "e1"


def test_approve_and_reject_publish() -> None:
    opp = load_module("routers.opportunities")
    pub = FakePublisher()
    svc = opp.OpportunitiesService(pub, FakeReader())
    asyncio.run(svc.approve("e1", issued_by="admin"))
    asyncio.run(svc.reject("e2", reason="no", issued_by="admin"))
    assert (ControlCommand.APPROVE_OPPORTUNITY, {"event_id": "e1"}, "admin") in pub.calls
    assert (ControlCommand.REJECT_OPPORTUNITY, {"event_id": "e2", "reason": "no"}, "admin") in pub.calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_control_api_opportunities.py -v`
Expected: FAIL (opportunities.py does not exist)

- [ ] **Step 3: Implement opportunities.py**

```python
# services/control-api/app/routers/opportunities.py
"""Pending opportunities: list + approve/reject (human-in-the-loop)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from cmi_common.auth import Principal
from cmi_common.events.control import ControlCommand

from ..auth_dep import require_principal


class OpportunitiesService:
    def __init__(self, publisher, reader) -> None:
        self._pub = publisher
        self._reader = reader

    async def list(self) -> list[dict]:
        return await self._reader.pending()

    async def approve(self, event_id: str, *, issued_by: str | None) -> None:
        await self._pub.publish(
            ControlCommand.APPROVE_OPPORTUNITY, {"event_id": event_id}, issued_by=issued_by
        )

    async def reject(self, event_id: str, *, reason: str | None, issued_by: str | None) -> None:
        payload = {"event_id": event_id}
        if reason:
            payload["reason"] = reason
        await self._pub.publish(ControlCommand.REJECT_OPPORTUNITY, payload, issued_by=issued_by)


router = APIRouter(prefix="/trading/opportunities", tags=["opportunities"])


def _svc(request: Request) -> OpportunitiesService:
    return request.app.state.opportunities_service


class RejectInput(BaseModel):
    reason: str | None = None


@router.get("")
async def list_pending(request: Request,
                       principal: Principal = Depends(require_principal)) -> list[dict]:
    return await _svc(request).list()


@router.post("/{event_id}/approve")
async def approve(event_id: str, request: Request,
                  principal: Principal = Depends(require_principal)) -> dict:
    await _svc(request).approve(event_id, issued_by=principal.sub)
    return {"ok": True}


@router.post("/{event_id}/reject")
async def reject(event_id: str, request: Request, body: RejectInput | None = None,
                 principal: Principal = Depends(require_principal)) -> dict:
    await _svc(request).reject(event_id, reason=(body.reason if body else None),
                               issued_by=principal.sub)
    return {"ok": True}
```

- [ ] **Step 4: Wire into main.py**

- import `from .routers import opportunities as opportunities_router`
- `app.state.opportunities_service = opportunities_router.OpportunitiesService(publisher, reader)`
- `app.include_router(opportunities_router.router)`

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_control_api_opportunities.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Frontend wiring**

`frontend/src/lib/api/endpoints.ts` already has `approveOpportunity`/`rejectOpportunity` pointing at
`/trading/opportunities/{id}/approve|reject` — confirm. In
`frontend/src/components/trading/OpportunitiesSection.tsx`, wire the approve/reject buttons to
`tradingApi.approveOpportunity(row.event_id)` / `rejectOpportunity(row.event_id, reason)` using the
existing mutation pattern. Typecheck: `npx tsc --noEmit` in `frontend/`.

- [ ] **Step 7: Commit**

```bash
git add services/control-api/app/routers/opportunities.py services/control-api/app/main.py frontend/src tests/test_control_api_opportunities.py
git commit -m "feat(control-api): opportunities approve/reject + frontend"
```

---

# PHASE D — Ordre manuel

## Task D1: Engine manual order (shared guards)

**Files:**
- Modify: `services/trading-engine/app/engine.py`
- Test: `tests/test_trading_engine_manual.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_engine_manual.py
import asyncio

from tests.trading_helpers import load_module
from tests.test_trading_engine import FakeCache, FakeProducer, FakeKraken


def _engine(cache, producer, kraken):
    mod = load_module("engine")
    config = load_module("config")
    return mod.TradingEngine(cache, producer, kraken,
                             config.TradingConfig(trading_enabled=True))


def test_manual_order_whitelisted_places_order() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.manual_order(
        symbol="SOL", side="buy", order_type="market", quantity=1.0, price=None,
        issued_by="admin"))
    assert any(o["pair"] == "PF_SOLUSD" for o in kraken.orders)


def test_manual_order_unknown_symbol_rejected() -> None:
    cache, producer, kraken = FakeCache(), FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.manual_order(
        symbol="NOTACOIN", side="buy", order_type="market", quantity=1.0, price=None,
        issued_by="admin"))
    assert kraken.orders == []


def test_manual_order_blocked_by_kill_switch() -> None:
    cache = FakeCache(values={"trading:runtime": {"trading_enabled": False}})
    producer, kraken = FakeProducer(), FakeKraken()
    engine = _engine(cache, producer, kraken)
    asyncio.run(engine.manual_order(
        symbol="SOL", side="buy", order_type="market", quantity=1.0, price=None,
        issued_by="admin"))
    assert kraken.orders == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading_engine_manual.py -v`
Expected: FAIL (`manual_order` not defined)

- [ ] **Step 3: Implement manual_order**

Add to `TradingEngine`:
```python
    async def manual_order(
        self, *, symbol: str, side: str, order_type: str, quantity: float,
        price: float | None = None, issued_by: str | None = None,
    ) -> None:
        config = await RuntimeConfig.load(self._cache, self._defaults)
        reason = await check_guards(self._cache, config)
        if reason is not None:
            logger.info("manual_order blocked: %s", reason)
            return
        if not symbols.is_whitelisted(symbol):
            logger.info("manual_order rejected: unknown symbol %s", symbol)
            return
        pair = symbols.to_kraken_pair(symbol)
        kraken_type = "mkt" if order_type == "market" else "lmt"
        # Notional cap check via sizing (quantity is explicit but must respect MAX_ORDER_USD).
        ref_price = price or 0.0
        if kraken_type == "lmt" and ref_price > 0:
            notional = quantity * ref_price
            if notional > config.max_order_usd:
                logger.info("manual_order rejected: notional %.2f over cap", notional)
                return
        await self._kraken.send_order(
            pair=pair, side=side, order_type=kraken_type, size=quantity,
            limit_price=price, cli_ord_id=f"manual-{symbol}-{side}",
        )
        logger.info("MANUAL ORDER %s %s %s qty=%s by %s", symbol, side, order_type, quantity,
                    issued_by)
```
`symbols`, `RuntimeConfig`, `check_guards` are already imported at module top.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trading_engine_manual.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/engine.py tests/test_trading_engine_manual.py
git commit -m "feat(trading-engine): guarded manual order"
```

---

## Task D2: ControlHandler dispatch + control-api orders router + frontend

**Files:**
- Modify: `services/trading-engine/app/control.py`
- Create: `services/control-api/app/routers/orders.py`
- Modify: `services/control-api/app/main.py`
- Modify: `frontend/src/components/trading/ManualOrderCard.tsx`
- Test: `tests/test_trading_control.py` (extend), `tests/test_control_api_orders.py`

- [ ] **Step 1: Add the failing engine-dispatch test**

Append to `tests/test_trading_control.py`:
```python
def test_manual_order_dispatch() -> None:
    control = load_module("control")
    config = load_module("config")

    class E:
        def __init__(self): self.calls = []
        async def manual_order(self, *, symbol, side, order_type, quantity, price=None, issued_by=None):
            self.calls.append((symbol, side, order_type, quantity, price, issued_by))

    e = E()
    h = control.ControlHandler(FakeCache(), engine=e, kraken=None, defaults=config.TradingConfig())
    asyncio.run(h.handle(_cmd(ControlCommand.MANUAL_ORDER, symbol="SOL", side="buy",
                              order_type="market", quantity=1.0)))
    assert e.calls == [("SOL", "buy", "market", 1.0, None, "admin")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading_control.py::test_manual_order_dispatch -v`
Expected: FAIL

- [ ] **Step 3: Extend control dispatch**

In `control.py` `handle`, before the final `else`, add:
```python
        elif cmd == ControlCommand.MANUAL_ORDER:
            await self._engine.manual_order(
                symbol=p["symbol"], side=p["side"], order_type=p["order_type"],
                quantity=p["quantity"], price=p.get("price"), issued_by=event.issued_by,
            )
```

- [ ] **Step 4: Run the dispatch test**

Run: `python -m pytest tests/test_trading_control.py -v`
Expected: PASS

- [ ] **Step 5: Write the control-api orders test**

```python
# tests/test_control_api_orders.py
import asyncio

from cmi_common.events.control import ControlCommand
from tests.control_api_helpers import load_module
from tests.test_control_api_settings import FakePublisher


def test_place_order_publishes_manual_command() -> None:
    orders = load_module("routers.orders")
    pub = FakePublisher()
    svc = orders.OrdersService(pub)
    asyncio.run(svc.place(
        {"symbol": "SOL", "side": "buy", "order_type": "market", "quantity": 1.0},
        issued_by="admin"))
    cmd, payload, who = pub.calls[0]
    assert cmd == ControlCommand.MANUAL_ORDER
    assert payload["symbol"] == "SOL" and payload["quantity"] == 1.0
    assert who == "admin"
```

- [ ] **Step 6: Implement orders.py**

```python
# services/control-api/app/routers/orders.py
"""Manual order placement (published as a MANUAL_ORDER command)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from cmi_common.auth import Principal
from cmi_common.events.control import ControlCommand

from ..auth_dep import require_principal


class OrdersService:
    def __init__(self, publisher) -> None:
        self._pub = publisher

    async def place(self, order: dict, *, issued_by: str | None) -> None:
        await self._pub.publish(ControlCommand.MANUAL_ORDER, order, issued_by=issued_by)


router = APIRouter(prefix="/trading/orders", tags=["orders"])


def _svc(request: Request) -> OrdersService:
    return request.app.state.orders_service


class OrderInput(BaseModel):
    symbol: str
    side: str          # buy | sell
    order_type: str    # market | limit
    quantity: float
    price: float | None = None


@router.post("")
async def place_order(body: OrderInput, request: Request,
                      principal: Principal = Depends(require_principal)) -> dict:
    await _svc(request).place(body.model_dump(exclude_none=True), issued_by=principal.sub)
    return {"ok": True}
```

- [ ] **Step 7: Wire into main.py**

- import `from .routers import orders as orders_router`
- `app.state.orders_service = orders_router.OrdersService(publisher)`
- `app.include_router(orders_router.router)`

- [ ] **Step 8: Run tests**

Run: `python -m pytest tests/test_control_api_orders.py -v`
Expected: PASS

- [ ] **Step 9: Frontend wiring**

`frontend/src/lib/api/endpoints.ts` `tradingApi.placeOrder` already posts to `/trading/orders` —
confirm. In `frontend/src/components/trading/ManualOrderCard.tsx`, wire the form submit to
`tradingApi.placeOrder({symbol, side, order_type, quantity, price})`. Typecheck: `npx tsc --noEmit`.

- [ ] **Step 10: Commit**

```bash
git add services/trading-engine/app/control.py services/control-api/app/routers/orders.py services/control-api/app/main.py frontend/src tests/test_trading_control.py tests/test_control_api_orders.py
git commit -m "feat: manual order end-to-end (engine + control-api + frontend)"
```

---

## Task D3: Final verification

- [ ] **Step 1: Full suite**

Run: `python -m pytest -o addopts="" -q`
Expected: all green.

- [ ] **Step 2: Frontend typecheck**

Run (in `frontend/`): `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: No placeholders**

Run: `git grep -nE "TODO|FIXME|TBD" services/control-api services/trading-engine/app`
Expected: empty.

- [ ] **Step 4: Compose validates**

Run: `python -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('OK')"`
Expected: `OK`.

- [ ] **Step 5: Document end-to-end demo validation**

Handoff note: in `TRADING_MODE=demo` with demo Kraken keys, exercise the full flow from the UI —
login, switch mode, toggle auto-trading off, see a signal appear as a pending opportunity, approve
it, watch entry + SL/TP on Kraken demo, adjust SL/TP, close the position, place a manual order.
Confirm `execution.events` drive the UI in real time.

---

## Self-review checklist (completed by plan author)

- **Spec coverage:** control-api dedicated service (A8–A12), Kafka+Redis runtime (A4/A5/A6/A7),
  settings mode/kill/auto/caps (A6/A11/A14), positions close/adjust (B1–B4), human-in-the-loop
  gate + approve/reject (C1–C4), manual order (D1–D2), unified guards (B1/C2/D1 all call
  check_guards / whitelist / sizing), JWT shared + login + dependency (A3/A10), 3 modes front
  (A14), DB read-only (A9), docker-compose (A13), ExecutionKind.PENDING (A3). ✅
- **Phasing:** each phase ends with working, tested software (A15, B4, C4, D3). ✅
- **Placeholders:** none — every code step has full code; frontend steps name exact files,
  components, and endpoint contracts (the React components already exist; steps are wiring with
  concrete call signatures). ✅
- **Type consistency:** `ControlCommand`/`ControlCommandEvent`/`CommandPublisher.publish(command,
  payload, issued_by=)`/`RuntimeConfig.load|write_defaults_if_absent|set_fields`/`ControlHandler`/
  `StateReader.settings|positions|pending|trades`/`SettingsService`/`PositionsService`/
  `OpportunitiesService`/`OrdersService`/`_execute(event, config, size)` used consistently across
  tasks and phases. The engine `_execute` extraction (C1) is required by C2's approve path — noted
  in both. ✅
- **Known deferral:** market-fallback-after-timeout (from the trading-engine plan) remains a V1
  simplification, untouched here; `ENTRY_TIMEOUT_S` stays a runtime cap. Flagged, not hidden.
```
