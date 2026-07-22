# Design — Live read endpoints for the web terminal (api-gateway unified read API)

**Date:** 2026-07-22
**Status:** Approved (design) — pending spec review
**Related:** `memory/web-terminal-backend-gap.md`, `memory/control-api-owns-frontend-control.md`, `CLAUDE.md`

## 1. Context & goal

The web terminal (Next.js) can already control the bot in live mode (control plane wired to
control-api). The **read plane is still mocked**: the frontend calls `/portfolio*`, `/market/*`,
`/risk/*` which exist only in the mock BFF. On the backend, only `signals`, `decisions`, `trades`
(DB) and `trading:*` (Redis) hold real data; the `tokens`/`prices`/`news`/`sentiments` tables are
empty (nobody writes them), and portfolio aggregates / equity history / risk alerts have no source.

**Goal:** make every read endpoint the frontend consumes return real data, so `NEXT_PUBLIC_USE_MOCK=0`
gives a fully live terminal. Scope chosen by the user: **everything**, including aggregated portfolio.

## 2. Architecture decision — api-gateway is the unified READ API

The frontend uses a **single** read client (`api` → `/api/gateway`). Read data lives in two stores
(DB: trades/decisions/signals/market; Redis: positions/runtime/exposure). Decision: **api-gateway
reads both DB and Redis and serves all reads under `/api/gateway/*`**. control-api stays write-only.

- Preserves the clean split: **api-gateway = read, control-api = write (Kafka commands)**.
- Frontend unchanged — one read base, no per-endpoint routing.
- api-gateway gains a Redis `Cache` (already available via `cmi_common`) in `app.state`.

Rejected: splitting reads across api-gateway (DB) + control-api (Redis) → two frontend bases and
read/write coupling on control-api.

### Auth
api-gateway is currently unauthenticated. The frontend attaches the JWT bearer on every request.
Add a shared JWT verification dependency (reuse `cmi_common.auth` / the pattern in control-api's
`auth_dep.py`) to protect all read routes. `/health` and `/metrics` stay open.

### Shared read logic
control-api's `StateReader` (Redis positions/pending/runtime + DB trades) is exactly what
api-gateway needs. **Extract `StateReader` into `cmi_common`** (e.g. `cmi_common.state`) and have
both services import it, instead of duplicating Redis key knowledge.

## 3. Endpoint contract

All paths are relative to `/api/gateway`. Response shapes are the frontend `domain.ts` types.

| Endpoint | Method | Source | Notes |
|---|---|---|---|
| `/portfolio` | GET | Redis `trading:portfolio` snapshot | written by trading-engine (§6) |
| `/portfolio/positions` | GET | Redis `trading:positions*` | via shared StateReader → `Position` |
| `/portfolio/trades` | GET | DB `trades` | `?limit` → `Trade` |
| `/portfolio/history` | GET | DB `portfolio_snapshots` hypertable | `?range` → `PricePoint[]` |
| `/market/tokens` | GET | DB `tokens` + latest `prices` + `sentiments` + `signals` | `MarketToken[]` |
| `/market/tokens/{symbol}` | GET | same, one row | `MarketToken` |
| `/market/tokens/{symbol}/prices` | GET | DB `prices` (Timescale) | `?range` → `PricePoint[]` |
| `/market/news` | GET | DB `news` | `?limit` → `NewsItem[]` |
| `/market/decisions` | GET | DB `decisions` | `?limit` → `WorkerDecision[]` |
| `/market/signals` | GET | DB `signals` (+`decisions`) | `?limit` → recent events |
| `/risk/exposure` | GET | Redis positions + `trading:exposure` + caps | derived → `RiskExposure` |
| `/risk/limits` | GET | Redis `trading:runtime` caps | `RiskLimit[]` |
| `/risk/alerts` | GET | DB `risk_alerts` | `?limit` → `RiskAlert[]` (§7) |

Response mapping details (field-by-field DB/Redis → domain type) belong in the implementation plan.
Where a domain field has no backend source, the plan documents an explicit default (e.g.
`MarketToken.is_trending` from a signals-derived heuristic).

## 4. Sub-system 1 — Tier-1 read endpoints (data already present)

