# CLAUDE.md — BotTrading / CMI Platform

Event-driven crypto **market-intelligence + autonomous trading** platform. Python 3.12
microservices over a Kafka bus, PostgreSQL/TimescaleDB + Redis, Next.js control terminal,
Traefik reverse proxy. Not HFT — it detects opportunities/risks and executes guarded trades.

The README (`README.md`) covers the collection/analysis pipeline. This file focuses on the
**runtime architecture** and the parts the README is out of date on (control-api,
trading-engine, websocket-gateway, frontend wiring).

## Pipeline (data → decision)

```
collectors ─► Kafka ─► sentiment-service ─┐
              │        ai-worker-haiku  ───┼─► ai-worker-sonnet ─► decision-engine
              │        decision-engine  ───┘
              └─────────────────────────────────► risk-engine ─► risk.approved.events
```
Collectors (coingecko, dexscreener) are stateless producers. Social + news feeds run
through two resilient cascades — `collector-social` (Bluesky primary → Reddit fallback) and
`collector-news` (CryptoCompare primary → RSS floor) — each failing over on quota/error via a
Redis circuit breaker so the sentiment pipeline never dries up on free tiers.
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
`{ username: email, email, password }` (control-api reads `username`, mock reads `email`); the
hardcoded admin account mints the **`admin`** role so RBAC allows mode-switch/settings.

⚠️ **Remaining gap — read plane:** the frontend's read endpoints (`/portfolio`, `/market/*`,
`/risk/*`) don't exist on any backend yet — api-gateway only serves `/api/v1/{opportunities,
decisions,trades}`. Live mode still needs those GET endpoints built (or reads left on mock). See
`memory/web-terminal-backend-gap.md` and `memory/control-api-owns-frontend-control.md`.

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
