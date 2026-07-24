# Command Center + Data Explorer — Design Spec

**Date:** 2026-07-24
**Status:** Approved (design), pending implementation plan
**Scope:** Frontend (`frontend/`, Next.js). No Python backend changes required for demo/mock mode; live-mode backend endpoints are noted as gaps, not built here.

## 1. Goal

Deliver an *ultra-complete, real-time* supervision experience for the CMI trading bot so the operator can see everything happening — AI reasoning, money/execution, market & sentiment data, and technical health — at a glance and drill into the "why" of any decision. Keep it ergonomic, clean, épuré, futuristic and responsive, reusing the existing design system (Sora/Manrope/IBM Plex Mono, glass surfaces, atmospheric backdrop, azure/cyan/violet accents) established in the current build.

The four information domains (all in scope, per user):
1. **AI reasoning & decision cycle** — end-to-end trace price → sentiment → Haiku → Sonnet → risk → order, with full rationale.
2. **Live money** — positions, tick-by-tick unrealized PnL, exposure, Kraken order lifecycle.
3. **Market & sentiment** — prices, DEX liquidity, volume spikes, scored social/news feeds.
4. **Technical health & guardrails** — per-service latency, Kraken/Kafka connectivity, AI queue/cost, kill-switch & risk limits.

## 2. Information architecture changes

Chosen paradigm: **A (Command Center) + B (Decision Trace) merged** — a flagship real-time page where clicking any event opens its full causal lineage. Paradigm C (modular Bloomberg workspace) rejected as too heavy.

| # | Page | Change | Role |
|---|------|--------|------|
| 1 | **Command Center** | 🆕 new **landing page** (`/` → `/command`) | Real-time everything (A) + decision-trace drawer (B) |
| 2 | **Data Explorer** | 🆕 new page (`/data`) | Every collected item (news + social + market), sentiment scores, derived decisions, charts & stats |
| 3 | **Market Intelligence** | keep (`/market`) | Token-centric: prices, scores, sentiment aggregate, AI decisions |
| 4 | **Trading** | keep (`/trading`) | Engine control + orders + opportunities (the act plane) |
| 5 | **Capital & Risque** | 🔀 merge Portefeuille + Risque (`/capital`) | Positions, PnL, history **+** exposure, limits, alerts |
| 6 | **Paramètres** | keep (`/settings`) | Session, RBAC, engine caps |
| — | **Systèmes** | demote to secondary (`/systems`, not in main nav) | Deep infra observability, reachable from Command Center health rail |
| ~~—~~ | ~~Dashboard~~ | **removed** | Real-time role absorbed by Command Center |

Nav order (main): Command Center, Data Explorer, Market, Trading, Capital & Risque, Paramètres. Systèmes accessible via a "voir tout" link on the Command Center health rail (and a still-valid `/systems` route).

Redirects: `/` and old `/dashboard` → `/command`. Old `/portfolio` and `/risk` → `/capital` (with anchored tabs).

## 3. Command Center (`/command`)

The landing page. Dense mission-control, all real-time. Built primarily on the existing WebSocket event stream (`useWebSocket` / `useEventSubscription`, mock transport `MockEventSource` in demo mode) plus existing REST queries.

