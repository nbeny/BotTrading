# Read-Plane Contract Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mechanically enforce that api-gateway read-endpoint response shapes match the frontend TypeScript contract, via one shared manifest consumed by an offline pytest test and an extended live harness.

**Architecture:** A pure-data manifest `CONTRACT: dict[str, set[str]]` (endpoint → required response keys, derived from `frontend/src/lib/types/*.ts`) lives in the api-gateway `app` package. An offline pytest test calls each read_api endpoint with fake rows (reusing the fixtures already in `tests/test_api_gateway_read.py`) and asserts `keys ⊇ CONTRACT`. The live smoke script asserts the same against a real DB and exits non-zero on drift. Mismatches surfaced by the test are fixed to align the backend with the TS contract.

**Tech Stack:** Python 3.12, FastAPI, pytest (asyncio auto), SQLAlchemy 2.0 async.

**Spec:** `docs/superpowers/specs/2026-07-25-read-plane-contract-parity-design.md`

---

## Reference facts (read before starting)

- Frontend contract types: `frontend/src/lib/types/domain.ts` (Portfolio, Position, Trade, MarketToken, NewsItem, WorkerDecision, RiskExposure, RiskLimit, RiskAlert, PricePoint), `content.ts` (DataStats, ContentPage, DecisionTrace), `systems.ts` (SystemsSnapshot).
- Backend endpoints: `services/api-gateway/app/read_api.py` (`@router.get` at lines 277+ market, 350 data/content, 467 trace, 487 data/stats, 671 portfolio, 712 risk, 905 systems) and `routers.py`.
- Existing tests + fixtures to reuse: `tests/test_api_gateway_read.py` — `_content(...)`, `_Result`, `_FakeSession`, `_client(results)`, and the `map_*` pure functions imported from `app.read_api`. The api-gateway package is imported as `app` via the sys.path insert at the top of that file (lines 17-23).
- Existing live smoke script: `scripts/verify_read_live.py` (calls read_api functions against a real DB in one event loop, prints OK/ERR).
- `make lint` targets `libs services` only (not `tests/` or `scripts/`).

Run tests from repo root with `python -m pytest`.

---

## Task 1: Contract manifest

**Files:**
- Create: `services/api-gateway/app/read_contract.py`

- [ ] **Step 1: Create the manifest (pure data)**

Create `services/api-gateway/app/read_contract.py`:

