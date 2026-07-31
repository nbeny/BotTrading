# Command Center Pipeline Graph — Live Data & Per-Stage Detail — Design

**Date:** 2026-07-31
**Status:** Approved (design), pending implementation plan
**Services touched:** `api-gateway` (health collector fix, new `systems_pipeline` module,
contract manifest), `trading-engine` (missing event counters), `libs/cmi_common`
(metric name constants), `frontend` (PipelineFlow, new StageDetailDrawer, mock BFF),
Alembic migration (index only)

## Problem

The Command Center pipeline graph (`frontend/src/components/systems/PipelineFlow.tsx`,
fed by `GET /systems/overview`) shows `0/m` on every connector, permanently, as if
nothing ever traversed the pipeline. It does not. Four distinct defects:

1. **Metric name mismatch (root cause).** `services/api-gateway/app/health_collector.py:98`
   sums `events_consumed_total` and `events_produced_total`. The real counters are named
   `cmi_events_consumed_total` and `cmi_events_produced_total`
   (`libs/cmi_common/cmi_common/observability/metrics.py:8`). `metric_sum()` therefore
   always returns 0, `compute_detail()` never writes `throughput_per_min` into
   `service_health.detail`, and `read_api.py:1017` falls back to the literal `0`.
2. **trading-engine increments no counters at all.** Even with (1) fixed, the
   "Exécution" stage would report nothing.
3. **One service per stage.** `_PIPELINE` (`read_api.py:973`) maps "Collecte" to
   `collector-coingecko` alone, ignoring dexscreener, social and news.
4. **The nodes carry no business data.** A node shows a label, a sublabel and a health
   dot. Nothing says how many items actually crossed that stage, when the last one did,
   or how many were dropped there.

The consequence is worse than a cosmetic zero: an operator cannot distinguish "the
pipeline is stalled" from "the terminal does not know". Production has a real,
measured stall (24h sample: 7735 analyses → 329 escalations → 1 decision → 0 trades,
collapsing at the Sonnet budget — see `memory/pipeline-bottleneck-measured.md`), and the
graph renders it identically to a healthy pipeline whose metrics simply failed to scrape.

## Goal

Make every pipeline stage report what actually flowed through it, and let an operator
open any stage to see the real items behind the number.

- Fix the throughput plumbing end-to-end, with a regression test that cannot drift.
- Give each stage a windowed **volume** from Postgres, a **dropped** count, a
  **conversion rate** from the previous stage, and the **age of its last item**.
- Click a stage → a drawer listing the last N real items of that stage, with a
  breakdown (by source / by rejection reason / by status).
- **Never render an unknown value as `0`.** Unknown is `null` and displays as `—`.

Out of scope: Kafka broker lag, per-collector Prometheus panels, and the `kafka` /
`collectors` / `workers` / `infra` arrays of `/systems/overview` (still empty, still a
documented follow-up). No change to the write/control plane.

## Decisions taken during design

| Question | Decision |
|---|---|
| What does a stage number mean? | **Both**: windowed volume (Postgres, primary) + instantaneous throughput (Prometheus, secondary, on the connector) |
| How to see the latest data? | **Click a node → side drawer**, reusing the `DecisionTraceDrawer` pattern |
| Time window | **Shared `1h / 24h / 7d` selector**, driving the graph *and* the adjacent `FunnelPanel` |
| Where does the data come from? | **Extend `/systems/overview` + a new `/systems/stage/{id}` detail route** (rather than a parallel endpoint or client-side assembly) |

## Stage model

Every stage is durably recorded in Postgres. Window `W` below is the selected window.

| Stage id | Label | Volume (window W) | Dropped / pending | Last item |
|---|---|---|---|---|
| `collect` | Collecte | `prices`(time≥W) + `raw_content`(fetched_at≥W) | — | last price + last content, per source |
| `sentiment` | Sentiment | `raw_content`(scored_at≥W) | backlog: `scored_at IS NULL` | last scored item + its score |
| `triage` | Triage (Haiku) | `signals`(time≥W) | `escalated=false`, grouped by `block_reason` | symbol, score, confidence |
| `senior` | Analyse (Sonnet) | `signals`(time≥W AND `escalated=true`) | escalated but `decision_journal.sonnet_called=false` (budget) | last `decision_journal` row with Sonnet output |
| `decision` | Décision | `decisions`(created_at≥W) | `pipeline_rejections.stage='decision_engine'` | direction, confidence, `ai_validated` |
| `risk` | Risque | `trades`(created_at≥W) | `pipeline_rejections.stage='risk_engine'` | size, SL/TP, R:R |
| `execute` | Exécution | `trades`(created_at≥W AND status ∈ `submitted,filled,closed`) | status ∈ `failed,rejected` | status, fill price, PnL |

Three consequences worth stating explicitly:

- **Volumes reuse the funnel's definitions verbatim** (`analyses`/`escalated`/`decisions`/
  `approved`/`executed`, `services/api-gateway/app/read_api.py:1176`+). The graph and the
  `FunnelPanel` sitting next to it therefore show the same numbers over the same window.
  The graph adds `collect` and `sentiment` upstream, which the funnel does not cover.
- **The Sonnet budget gap becomes visible.** "escalated" and "Sonnet actually called" are
  two separate numbers on the `senior` stage. That gap *is* the measured production
  bottleneck and nothing in the product surfaces it today.
- **`collect` aggregates the four collectors** (coingecko, dexscreener, social, news):
  status = the most degraded of the four, throughput = their sum. The per-source split
  lives in the drawer.

`conversion_pct` on a stage is its volume relative to the previous stage's volume, `null`
whenever the previous volume is unknown or zero.

**It is deliberately `null` on `collect`, `sentiment` and `triage`.** Those stages count
different units: `collect` counts price points *and* content rows, `sentiment` counts
content rows only, `triage` counts per-token signals. A ratio between them would be a
number with no meaning, and a meaningless percentage on a debugging panel is worse than a
blank. Conversion is therefore reported only along the chain the funnel already defines
and where the unit is constant — `triage → senior → decision → risk → execute`, i.e. on
the `senior`, `decision`, `risk` and `execute` stages.

## API contract

### `GET /systems/overview?window=1h|24h|7d` (default `24h`)

`PipelineStage` gains five fields, and three existing/new numeric fields become nullable:

```ts
export interface PipelineStage {
  id: string;
  label: string;
  sublabel: string;
  status: ServiceHealth;
  throughput_per_min: number | null;   // Prometheus; null until two scrapes exist
  volume: number | null;               // windowed count from Postgres; null = unknown
  dropped: number | null;              // rejected / pending at this stage
  conversion_pct: number | null;       // survival from the previous stage
  last_at: string | null;              // ISO timestamp of the most recent item
  last_summary: string | null;         // e.g. "SOL · score 72 · escaladé"
}
```

`SystemsSnapshot` gains `pipeline_window: string` (echo of the applied window) and
`pipeline_stale: boolean` (true when the aggregate block was served from cache after a
query failure).

**`throughput_per_min` changing from `number` to `number | null` is a deliberate breaking
change** to the existing contract. It is the whole point: `0` and "not measured yet" must
stop being the same value. The `/systems` page consumes this field too and is updated in
the same change.

### `GET /systems/stage/{id}?window=1h|24h|7d&limit=20`

```ts
interface StageDetail {
  id: string;
  label: string;
  window: string;
  volume: number | null;
  dropped: number | null;
  breakdown: { key: string; count: number }[];  // by source, rejection reason, or status
  items: StageItem[];
  updated_at: string;
}

interface StageItem {
  at: string;
  symbol: string | null;
  summary: string;                                        // one readable line
  detail: Record<string, string | number | boolean | null>;
  correlation_id: string | null;   // when present, clicking the row opens DecisionTraceDrawer
}
```

`limit` is clamped to `1..50`. An unknown `id` returns **404** — not an empty payload,
which would read as "this stage processed nothing".

`breakdown` semantics per stage: `collect` → by `raw_content.source` (+ `prices.source`);
`sentiment` → scored vs backlog; `triage` → by `signals.block_reason`; `senior` → called
vs skipped by `decision_journal.skip_reason`; `decision`/`risk` → by
`pipeline_rejections.reason` (numbers collapsed the way `funnel._merge_block_reasons`
already does, so twelve spellings of one reason group as one); `execute` → by
`trades.status`.

## Query cost

`/systems/overview` is polled every 8 s by every open terminal. Grafting seven aggregates
onto it — including counts over `prices` and `raw_content` at a 7-day window — would make
that poll expensive. Two guards:

- **In-process cache, TTL 30 s, keyed by window**, covering only the aggregate block.
  Service health stays live at the 8 s cadence. On a query failure the cache serves its
  last good value with `pipeline_stale: true`; with no cached value, the fields are `null`.
- **Alembic migration adding indexes**: `raw_content(fetched_at)`, `raw_content(scored_at)`,
  `decisions(created_at)`, `trades(created_at)`. `prices` and `signals` are Timescale
  hypertables already indexed on time. The existing `ix_raw_content_unscored` is partial
  (`WHERE scored_at IS NULL`) and does not serve a `scored_at >= W` range scan.

## Root-cause fixes

Without these, everything above still renders zeros.

1. **`health_collector.py:98`** reads `cmi_events_consumed_total` /
   `cmi_events_produced_total`. Both names are exported as constants from
   `libs/cmi_common/cmi_common/observability/metrics.py` and imported by the collector, so
   producer and consumer cannot drift apart again.