### Zones
1. **KPI ticker strip** (top, full width): portfolio value + Δ, PnL today, exposure %, open positions, mode (paper/live) + AUTO state, Kraken/Kafka connectivity dot, events/min, kill-switch state. Values from `portfolioApi.get`, `tradingApi.status`, `riskApi.exposure`, WS rate counter.
2. **Live pipeline strip** (left, upper): the 7-stage flow (reuse `PipelineFlow` styling from Systems) with live per-stage throughput derived from WS event counts by `event_type`.
3. **Live event stream** (left, lower): rolling list of the last N WS events (Price/Sentiment/Analysis/Decision/RiskApproved/OrderExecuted/Position). Each row is **clickable → opens Decision Trace drawer**. Color-coded, monospace timestamps, flash-in animation (reuse `.flash-up/.flash-down`).
4. **AI decision feed** (right column): Haiku triage + Sonnet decisions, long/short pill, confidence, truncated rationale. From WS `AnalysisEvent`/`DecisionEvent`, seeded by `marketApi.decisions`.
5. **PnL & positions live** (middle-left): open positions with live unrealized PnL and per-position sparkline. From `portfolioApi.positions`, updated by `PositionChangedEvent`/`PortfolioChangedEvent`.
6. **Market heat + guardrails** (middle-right): token heat chips (price Δ, sentiment) + exposure gauge, daily loss vs limit, kill-switch state. From `marketApi.tokens`, `riskApi.exposure`.
7. **Health rail** (bottom): compact service-health summary (reuse Systems `HealthDot`), Kraken/Kafka/Postgres/Redis status, top latencies; "voir tout →" links to `/systems`.

### Decision Trace drawer (feature B)
A right-side drawer (full-screen on mobile) opened by clicking any event. Shows the causal lineage grouped by `correlation_id`:
`① price tick → ② Haiku (score, escalate, reason) → ③ Sonnet (direction, confidence, rationale, key_risks) → ④ Risk (size %, SL, TP, RR) → ⑤ Order (fill, fee, mode)`.
Each stage is a timeline node; missing stages show "en attente / non atteint". In mock mode the client correlates events already carrying `correlation_id`; where a full chain isn't present, a `traceApi.trace(correlationId)` mock endpoint synthesizes a coherent lineage.

### Components (new, under `components/command/`)
`KpiTicker`, `LiveEventStream`, `AiDecisionFeed`, `LivePnlPanel`, `MarketHeatPanel`, `GuardrailPanel`, `HealthRail`, `DecisionTraceDrawer`. Reuse `PipelineFlow`, `HealthDot`, `Sparkline`, `MiniBar`, `SectionCard` from `components/systems/`.

## 4. Data Explorer (`/data`)

Content/source-centric firehose over everything collected. Distinct from Market (token-centric).

### Zones
1. **Stats row**: items/24h, social/news/market split, average sentiment, (top source).
2. **Charts**: (a) volume ingested per hour, stacked by category; (b) sentiment trend/distribution over time; (c) top sources bar; (d) mentions per token bar. Recharts (already a dependency).
3. **Filter bar**: category (all/social/news/market), full-text search, token, sentiment range, live toggle.
4. **Firehose table** (dense, infinite scroll + live prepend): time · source/platform · category · symbol(s) · content snippet · sentiment pill · derived decision (→ LONG/SHORT or score/—).
5. **Detail drawer** (row click): full content, sentiment breakdown (model_name, confidence, sample_size, input_kind), and the analysis/decision it fed (link into the same Decision Trace drawer when a decision exists).

### Data model
New domain type `RawContentItem` mirroring backend `raw_content` + `content_sentiment_agg`:
`id, platform, source, category ('social'|'news'|'market'), symbols[], title, snippet, url, published_at, collected_at, sentiment_score, sentiment_confidence, model_name, sample_size, derived_decision?: { direction?, opportunity_score?, correlation_id? }`.
Plus aggregates `DataStats` (counts, splits, avg sentiment, per-hour series, top sources, mentions per token).

### Components (new, under `components/data/`)
`DataStatsRow`, `IngestionVolumeChart`, `SentimentTrendChart`, `TopSourcesChart`, `MentionsChart`, `DataFilterBar`, `ContentFirehoseTable`, `ContentDetailDrawer`.

## 5. Capital & Risque (`/capital`)