```python
"""Read-endpoint → required response-key contract.

Single source of truth shared by the offline parity test
(tests/test_read_contract.py) and the live smoke harness
(scripts/verify_read_live.py). Each value is the set of REQUIRED top-level keys
of the endpoint's response (for list endpoints, the required keys of one item),
derived from the frontend interfaces in frontend/src/lib/types/*.ts. Optional TS
fields (declared `field?:`) are intentionally excluded.

`market/signals` is omitted on purpose: it returns a heterogeneous union of raw
event dicts with no stable shared key set, so it is smoke-checked (reachable +
list) but not key-asserted.
"""

from __future__ import annotations

CONTRACT: dict[str, set[str]] = {
    # domain.ts
    "portfolio": {
        "total_value_usd", "cash_usd", "kraken_balance_usd", "invested_usd",
        "unrealized_pnl_usd", "unrealized_pnl_pct", "realized_pnl_24h_usd",
        "pnl_24h_pct", "updated_at",
    },
    "portfolio/positions": {
        "position_id", "symbol", "direction", "quantity", "entry_price",
        "current_price", "value_usd", "unrealized_pnl_usd", "unrealized_pnl_pct",
        "stop_loss", "take_profit", "protected", "opened_at", "mode",
    },
    "portfolio/trades": {
        "trade_id", "symbol", "side", "order_type", "price", "quantity",
        "cost_usd", "fee_usd", "pnl_usd", "status", "mode", "executed_at",
    },
    "portfolio/history": {"t", "price"},
    "market/tokens": {
        "symbol", "coin_id", "name", "price_usd", "price_change_pct_24h",
        "volume_24h_usd", "liquidity_usd", "market_cap_usd", "sentiment_score",
        "opportunity_score", "is_trending", "updated_at",
    },
    "market/token": {
        "symbol", "coin_id", "name", "price_usd", "price_change_pct_24h",
        "volume_24h_usd", "liquidity_usd", "market_cap_usd", "sentiment_score",
        "opportunity_score", "is_trending", "updated_at",
    },
    "market/prices": {"t", "price"},
    "market/news": {"id", "title", "url", "source", "symbols", "sentiment",
                    "published_at"},
    "market/decisions": {
        "id", "symbol", "worker", "decision", "opportunity_score", "confidence",
        "justification", "escalated", "created_at",
    },
    "risk/exposure": {
        "total_exposure_usd", "total_exposure_pct", "max_exposure_pct",
        "by_asset", "protected_positions", "open_positions", "daily_loss_usd",
        "daily_loss_limit_usd", "updated_at",
    },
    "risk/limits": {"key", "label", "value", "max", "unit", "breached"},
    "risk/alerts": {"id", "level", "message", "created_at"},
    # content.ts
    "data/content": {"items", "total", "offset", "limit"},
    "data/stats": {
        "total_24h", "social_24h", "news_24h", "market_24h", "avg_sentiment",
        "volume_series", "sentiment_series", "top_sources", "mentions",
        "updated_at",
    },
    "trace": {"correlation_id", "symbol", "stages"},
    # systems.ts
    "systems/overview": {
        "summary", "services", "pipeline", "kafka", "collectors", "workers",
        "infra",
    },
}
```

- [ ] **Step 2: Verify it imports and is well-formed**

Run: `python -c "import sys; sys.path.insert(0, 'services/api-gateway'); from app.read_contract import CONTRACT; print(len(CONTRACT), all(isinstance(v, set) and v for v in CONTRACT.values()))"`
Expected: `18 True`

- [ ] **Step 3: Commit**

```bash
git add services/api-gateway/app/read_contract.py
git commit -m "feat(api-gateway): read-endpoint response-key contract manifest"
```

---

## Task 2: Offline parity test

**Files:**
- Create: `tests/test_read_contract.py`
- Test: itself

The test builds one representative response per endpoint by calling the read_api function with fake rows, then asserts the response satisfies `CONTRACT`. It reuses the fake-session/`_Result` pattern from `tests/test_api_gateway_read.py`. Helper `_assert_contract` centralises the dict-vs-list logic.

**CONTRACT key → read_api function** (verified to exist):

| CONTRACT key | read_api function |
|---|---|
| `portfolio` / `portfolio/positions` / `portfolio/trades` / `portfolio/history` | `portfolio` / `portfolio_positions` / `portfolio_trades` / `portfolio_history` |
| `market/tokens` / `market/token` / `market/prices` | `market_tokens` / `market_token` / `market_token_prices` |
| `market/news` / `market/decisions` | `market_news` / `market_decisions` |
| `risk/exposure` / `risk/limits` / `risk/alerts` | `risk_exposure` / `risk_limits` / `risk_alerts` |
| `data/content` / `data/stats` | `data_content` / `data_stats` |
| `trace` / `systems/overview` | `trace` / `systems_overview` |

(`market/prices` maps to `market_token_prices`, not `market_prices`.)

- [ ] **Step 1: Write the parity test scaffold + a first endpoint (failing until manifest import works)**

Create `tests/test_read_contract.py`:

```python
"""Offline contract-parity: every read endpoint's response satisfies CONTRACT.

No DB — each endpoint is driven with fake rows through a _FakeSession, mirroring
tests/test_api_gateway_read.py. Guards against backend↔frontend shape drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SVC = Path(__file__).resolve().parents[1] / "services" / "api-gateway"
if str(_SVC) not in sys.path:
    sys.path.insert(0, str(_SVC))

from app import read_api  # noqa: E402
from app.read_contract import CONTRACT  # noqa: E402


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        return self._results.pop(0)


def _assert_contract(name: str, resp) -> None:
    """resp is a dict (object endpoint) or list (collection endpoint)."""
    required = CONTRACT[name]
    if isinstance(resp, list):
        assert resp, f"{name}: expected a non-empty representative list"
        item = resp[0]
        missing = required - set(item)
        assert not missing, f"{name} item missing keys: {sorted(missing)}"
    else:
        missing = required - set(resp)
        assert not missing, f"{name} missing keys: {sorted(missing)}"
```

- [ ] **Step 2: Run to confirm the module imports (no tests yet → collected 0)**

Run: `python -m pytest tests/test_read_contract.py -q`
Expected: `no tests ran` (collection succeeds, module + CONTRACT import OK). If import fails, fix the path/import before continuing.

- [ ] **Step 3: Add the object-endpoint parity tests**

Append to `tests/test_read_contract.py`. These construct the minimal fake rows each endpoint needs. Reuse `read_api`'s own pure mappers where a row object is required (import them alongside `read_api`). Add to the import line:

```python
from app.read_api import map_token  # noqa: E402
```

Then the tests:

```python
from types import SimpleNamespace


def _price(**kw):
    base = dict(symbol="BTC", price_usd=100.0, price_change_pct_24h=1.0,
                volume_usd=5.0, liquidity_usd=9.0, market_cap_usd=1e9, time=None)
    base.update(kw)
    return SimpleNamespace(**base)


async def test_portfolio_contract() -> None:
    # portfolio derives from trades ledger + latest prices; empty ledger is a
    # valid, fully-shaped response.
    session = _FakeSession([_Result(rows=[]), _Result(rows=[])])
    resp = await read_api.portfolio(session=session)
    _assert_contract("portfolio", resp)


async def test_risk_exposure_contract() -> None:
    session = _FakeSession([_Result(rows=[]), _Result(rows=[])])
    resp = await read_api.risk_exposure(session=session)
    _assert_contract("risk/exposure", resp)


async def test_data_stats_contract() -> None:
    # raw_content scan + reader.series (hourly) + window_stats (hourly + daily).
    session = _FakeSession(
        [_Result(rows=[]), _Result(rows=[]), _Result(rows=[]), _Result(rows=[])]
    )
    resp = await read_api.data_stats(session=session)
    _assert_contract("data/stats", resp)


async def test_systems_overview_contract() -> None:
    # systems_overview reads service_health + several count/scalar queries; feed
    # enough _Result stubs. If it needs N executes, provide N empty results.
    session = _FakeSession([_Result(rows=[]) for _ in range(24)])
    resp = await read_api.systems_overview(session=session)
    _assert_contract("systems/overview", resp)
```

Note: the exact number of `_Result` stubs for `systems_overview` must match the number of `session.execute` calls it makes. Read `read_api.systems_overview` and count them; 24 is an upper-bound stub list (extra results are harmless — the session only pops what it needs). If it makes MORE than the provided stubs, the test raises `IndexError` — bump the count.

- [ ] **Step 4: Run the object-endpoint tests**

Run: `python -m pytest tests/test_read_contract.py -q`
Expected: PASS if the backend already conforms; FAIL listing missing keys if not. A failure here is an audit finding for Task 3 — do NOT weaken the test; fix the backend in Task 3.

- [ ] **Step 5: Add the collection-endpoint parity tests**

Append tests for list endpoints, seeding one representative row each. Read each mapper (`map_token`, `map_news`, `map_content`, `map_price_point`, the market/decisions and risk/limits/alerts inline builders) to see the row attributes they read, and construct a `SimpleNamespace` row with those attributes. Example for `market/tokens` (reuses `map_token`):

