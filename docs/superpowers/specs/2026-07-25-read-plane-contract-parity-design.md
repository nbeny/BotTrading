# Read-Plane Contract Parity — Design

**Date:** 2026-07-25
**Status:** Approved (design), pending implementation plan
**Services touched:** `api-gateway` (new contract module), `tests`, `scripts`

## Problem

The web terminal's live read plane already exists end-to-end: `api-gateway`
(`services/api-gateway/app/read_api.py` + `routers.py`) serves every path the
frontend contract (`frontend/src/lib/api/endpoints.ts`) calls, the WebSocket
provider has both mock and live transports, and `/auth/login` lives on
control-api. The read endpoints were DB-verified once (`scripts/verify_read_live.py`).

What is missing is a **durable guarantee that the backend response shapes match
the frontend TypeScript contract**. Today:

- Nothing checks that a backend endpoint's response keys match the TS interface
  it feeds (`domain.ts`, `content.ts`, `systems.ts`). Drift (a renamed/removed
  field) would only surface as a runtime `undefined` in the browser, in live mode.
- `scripts/verify_read_live.py` is a smoke check: it prints results but asserts
  nothing about shape, and covers only 10 of the ~20 read endpoints.

The sentiment rework already shifted one contract (`DataStats.sentiment_series`),
which is exactly the kind of change that should be caught mechanically.

## Goal

Make read-plane contract parity **mechanically enforced and auditable**, without
requiring docker or a browser:

- A single source of truth for each endpoint's required response keys.
- An offline pytest test that fails on drift (runs in `make test`, no DB).
- Fix every backend↔TS mismatch the audit surfaces.
- Extend the live harness to cover all read endpoints and assert shape (exit
  non-zero on mismatch).

Out of scope: docker-compose browser E2E; building new frontend components for
the unconsumed `/api/v1/sentiment/{windows,authors}` endpoints; the WS transports
(already exist).

## Route inventory (audited, all paths already match)

Frontend `endpoints.ts` → backend, all present:

| Frontend call | Backend route |
|---|---|
| `portfolioApi.{get,positions,trades,history}` | `/portfolio`, `/portfolio/{positions,trades,history}` (read_api) |
| `marketApi.{tokens,token,prices,news,decisions,signals}` | `/market/tokens`, `/market/tokens/{symbol}`, `/market/tokens/{symbol}/prices`, `/market/{news,decisions,signals}` |
| `riskApi.{exposure,limits,alerts}` | `/risk/{exposure,limits,alerts}` |
| `systemsApi.overview` | `/systems/overview` |
| `traceApi.get` | `/trace/{cid}` |
| `dataApi.{content,stats}` | `/data/{content,stats}` |
| `tradingApi.*` / `settingsApi.*` | control-api `/trading/*` (separate service, write plane) |

Paths match; this project verifies **response shapes**, not routing. The trading
control plane (control-api) is the write plane and out of scope for the read
contract test (it is exercised elsewhere).

## Architecture: one manifest, two consumers

```
services/api-gateway/app/read_contract.py   # CONTRACT: dict[str, set[str]]  (pure data)
        ├── tests/test_read_contract.py      # offline: mappers + fake rows, keys ⊇ CONTRACT
        └── scripts/verify_read_live.py       # live: real DB, keys ⊇ CONTRACT, exit != 0 on drift
```

The manifest lives in the `app` package so both the test (via the existing
`sys.path` insert in `tests/test_api_gateway_read.py`) and the container script
(`from app.read_contract import CONTRACT`) import the same data.

## Section 1 — Contract manifest (`services/api-gateway/app/read_contract.py`)

Pure data, no logic:

```python
CONTRACT: dict[str, set[str]] = {
    "market/tokens": {"symbol", "price_usd", ...},        # one item's keys
    "portfolio": {"total_value_usd", "cash_usd", ...},
    "risk/exposure": {...},
    "data/stats": {"total_24h", "avg_sentiment", "sentiment_series", ...},
    "systems/overview": {...},
    ...
}
```

Each value is the set of **required** top-level keys of the response (for
list-returning endpoints, the required keys of one item). Derived by reading the
TS interfaces in `frontend/src/lib/types/{domain,content,systems}.ts`. Optional TS
fields (`field?:`) are excluded from the required set. `CONTRACT` keys are the
endpoint identifiers used by both consumers.

## Section 2 — Offline parity test (`tests/test_read_contract.py`)

For each endpoint, call its read_api function with fake rows (reusing the
`_content`, `_Result`, `_FakeSession`, `map_token`, etc. patterns already in
`tests/test_api_gateway_read.py`) and assert the response satisfies the contract:

- dict response → `set(resp) >= CONTRACT[name]`.
- list response → non-empty representative item, `set(item) >= CONTRACT[name]`.

No DB, runs in `make test`. This is the anti-drift guard. Where an endpoint needs
seeded relationships (e.g. `trace`), a minimal fake row set is constructed. The
test table is data-driven off `CONTRACT` so adding an endpoint means adding one
manifest entry + one fixture.

## Section 3 — Audit + fixes

Building the manifest and test surfaces every backend↔TS mismatch. Resolution
policy: **the frontend is the consumer contract, so align the backend to the TS
type** — except a mismatch already deliberately settled (e.g.
`DataStats.sentiment_series`, realigned on the TS side during the sentiment
rework, where the backend is canonical). Each fix lands in this batch with a
one-line rationale in the commit. The unconsumed `/api/v1/sentiment/{windows,
authors}` endpoints get **no** frontend component (YAGNI — the dashboard consumes
sentiment via `/data/stats`); they are recorded as available-but-unwired.

## Section 4 — Extended live harness (`scripts/verify_read_live.py`)

Extend to cover all read endpoints (add `market/tokens/{s}`, `.../prices`,
`market/decisions`, `portfolio/{trades,history}`, `risk/{limits,alerts}`,
`data/content`, `sentiment/{windows,series,authors}`) and, for each, assert
`keys ⊇ CONTRACT[name]` instead of only printing. Accumulate failures, print a
summary, and `sys.exit(1)` if any endpoint is missing or non-conforming. Still a
single-event-loop script runnable inside the api-gateway container against a
seeded DB; not a unit test.

## Testing / verification of the deliverable

- Section 2 test runs in `make test` (offline, no DB) — the durable CI guard.
- Section 4 harness is a documented manual script (needs a seeded DB) for true
  live verification.
- No docker or browser dependency for the CI portion.

## Out of scope

- docker-compose full-stack browser E2E (environment-heavy; can be run manually
  later using the extended harness as the assertion layer).
- New frontend components for `/api/v1/sentiment/{windows,authors}`.
- WebSocket transports and the mock/live toggle (already implemented).
- The control-api write plane contract.