Merge existing Portefeuille + Risque into one page with two MUI tabs — "Capital" and "Risque":
- **Capital**: reuse `PortfolioKpis`, `PortfolioHistoryChart`, `AllocationDonut`, `PositionsTable`, `TradesTable`.
- **Risque**: reuse `RiskKpiRow`, `AssetExposurePanel`, `RiskLimitsPanel`, `RiskAlertsPanel`.
No component rewrites — compose existing ones under one route. Old `/portfolio` and `/risk` redirect here.

## 6. Data flow & real-time

- **Demo/mock (default, `NEXT_PUBLIC_USE_MOCK=1`)**: WS via `MockEventSource`; REST via built-in BFF under `/api/mock`. New mock BFF routes:
  - `GET /api/mock/data/content` (paginated, filterable) → `RawContentItem[]`
  - `GET /api/mock/data/stats` → `DataStats`
  - `GET /api/mock/trace/:correlationId` → decision lineage
  New mock generators in `lib/mock/content.ts` and `lib/mock/trace.ts`, reusing `universe.ts` helpers.
- **Live mode**: new read endpoints on api-gateway are required and currently **do not exist** (`/data/content`, `/data/stats`, `/trace/:id`), consistent with the pre-existing read-plane gap. The frontend `endpoints.ts` gains `dataApi` and `traceApi`; live wiring is out of scope for this spec (documented gap; see `memory/web-terminal-backend-gap.md`).
- To enrich mock realism, `MockEventSource` will attach a stable `correlation_id` across a price→analysis→decision→risk→order chain for a subset of symbols so the Decision Trace shows complete lineages.

## 7. Design system continuity

Reuse everything already shipped: theme tokens, fonts, `.cmi-backdrop`, glass cards, `reveal`/`beacon`/`flow-dash` animations, `HealthDot`/`Sparkline`/`MiniBar`/`SectionCard`. New pages must feel identical in language to Systems. No new fonts or palette.

## 8. Responsiveness

- Command Center: 2-col desktop grid collapses to single column on `md↓`; ticker becomes a horizontally scrollable strip; Decision Trace drawer is a right panel on desktop, full-screen sheet on mobile.
- Data Explorer: stats 5→2 cols, charts stack, table horizontally scrollable within its card.
- Apply the same `minWidth:0` / internal-scroll discipline used to fix the Systems overflow, so no page-level horizontal scroll on mobile.

## 9. Verification

- `npm run typecheck` and `npm run lint` clean.
- Manual (Playwright) screenshot pass at 1440px and 390px for `/command` and `/data`; assert `document.scrollWidth === clientWidth` (no horizontal overflow) as done for Systems.
- Click an event on Command Center → Decision Trace drawer renders a coherent lineage.
- Data Explorer filters (category, token, sentiment, search) narrow the firehose; row click opens detail drawer.
- No console errors.

## 10. Out of scope (YAGNI)

- Modular/draggable widget workspace (paradigm C).
- User-customizable layouts or saved views.
- Real backend endpoints for live mode (documented as gaps).
- New auth/RBAC changes; existing guard/roles unchanged.
- Historical/backtesting analytics beyond the ingestion charts described.

## 11. File-level plan (summary)

**New pages:** `app/(app)/command/page.tsx`, `app/(app)/data/page.tsx`, `app/(app)/capital/page.tsx`.
**Removed/redirected:** delete `dashboard/`, redirect `/`, `/dashboard`→`/command`, `/portfolio`,`/risk`→`/capital`.
**Nav:** update `components/layout/navItems.ts` (new order, drop Dashboard, demote Systèmes).
**New components:** `components/command/*`, `components/data/*`.
**New types:** extend `lib/types/` with `RawContentItem`, `DataStats`, `DecisionTrace`.
**New mock:** `lib/mock/content.ts`, `lib/mock/trace.ts`; extend `lib/ws/mockStream.ts` with correlated chains; new BFF routes under `app/api/mock/data/*` and `app/api/mock/trace/*`.
**Endpoints:** add `dataApi`, `traceApi` to `lib/api/endpoints.ts`.