```python
async def test_market_tokens_contract() -> None:
    session = _FakeSession([
        _Result(rows=[_price()]),          # prices (latest per symbol)
        _Result(rows=[]),                  # tokens meta
        _Result(rows=[]),                  # signals
        _Result(rows=[]),                  # sentiments
    ])
    resp = await read_api.market_tokens(session=session)
    _assert_contract("market/tokens", resp)
```

Add analogous tests for `market/news`, `market/decisions`, `risk/limits`, `risk/alerts`, `portfolio/positions`, `portfolio/trades`, `portfolio/history`, `market/token`, `market/prices`, `data/content`, and `trace` — each providing the fake rows its endpoint reads (mirror the corresponding wiring test in `tests/test_api_gateway_read.py`, which already constructs valid fake rows for most of these; copy those fixtures). For each, call the endpoint and `_assert_contract("<name>", resp)`.

- [ ] **Step 6: Run the full parity test**

Run: `python -m pytest tests/test_read_contract.py -v`
Expected: every endpoint in `CONTRACT` (except `market/signals`, intentionally omitted) has a passing test. Any missing-key failure is recorded for Task 3.

- [ ] **Step 7: Commit**

```bash
git add tests/test_read_contract.py
git commit -m "test(api-gateway): offline read-plane contract parity test"
```

---

## Task 3: Audit fixes

**Files:**
- Modify: `services/api-gateway/app/read_api.py` (only the mappers/endpoints with mismatches) and/or `frontend/src/lib/types/*.ts`

- [ ] **Step 1: Collect the mismatches**

Run: `python -m pytest tests/test_read_contract.py -v`
For every `missing keys: [...]` failure, note the endpoint and the missing keys.

- [ ] **Step 2: Resolve each mismatch (frontend is the contract)**

For each missing key, align the backend mapper to emit it (the TS interface is the consumer contract). Add the key to the endpoint's response dict, mapping from the underlying row/derived value. If instead the TS type is stale relative to a deliberate backend decision (e.g. `DataStats.sentiment_series`, already realigned on the TS side during the sentiment rework), update the manifest entry + a one-line comment in `read_contract.py` explaining the exception, rather than changing the backend. Make the minimal change per mismatch.

- [ ] **Step 3: Re-run until green**

Run: `python -m pytest tests/test_read_contract.py tests/test_api_gateway_read.py -v`
Expected: PASS. `test_api_gateway_read.py` must stay green (no regression from the mapper edits).

- [ ] **Step 4: Commit (only if fixes were needed)**

```bash
git add services/api-gateway/app/read_api.py frontend/src/lib/types
git commit -m "fix(api-gateway): align read-plane responses to the frontend contract"
```

If Step 1 showed zero mismatches, skip this commit and note "no drift found" — the test now guards it going forward.

---

## Task 4: Extended live harness

**Files:**
- Modify: `scripts/verify_read_live.py`

- [ ] **Step 1: Rewrite the harness to cover all endpoints and assert the contract**

Replace `scripts/verify_read_live.py` with:

```python
"""Live smoke check: call every api-gateway read endpoint against the real DB in
one event loop and assert each response satisfies read_contract.CONTRACT. Seed
rows via psql first; run inside the api-gateway container. Exits non-zero if any
endpoint is unreachable or missing required keys. Not a unit test.
"""

from __future__ import annotations

import asyncio
import sys

from cmi_common import Settings
from cmi_common.db import Database

from app import read_api
from app.read_contract import CONTRACT

settings = Settings()
db = Database(settings.db)


def _check(name: str, resp) -> list[str]:
    required = CONTRACT[name]
    target = resp[0] if isinstance(resp, list) and resp else resp
    if isinstance(resp, list) and not resp:
        return []  # empty collection is shape-less but reachable; OK
    missing = required - set(target)
    return sorted(missing)


async def main() -> int:
    failures: list[str] = []
    async with db._sessionmaker() as s:  # noqa: SLF001
        calls = [
            ("portfolio", read_api.portfolio(session=s)),
            ("portfolio/positions", read_api.portfolio_positions(session=s)),
            ("portfolio/trades", read_api.portfolio_trades(limit=50, session=s)),
            ("portfolio/history", read_api.portfolio_history(range="30d", session=s)),
            ("market/tokens", read_api.market_tokens(session=s)),
            ("market/news", read_api.market_news(limit=20, session=s)),
            ("market/decisions", read_api.market_decisions(limit=30, session=s)),
            ("risk/exposure", read_api.risk_exposure(session=s)),
            ("risk/limits", read_api.risk_limits(session=s)),
            ("risk/alerts", read_api.risk_alerts(limit=30, session=s)),
            ("data/content", read_api.data_content(
                category="all", symbol=None, q=None, sentiment="all",
                limit=50, offset=0, session=s)),
            ("data/stats", read_api.data_stats(session=s)),
            ("systems/overview", read_api.systems_overview(session=s)),
        ]
        for name, coro in calls:
            try:
                res = await coro
            except Exception as e:  # noqa: BLE001
                print(f"ERR  {name}  ->  {type(e).__name__}: {e}")
                failures.append(name)
                continue
            missing = _check(name, res)
            if missing:
                print(f"FAIL {name}  ->  missing {missing}")
                failures.append(name)
            else:
                print(f"OK   {name}  ->  {str(res)[:120]}")
    await db.dispose()
    if failures:
        print(f"\n{len(failures)} endpoint(s) failed: {failures}")
        return 1
    print("\nall read endpoints conform to CONTRACT")
    return 0


sys.exit(asyncio.run(main()))
```

Note: the endpoint-name → CONTRACT-key mapping must match Task 1 (`market/decisions`, `risk/limits`, etc.). Endpoints that need a specific seeded row to be non-empty (e.g. `market/token`, `market/prices`, `trace/{cid}`) are omitted from the live loop to keep it seed-light; the offline test (Task 2) covers their shape. `market/signals` is omitted (no key contract).

- [ ] **Step 2: Verify the script parses and imports**

Run: `python -c "import ast; ast.parse(open('scripts/verify_read_live.py').read()); print('parse ok')"`
Expected: `parse ok`. (Full execution needs a live DB inside the container — document that; do not run it here.)

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_read_live.py
git commit -m "test(scripts): live harness asserts read-plane CONTRACT, exits non-zero on drift"
```

---

## Task 5: Full suite + lint

**Files:** none (verification only)

- [ ] **Step 1: Run the read-plane tests**

Run: `python -m pytest tests/test_read_contract.py tests/test_api_gateway_read.py tests/test_api_gateway_sentiment.py -v`
Expected: PASS.

- [ ] **Step 2: Lint the new/changed library+service code**

Run: `python -m ruff check services/api-gateway/app/read_contract.py services/api-gateway/app/read_api.py`
Expected: no NEW violations attributable to this change (pre-existing E501s in `read_api.py` are out of scope; confirm the count is unchanged vs before by comparing against `git show master:services/api-gateway/app/read_api.py`). Fix any new ones.

- [ ] **Step 3: Commit (if lint produced fixes)**

```bash
git add -A
git commit -m "chore(api-gateway): lint fixes for contract-parity"
```

---

## Self-review checklist (completed by plan author)

- **Spec coverage:** manifest (T1), offline parity test (T2), audit fixes (T3), extended live harness (T4), verification (T5). All spec sections mapped.
- **Placeholder scan:** `market/signals` omission is explicit and justified; `systems_overview` stub count is a documented "count the executes" instruction, not a placeholder. Collection-endpoint fixtures reference the existing, working fixtures in `tests/test_api_gateway_read.py` rather than restating each.
- **Consistency:** endpoint identifiers in `CONTRACT` (Task 1) are used verbatim by the offline test (Task 2), the harness (Task 4), and the audit (Task 3). `market/token` and `market/prices` are single-object/collection variants distinct from `market/tokens`.
- **Scope:** no docker/browser E2E; no new frontend components; control-api write plane excluded.