2. **trading-engine gains counters**: `EVENTS_CONSUMED` on `risk.approved.events` and
   `control.commands`, `EVENTS_PRODUCED` on `execution.events`
   (`services/trading-engine/app/engine.py`, `control.py`).
3. **`_PIPELINE` maps `collect` to all four collectors**, with status = worst and
   throughput = sum.

## Error handling

The governing rule: **an unknown value is never rendered as zero.** That confusion is the
defect being fixed, so it must not be reintroduced by defensive coding.

- Aggregate query failure → cached value with `pipeline_stale: true`; no cache → `null`
  fields. Never a silent `0`.
- Prometheus scrape not yet twice-sampled → `throughput_per_min: null` → the connector
  renders `—/m`.
- Unknown stage id → 404.
- The drawer distinguishes three states: loading, load error (message + retry), and
  genuinely empty ("aucun élément sur cette fenêtre").
- A node with `volume === 0` renders "aucun élément sur 24 h" rather than a bare `0`.

## File layout

`read_api.py` is already ~1250 lines; seven aggregates plus a detail endpoint do not
belong in it.

- **New `services/api-gateway/app/systems_pipeline.py`** — the stage catalog, the windowed
  aggregate queries, the TTL cache, the pure shaping (conversion, labels, summaries) and
  the two route handlers. Mounted from `read_api.py`. This mirrors the split
  `funnel.py` / `read_api.systems_funnel` already established.
- `services/api-gateway/app/read_contract.py` — manifest entries for the new keys and the
  new `systems/stage/*` route.
- `frontend/src/components/systems/PipelineFlow.tsx` — stays purely presentational; node
  height grows from 96 to ~118 px to fit volume + last-item age.
- **New `frontend/src/components/command/StageDetailDrawer.tsx`** — the drawer, and the
  hand-off to the existing `DecisionTraceDrawer` when a row carries a `correlation_id`.
- `frontend/src/app/(app)/command/page.tsx` — owns the window state (`useState`), passes it
  to both `PipelineFlow` and `FunnelPanel`.
- `frontend/src/lib/types/systems.ts`, `frontend/src/lib/api/endpoints.ts`,
  `frontend/src/app/api/mock/*` — contract, client, mock fixtures.

## UI

```
┌────────────────────┐              ┌────────────────────┐
│ ● 03      il y a 2m│  ──129/m──▶  │ ● 04      il y a 8m│
│                    │     4.3%     │                    │
│ Triage             │              │ Analyse            │
│ Haiku              │              │ Sonnet             │
│ 7 735    ▼ 7 406   │              │ 329   ▼ 328 budget │
└────────────────────┘              └────────────────────┘
```

The connector carries two numbers — instantaneous throughput and conversion rate to the
next stage. The conversion rate is what makes the bottleneck legible at a glance. The
`1h / 24h / 7d` selector sits above the graph and drives the `FunnelPanel` as well.
Clicking a node opens `StageDetailDrawer`.

## Testing

- **Name-drift guard (the test that would have caught this bug):** increment the real
  `EVENTS_CONSUMED` / `EVENTS_PRODUCED` counters, render the actual Prometheus registry,
  feed the text through `parse_prometheus` + `compute_detail` across two samples, and
  assert `throughput_per_min > 0`. A test asserting hard-coded metric-name strings would
  have passed against the broken code and must not be written instead.
- **Pure shaping unit tests**, no database, in the style of `tests/test_funnel.py`:
  conversion arithmetic, `null` vs `0` propagation, bottleneck selection, the
  worst-of-four collector status roll-up, breakdown reason collapsing.
- **Contract parity:** `read_contract.py` + `tests/test_read_contract.py` extended to the
  new `PipelineStage` fields, `pipeline_window`, `pipeline_stale` and `systems/stage/*`.
- **Live harness:** `scripts/verify_read_live.py` asserts plausibility, not just shape —
  at least one stage must have `volume > 0` or a `last_at` inside the window, and
  `/systems/stage/{id}` must return 404 for an unknown id. This follows the precedent set
  by commit `981b08f`.
- **Cache behaviour:** a failing aggregate query yields `pipeline_stale: true` with the
  previous values, and `null` (never `0`) when no cache exists.
- **Frontend:** the repo has no JS test runner (`frontend/package.json` exposes only
  `dev`/`build`/`lint`/`typecheck`), and adding one is out of scope here. Verification is
  therefore `npm run typecheck` + `npm run lint`, plus a mock-mode check: the mock BFF
  fixtures deliberately emit one stage with `volume: null` and one with `volume: 0`, so
  `NEXT_PUBLIC_USE_MOCK=1` renders both `—` and the "aucun élément" copy on every run.
  The null-vs-zero decision lives in a single pure formatter so it stays reviewable.