api-gateway routers for `/portfolio/positions`, `/portfolio/trades`, `/market/decisions`,
`/market/signals`, `/risk/limits`, `/risk/exposure`. Uses the extracted shared `StateReader` for
Redis + existing DB models. `/risk/exposure` is pure computation over positions + caps +
`trading:exposure`. No new persistence. **Highest value, ships first.**

## 5. Sub-system 2 — Market data persistence (extended persister)

Rather than making each collector write the DB (couples collectors, breaks event-driven purity),
**extend api-gateway's `Persister` + `EventConsumer`** to also consume `market.price.events`,
`market.dex.events`, `market.news.events`, `market.sentiment.events` and upsert into
`prices` / `tokens` / `news` / `sentiments`. Single write point, consistent with the existing
signal/decision/trade persistence. Unblocks `/market/tokens`, `/market/tokens/{s}/prices`,
`/market/news`. `tokens` rows are upserted on first sight of a symbol (name/coin_id from the event).

## 6. Sub-system 3 — Portfolio aggregation + history (trading-engine)

The trading-engine owns Kraken + positions + a reconcile loop (`Reconciler.run(interval_s)`), so it
computes the portfolio snapshot each reconcile cycle:

- Reads Kraken account balance (add a balance read to `KrakenFuturesClient` if absent), sums
  position values / unrealized PnL from Redis, computes realized PnL (24h) from DB trades.
- Writes the latest aggregate to Redis `trading:portfolio` (for `/portfolio`).
- Appends a row to a new TimescaleDB hypertable `portfolio_snapshots(time, total_value_usd,
  cash_usd, unrealized_pnl_usd, realized_pnl_24h_usd, mode)` (for `/portfolio/history`).

In `dry_run`/`demo` (no live Kraken account), balance falls back to a configured paper starting
balance so the terminal is populated. api-gateway only reads these — it never computes portfolio.

## 7. Sub-system 4 — Risk alerts (new topic + table)

- New event `RiskAlertEvent` in `cmi_common.events.risk` and topic `Topic.RISK_ALERT =
  "risk.alert.events"` (+ partitions entry).
- **risk-engine** emits a `RiskAlertEvent` when it blocks/flags: blacklist hit, exposure-cap breach,
  daily-loss-limit breach. Fields: `level` (info/warning/critical), `symbol?`, `message`,
  `occurred_at`.
- **api-gateway** persister consumes `risk.alert.events` → new DB table `risk_alerts(id, level,
  symbol, message, created_at)`; served by `/risk/alerts`.

## 8. Migrations (Alembic)

- New hypertable `portfolio_snapshots` (Timescale `create_hypertable` on `time`).
- New table `risk_alerts`.
- `tokens`/`prices`/`news`/`sentiments` already exist (no schema change; they just start receiving
  writes).

## 9. Testing strategy

- **Persister:** unit tests feeding PriceEvent/DexEvent/NewsEvent/SentimentEvent/RiskAlertEvent →
  assert DB upserts (follows existing `_save_signal` test pattern).
- **Read routers:** tests with a seeded DB + fake Redis (reuse `tests/control_api_helpers.py`
  fakes) → assert response shape matches `domain.ts` (mode, PnL signs, limits/exposure math).
- **Shared StateReader:** move/extend existing control-api state tests to `cmi_common`.
- **trading-engine snapshot:** test the aggregation math (paper-balance fallback, PnL sums) and the
  hypertable append.
- **risk-engine alerts:** test each trigger (blacklist / exposure breach / daily-loss) emits the
  right `RiskAlertEvent`.
- **Frontend:** `npm run typecheck` + `lint`; manual live smoke with `NEXT_PUBLIC_USE_MOCK=0`.

## 10. Build order (incremental, each step shippable)

1. **Spine + Tier 1** — api-gateway gains Redis + JWT auth + shared StateReader; Tier-1 routers.
   No new persistence. (§2, §4)
2. **Market persistence** — extended persister + market read routers. (§5)
3. **Portfolio aggregate + history** — trading-engine snapshots + hypertable + `/portfolio*`. (§6, §8)
4. **Risk alerts** — new topic/event, risk-engine producer, table, `/risk/alerts`. (§7, §8)

## 11. Out of scope

- Refactoring collectors to write the DB (persistence stays centralized in api-gateway).
- WebSocket changes (real-time feed already works).
- New frontend features — this only makes existing read screens live.
- Multi-account / multi-exchange portfolio (single Kraken account).
