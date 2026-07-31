# CLAUDE.md — BotTrading / CMI Platform

Event-driven crypto **market-intelligence + autonomous trading** platform. Python 3.12
microservices over a Kafka bus, PostgreSQL/TimescaleDB + Redis, Next.js control terminal,
Traefik reverse proxy. Not HFT — it detects opportunities/risks and executes guarded trades.

The README (`README.md`) covers the collection/analysis pipeline. This file drills into the
**runtime architecture** — control-api, trading-engine, websocket-gateway, frontend wiring.

## Pipeline (data → decision)

```
coingecko/dexscreener  ─► Kafka (price/volume/dex) ────────────────────┐
social/news collectors ─► Postgres raw_content ─► sentiment-service ────┤ (→ sentiment.events)
                                                                        ▼
                                          ai-worker-haiku ─► ai-worker-sonnet ─┐
                                          decision-engine ───────────────────┼─► risk-engine
                                                                              ┘   ─► risk.approved.events
                                                                                      ─► trading-engine ─► execution.events
```
`ai-worker-haiku` consumes `price/volume/dex/sentiment` (the social/news Kafka topics are now
orphaned — social/news ingestion flows through `raw_content`, not Kafka).
Collectors (coingecko, dexscreener) are stateless producers. Social + news ingestion runs
as two fan-out services — `collector-social` (Bluesky, Reddit, Mastodon, 4chan, Farcaster,
YouTube, Lens) and `collector-news` (CryptoCompare, RSS, GDELT, NewsData) — where each platform
runs its own adaptive poll loop that
self-throttles on its rate limit (learned from the API's headers) and persists raw items to
Postgres `raw_content`. Key-gated sources (Farcaster, YouTube, NewsData) activate when their
env key is set; Telegram/StockTwits/Messari/CoinGecko-news deferred (paid or session-based).
`sentiment-service` scores unscored rows from the DB, upserts
`content_sentiment_agg`, and still publishes `SentimentEvent` on Kafka for decision-engine.
`risk-engine` emits `risk.approved.events`, the input to the execution core.

## Control / execution plane (this is the important part)

Three backend surfaces sit in front of the frontend. **Keep the read vs. write split straight:**

| Service | Traefik host / port | Role | Kafka in | Kafka out | State |
|---|---|---|---|---|---|
| **api-gateway** | `api.cmi.localhost` :8000 | **READ-ONLY** REST | analysis, decision, risk, execution | — | persists → Postgres (Signal/Decision/Trade) |
| **control-api** | `control.cmi.localhost` :8000 | **WRITE / control plane** + `/auth/login` (JWT) | — | `control.commands` | reads Redis `trading:*` |
| **trading-engine** | none (background) | **execution core**, trades **Kraken Futures** | `risk.approved.events`, `control.commands` | `execution.events` | RW Redis `trading:*` |
| **websocket-gateway** | `ws.cmi.localhost` :8000, host :8080 | broadcast 10 market/decision/exec topics → WS `/ws?token=` | 10 topics | — | — |

Key rules:
- **api-gateway never writes.** It's a Kafka→Postgres persister with GET endpoints only.
- **control-api owns every bot action.** `/trading/{mode,kill,auto,caps,orders}`,
  `/trading/positions/{id}/{close,sltp}`, `/trading/opportunities/{id}/{approve,reject}` each
  publish a `ControlCommandEvent` on `control.commands`. It writes nothing directly — the
  trading-engine applies commands and mutates Redis. This is the human-in-the-loop plane.
- **trading-engine** consumes both `risk.approved.events` (autonomous) and `control.commands`
  (operator). Modes: `dry_run` (compose default) / `demo` / `live`, switched via `SET_MODE`.
  Guards: `MAX_ORDER_USD`, `MAX_LEVERAGE`, `MAX_ORDERS_PER_HOUR`. Each replica uses a unique
  control consumer group so **all** instances apply every command.

Runtime state lives in Redis under `trading:runtime` (mode/kill/auto/caps),
`trading:positions*`, `trading:pending*` — written by trading-engine, read by control-api.

## Frontend (`frontend/`, Next.js) ↔ backend

- **Two** axios clients (`frontend/src/lib/api/client.ts`), each with a base from
  `frontend/src/lib/config.ts`. `NEXT_PUBLIC_USE_MOCK=1` collapses both onto `/api/mock` (built-in
  Next.js BFF, fake data, **current default**, no Python needed).
  - `api` → `API_BASE` (`/api/gateway`): read-only (portfolio, market, risk, signals).
  - `control` → `CONTROL_BASE` (`/api/control`): auth + all `/trading/*` writes.
- `next.config.mjs` rewrites `/api/gateway/*` → `API_GATEWAY_URL` (default `api-gateway:8000`) and
  `/api/control/*` → `CONTROL_API_URL` (default `control-api:8000`, host port `8001` for dev).
- WS: `NEXT_PUBLIC_WS_URL`, default `ws://localhost:8080/ws` (dev) / `wss://ws.cmi.localhost/ws`.

**Control plane is wired for live** (control endpoints → control-api). Contract notes: login sends
`{ username: email, email, password, turnstile_token? }` (control-api reads `username`, mock reads
`email`); the hardcoded admin account mints the **`admin`** role so RBAC allows mode-switch/settings.

**Login is captcha-gated (Cloudflare Turnstile).** Two independent keys, both optional:
`NEXT_PUBLIC_TURNSTILE_SITE_KEY` (build-time, baked into the bundle by `deploy.yml` → renders the
widget) and `TURNSTILE_SECRET_KEY` (control-api env → `POST /auth/login` verifies the token against
siteverify and **403**s otherwise). Unset = off, which is the local/mock default; the gate fails
**closed** once the secret is set, so an unreachable Cloudflare denies logins rather than waving
them through. Verification lives in `services/control-api/app/turnstile.py`.

✅ **Read plane — built & contract-verified.** api-gateway now also serves the full frontend read
plane from `app/read_api.py` (mounted at root): `/portfolio*`, `/market/*`, `/risk/*`, `/data/*`,
`/trace/{cid}`, `/systems/overview?window=`, `/systems/stage/{id}` (plus the original
`/api/v1/{opportunities,decisions,trades}`).
Response shapes are locked to the TS contract by a manifest (`app/read_contract.py`) enforced by an
offline parity test (`tests/test_read_contract.py`) and a live harness (`scripts/verify_read_live.py`).
Every frontend read path has a matching route; live mode is wired. See
`memory/web-terminal-backend-gap.md` and `memory/control-api-owns-frontend-control.md`.

**Pipeline graph (`app/systems_pipeline.py`).** Each of the 7 Command Center stages reports a
windowed volume counted from **Postgres** (not Prometheus — those counters reset on restart and
round to 0 at low volume), reusing `funnel.py`'s stage predicates verbatim so the graph and the
Entonnoir can never disagree. `throughput_per_min` stays Prometheus-derived and is **nullable**.
The rule the whole panel is built on: **an unknown value is `null` and renders `—`; a measured zero
renders as words.** Conflating the two is what made the graph read as a dead pipeline for months —
`health_collector.py` scraped `events_consumed_total` while the counters are `cmi_events_consumed_total`,
so "not measured" was served as a confident `0`. Metric names are now shared constants in
`cmi_common.observability`. Aggregates sit behind a 30s TTL cache; a failed query reports stale or
unknown, never zero.

**Deployment:** `docker-compose.vps.yml` + `.github/workflows/deploy.yml` build every service to
GHCR and auto-deploy to the Hostinger VPS behind the shared Traefik on push to `master`
(single host `crypto.nbeny.fr`, REST proxied server-side by Next.js). See
`docs/superpowers/specs/2026-07-25-vps-deployment-design.md`.

## Layout & conventions

- Each `services/<svc>/` follows clean architecture: `domain/` (pure rules), `application/`
  (use-cases), `infrastructure/` (Kafka/HTTP/DB/cache), `api/` (FastAPI); DI via `deps.py`.
- Shared code in `libs/cmi_common/` — event schemas (`events/`), Kafka topic names
  (`kafka/topics.py`), db/cache/obs helpers. **All events are Pydantic v2 models.**
- Every service exposes `/health` and `/metrics`.

## Commands

```bash
make up        # docker compose build + up (full stack)
make migrate   # alembic upgrade head
make logs
make lint      # ruff + black --check + mypy
make format    # ruff --fix + black
make test      # pytest + coverage

cd frontend && npm run dev   # standalone mock terminal on :3000 (no backend)
```

Stack: FastAPI + Uvicorn, Pydantic v2, SQLAlchemy 2.0 async, aiokafka, PostgreSQL 16 +
TimescaleDB, Redis 7, Alembic, Docker Compose, Traefik v3. AI layers: Claude Haiku (triage) +
Claude Sonnet (senior analyst). Sentiment L1: HuggingFace (CryptoBERT/FinBERT/RoBERTa).
