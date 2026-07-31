# Command Center Pipeline Graph — Live Data & Per-Stage Detail — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every stage of the Command Center pipeline graph report the volume that actually crossed it, the age of its last item and its drop count, with a click-through drawer listing the real items — and never render an unmeasured value as `0`.

**Architecture:** A new pure-plus-queries module `services/api-gateway/app/systems_pipeline.py` owns the stage catalog, the windowed Postgres aggregates (30 s TTL cache) and the two route handlers, mounted from `read_api.py` — the same split as the existing `funnel.py`. The metric-name bug in `health_collector.py` is fixed at its source by exporting the counter names as constants from `cmi_common`, and `trading-engine` gains the counters it never had. On the frontend, `PipelineFlow` stays presentational, a new `StageDetailDrawer` shows per-stage items, and a shared `1h/24h/7d` selector drives both the graph and `FunnelPanel`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, prometheus_client, Alembic, pytest; Next.js 14 + MUI 6 + TanStack Query.

**Spec:** `docs/superpowers/specs/2026-07-31-pipeline-graph-live-data-design.md`

---

## File Structure

**Backend — created**
- `services/api-gateway/app/systems_pipeline.py` — stage catalog, pure shaping, windowed aggregates, TTL cache, the two handlers. Everything about "what flowed through a stage" lives here and nowhere else.
- `migrations/alembic/versions/0015_pipeline_stage_indexes.py` — the four range-scan indexes.
- `tests/test_health_collector_metrics.py` — the name-drift guard.
- `tests/test_systems_pipeline.py` — pure shaping + cache behaviour.
- `tests/test_trading_engine_metrics.py` — the counters trading-engine was missing.

**Backend — modified**
- `libs/cmi_common/cmi_common/observability/metrics.py` + `__init__.py` — metric names become exported constants.
- `services/api-gateway/app/health_collector.py:98` — read the real names.
- `services/api-gateway/app/read_api.py` — `assemble_systems_snapshot` delegates the pipeline to the new module; `/systems/overview` takes `?window=`; `/systems/stage/{id}` mounted.
- `services/api-gateway/app/funnel.py` — expose `collapse_reason()` so rejection grouping is shared, not duplicated.
- `services/api-gateway/app/read_contract.py` + `tests/test_read_contract.py` — manifest entries.
- `services/trading-engine/app/engine.py`, `control.py` — event counters.
- `libs/cmi_common/cmi_common/db/models.py` — index declarations matching the migration.
- `scripts/verify_read_live.py` — plausibility assertions.

**Frontend — created**
- `frontend/src/components/command/StageDetailDrawer.tsx`
- `frontend/src/components/command/WindowSelector.tsx`
- `frontend/src/app/api/mock/systems/stage/[id]/route.ts`

**Frontend — modified**
- `frontend/src/lib/types/systems.ts`, `frontend/src/lib/api/endpoints.ts`, `frontend/src/lib/format.ts`
- `frontend/src/components/systems/PipelineFlow.tsx`, `frontend/src/components/systems/ServiceGrid.tsx`
- `frontend/src/components/command/FunnelPanel.tsx`, `frontend/src/app/(app)/command/page.tsx`
- `frontend/src/lib/mock/systems.ts`, `frontend/src/app/api/mock/systems/overview/route.ts`

---

### Task 1: Fix the metric-name mismatch at its source

This is the root cause: `health_collector.py:98` sums `events_consumed_total`, the counters are named `cmi_events_consumed_total`. Fixing the string alone would leave the two free to drift again, so the names become shared constants.

**Files:**
- Modify: `libs/cmi_common/cmi_common/observability/metrics.py:7-17`
- Modify: `libs/cmi_common/cmi_common/observability/__init__.py`
- Modify: `services/api-gateway/app/health_collector.py:98`
- Test: `tests/test_health_collector_metrics.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_health_collector_metrics.py`:

```python
"""Guard against metric-name drift between producer and consumer.

The Command Center graph reported `0/m` on every connector for one reason: the
health collector summed `events_consumed_total` while the counters are named
`cmi_events_consumed_total`. A test asserting hard-coded strings would have
passed against that broken code. This one renders the *real* Prometheus registry
and requires a non-zero rate to come out the other end.
"""

from __future__ import annotations

from prometheus_client import REGISTRY, generate_latest

from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED
from service_modules import load_service_module

health_collector = load_service_module("api-gateway", "health_collector")


def _scrape() -> dict:
    return health_collector.parse_prometheus(generate_latest(REGISTRY).decode())


def test_throughput_is_derived_from_the_real_counter_names() -> None:
    EVENTS_CONSUMED.labels("probe-svc", "topic", "type").inc(10)
    EVENTS_PRODUCED.labels("probe-svc", "topic", "type").inc(5)
    first, sample = health_collector.compute_detail(_scrape(), None, 0.0)

    # One sample is not a rate: reporting anything here would be an invention.
    assert "throughput_per_min" not in first

    EVENTS_CONSUMED.labels("probe-svc", "topic", "type").inc(40)
    EVENTS_PRODUCED.labels("probe-svc", "topic", "type").inc(20)
    second, _ = health_collector.compute_detail(_scrape(), sample, 60.0)

    # 60 new events over 60s == 60/min.
    assert second["throughput_per_min"] == 60


def test_counter_name_constants_match_the_exposed_samples() -> None:
    from cmi_common.observability import (
        EVENTS_CONSUMED_METRIC,
        EVENTS_PRODUCED_METRIC,
    )

    scraped = _scrape()
    assert EVENTS_CONSUMED_METRIC in scraped
    assert EVENTS_PRODUCED_METRIC in scraped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_health_collector_metrics.py -v`
Expected: FAIL — `ImportError: cannot import name 'EVENTS_CONSUMED_METRIC'`, and `test_throughput_is_derived_from_the_real_counter_names` fails on `assert second["throughput_per_min"] == 60` because the key is absent (the sum is 0, so `compute_detail` computes a rate of 0 — the exact bug).

- [ ] **Step 3: Export the names as constants**

In `libs/cmi_common/cmi_common/observability/metrics.py`, replace lines 7-17:

```python
# Events consumed / produced, labeled by service + topic + event type.
# The exposed sample names are constants because a second copy of these strings
# is what broke the Command Center graph: the health collector scraped
# `events_consumed_total` and always read 0.
EVENTS_CONSUMED_METRIC = "cmi_events_consumed_total"
EVENTS_PRODUCED_METRIC = "cmi_events_produced_total"

EVENTS_CONSUMED = Counter(
    EVENTS_CONSUMED_METRIC,
    "Number of events consumed",
    ["service", "topic", "event_type"],
)
EVENTS_PRODUCED = Counter(
    EVENTS_PRODUCED_METRIC,
    "Number of events produced",
    ["service", "topic", "event_type"],
)
```

In `libs/cmi_common/cmi_common/observability/__init__.py`, add both constants to the import from `.metrics` and to `__all__`, next to the existing `EVENTS_CONSUMED` / `EVENTS_PRODUCED` entries.

- [ ] **Step 4: Fix the health collector**

In `services/api-gateway/app/health_collector.py`, add to the imports at line 20:

```python
from cmi_common.observability.metrics import (
    EVENTS_CONSUMED_METRIC,
    EVENTS_PRODUCED_METRIC,
)
```

and replace line 98:

```python
    events = metric_sum(parsed, EVENTS_CONSUMED_METRIC) + metric_sum(
        parsed, EVENTS_PRODUCED_METRIC
    )
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_health_collector_metrics.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add libs/cmi_common/cmi_common/observability/metrics.py libs/cmi_common/cmi_common/observability/__init__.py services/api-gateway/app/health_collector.py tests/test_health_collector_metrics.py
git commit -m "fix(systems): scrape the real cmi_events_* counter names

The health collector summed `events_consumed_total`; the counters are named
`cmi_events_consumed_total`, so throughput_per_min was never written and the
Command Center graph showed 0/m everywhere. The names are now shared constants."
```

---

### Task 2: Give trading-engine the event counters it never had

Even with Task 1 done, the "Exécution" stage reports nothing: `services/trading-engine/` contains no `EVENTS_CONSUMED` / `EVENTS_PRODUCED` call at all.

**Files:**
- Modify: `services/trading-engine/app/engine.py:1-50,278-299`
- Modify: `services/trading-engine/app/control.py:32`
- Test: `tests/test_trading_engine_metrics.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_trading_engine_metrics.py`:

```python
"""trading-engine must report what it consumes and produces.

Without these counters the `execute` stage of the Command Center graph has no
throughput to show, and an operator cannot tell a stopped executor from a quiet
one.
"""

from __future__ import annotations

from prometheus_client import REGISTRY

from cmi_common.events import RiskApprovedEvent
from cmi_common.kafka import Topic
from service_modules import load_service_module

engine_mod = load_service_module("trading-engine", "engine")


def _value(metric: str, **labels) -> float:
    return REGISTRY.get_sample_value(metric, labels) or 0.0


def _risk_event() -> RiskApprovedEvent:
    return RiskApprovedEvent(
        symbol="BTC",
        direction="long",
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=120.0,
        confidence=0.9,
        position_size_pct=0.05,
        correlation_id="cid-1",
    )


class _Cache:
    def __init__(self) -> None:
        self.store: dict = {}

    async def get_json(self, key):
        return self.store.get(key)


async def test_consumed_counter_increments_on_a_duplicate_event() -> None:
    """A duplicate is still an event the engine consumed: counting it after the
    idempotency check would under-report exactly when redelivery spikes."""
    cache = _Cache()
    event = _risk_event()
    cache.store[engine_mod.SUBMITTED_KEY.format(event_id=event.event_id)] = {"ok": True}
    eng = engine_mod.TradingEngine(cache, None, None, None)

    labels = dict(
        service="trading-engine",
        topic=Topic.RISK_APPROVED.value,
        event_type=event.event_type,
    )
    before = _value("cmi_events_consumed_total", **labels)
    await eng.handle(event)
    assert _value("cmi_events_consumed_total", **labels) == before + 1
```

`pyproject.toml` sets `asyncio_mode = "auto"`, so a plain `async def test_…` runs as-is — no marker needed.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trading_engine_metrics.py -v`
Expected: FAIL — the counter stays at `before`, because nothing in `engine.py` increments it.

- [ ] **Step 3: Add the counters**

In `services/trading-engine/app/engine.py`, add after line 10:

```python
from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED
```

and after line 18 (`logger = ...`):

```python
SERVICE = "trading-engine"
```

In `handle()`, immediately after the `isinstance` guard at line 50 and **before** the idempotency check:

```python
        # Counted before the idempotency check: a redelivery is still an event
        # this service consumed, and dropping it would under-report precisely
        # when Kafka is redelivering most.
        EVENTS_CONSUMED.labels(
            SERVICE, Topic.RISK_APPROVED.value, event.event_type
        ).inc()
```

In `_emit()`, replace line 299:

```python
        await self._producer.publish(Topic.EXECUTION, ev)
        EVENTS_PRODUCED.labels(SERVICE, Topic.EXECUTION.value, ev.event_type).inc()
```

In `services/trading-engine/app/control.py`, add the import

```python
from cmi_common.observability import EVENTS_CONSUMED
from cmi_common.kafka import Topic
```

(skip either import if already present) and as the first statement inside `handle()` at line 32:

```python
        EVENTS_CONSUMED.labels("trading-engine", Topic.CONTROL.value, event.event_type).inc()
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/test_trading_engine_metrics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/trading-engine/app/engine.py services/trading-engine/app/control.py tests/test_trading_engine_metrics.py
git commit -m "feat(trading-engine): count consumed and produced events

The execute stage of the pipeline graph had no throughput to report because
this service incremented no counters at all."
```

---

### Task 3: Pure stage shaping in a new `systems_pipeline` module

Pure first, no database — the same split as `funnel.py` / `read_api.systems_funnel`.

**Files:**
- Create: `services/api-gateway/app/systems_pipeline.py`
- Test: `tests/test_systems_pipeline.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_systems_pipeline.py`:

```python
"""Pipeline stage shaping: pure, no database.

The panel exists because the graph reported 0/m everywhere while the pipeline
was alive. The cases below pin the one rule that matters: an unmeasured value
stays None all the way out, and is never smoothed into a zero.
"""

from __future__ import annotations

from datetime import datetime, timezone

from service_modules import load_service_module

sp = load_service_module("api-gateway", "systems_pipeline")

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def _services(**overrides):
    base = {
        s: {"status": "healthy", "throughput_per_min": 10}
        for s in (
            "collector-coingecko", "collector-dexscreener", "collector-social",
            "collector-news", "sentiment-service", "ai-worker-haiku",
            "ai-worker-sonnet", "decision-engine", "risk-engine",
            "trading-engine",
        )
    }
    base.update(overrides)
    return base


def test_stages_are_ordered_and_complete() -> None:
    out = sp.build_pipeline_stages({}, _services())
    assert [s["id"] for s in out] == [
        "collect", "sentiment", "triage", "senior", "decision", "risk", "execute",
    ]


def test_unknown_volume_stays_none_and_never_becomes_zero() -> None:
    out = sp.build_pipeline_stages({}, _services())
    assert all(s["volume"] is None for s in out)
    assert all(s["dropped"] is None for s in out)
    assert all(s["conversion_pct"] is None for s in out)
    assert all(s["last_at"] is None for s in out)


def test_conversion_is_reported_only_where_the_unit_is_constant() -> None:
    counts = {
        "collect": sp.StageCounts(volume=1000),
        "sentiment": sp.StageCounts(volume=800),
        "triage": sp.StageCounts(volume=400),
        "senior": sp.StageCounts(volume=100),
        "decision": sp.StageCounts(volume=50),
        "risk": sp.StageCounts(volume=25),
        "execute": sp.StageCounts(volume=5),
    }
    by = {s["id"]: s for s in sp.build_pipeline_stages(counts, _services())}
    # collect counts price points *and* content rows, sentiment counts content
    # rows, triage counts per-token signals: ratios across them mean nothing.
    assert by["collect"]["conversion_pct"] is None
    assert by["sentiment"]["conversion_pct"] is None
    assert by["triage"]["conversion_pct"] is None
    assert by["senior"]["conversion_pct"] == 25.0
    assert by["decision"]["conversion_pct"] == 50.0
    assert by["risk"]["conversion_pct"] == 50.0
    assert by["execute"]["conversion_pct"] == 20.0


def test_zero_upstream_reports_no_conversion_rather_than_dividing() -> None:
    counts = {"triage": sp.StageCounts(volume=0), "senior": sp.StageCounts(volume=0)}
    by = {s["id"]: s for s in sp.build_pipeline_stages(counts, _services())}
    assert by["senior"]["conversion_pct"] is None


def test_collect_status_is_the_worst_of_its_four_collectors() -> None:
    svc = _services(**{
        "collector-social": {"status": "down", "throughput_per_min": 0},
        "collector-news": {"status": "degraded", "throughput_per_min": 2},
    })
    by = {s["id"]: s for s in sp.build_pipeline_stages({}, svc)}
    assert by["collect"]["status"] == "down"


def test_collect_throughput_sums_its_collectors() -> None:
    by = {s["id"]: s for s in sp.build_pipeline_stages({}, _services())}
    assert by["collect"]["throughput_per_min"] == 40


def test_throughput_is_none_when_no_collector_has_been_measured() -> None:
    svc = _services(**{
        s: {"status": "healthy", "throughput_per_min": None}
        for s in ("collector-coingecko", "collector-dexscreener",
                  "collector-social", "collector-news")
    })
    by = {s["id"]: s for s in sp.build_pipeline_stages({}, svc)}
    assert by["collect"]["throughput_per_min"] is None


def test_last_item_is_serialised_with_its_summary() -> None:
    counts = {"triage": sp.StageCounts(volume=3, last_at=NOW, last_summary="SOL · score 72")}
    by = {s["id"]: s for s in sp.build_pipeline_stages(counts, _services())}
    assert by["triage"]["last_at"] == NOW.isoformat()
    assert by["triage"]["last_summary"] == "SOL · score 72"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_systems_pipeline.py -v`
Expected: FAIL — `FileNotFoundError` / `ModuleNotFoundError` for `systems_pipeline`.

- [ ] **Step 3: Write the module**

Create `services/api-gateway/app/systems_pipeline.py`:

```python
"""Windowed, per-stage view of the pipeline behind the Command Center graph.

The graph used to report `0/m` on every connector: the health collector scraped
the wrong metric names, so `throughput_per_min` was never written and the read
API fell back to a literal zero. An unknown that renders as zero is worse than
no value at all — it says "the pipeline is dead" when it means "I did not
measure". Nothing here converts an unknown into a number.

Volumes come from Postgres rather than Prometheus: they survive a restart, and
they reuse the funnel's stage definitions so the two panels never disagree.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StageSpec:
    id: str
    label: str
    sublabel: str
    services: tuple[str, ...]


STAGE_SPECS: tuple[StageSpec, ...] = (
    StageSpec(
        "collect", "Collecte", "Marché · Social · News",
        ("collector-coingecko", "collector-dexscreener",
         "collector-social", "collector-news"),
    ),
    StageSpec("sentiment", "Sentiment", "Scoring L1", ("sentiment-service",)),
    StageSpec("triage", "Triage", "Haiku", ("ai-worker-haiku",)),
    StageSpec("senior", "Analyse", "Sonnet", ("ai-worker-sonnet",)),
    StageSpec("decision", "Décision", "Fusion signaux", ("decision-engine",)),
    StageSpec("risk", "Risque", "Garde-fous", ("risk-engine",)),
    StageSpec("execute", "Exécution", "Kraken Futures", ("trading-engine",)),
)

STAGE_IDS: tuple[str, ...] = tuple(s.id for s in STAGE_SPECS)
STAGE_BY_ID: dict[str, StageSpec] = {s.id: s for s in STAGE_SPECS}

# Conversion is reported only between stages that count the same unit.
# `collect` counts price points *and* content rows, `sentiment` counts content
# rows, `triage` counts per-token signals — a ratio across those is a number
# with no meaning, and a meaningless percentage on a debugging panel is worse
# than a blank.
CONVERSION_FROM: dict[str, str] = {
    "senior": "triage",
    "decision": "senior",
    "risk": "decision",
    "execute": "risk",
}

# "idle" ranks above "healthy": a collector with no data is not fine, it is
# quiet, and the stage roll-up must not hide that behind a healthy sibling.
_STATUS_RANK = {"healthy": 0, "idle": 1, "degraded": 2, "down": 3}


@dataclass(frozen=True)
class StageCounts:
    """What crossed one stage over the window. Every field defaults to unknown."""

    volume: int | None = None
    dropped: int | None = None
    last_at: datetime | None = None
    last_summary: str | None = None


def _roll_up_status(statuses: Sequence[str]) -> str:
    if not statuses:
        return "idle"
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, len(_STATUS_RANK)))


def _roll_up_throughput(values: Sequence[int | None]) -> int | None:
    """Sum the measured collectors; None when none of them has been measured."""
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _conversion(volume: int | None, previous: int | None) -> float | None:
    if volume is None or not previous:
        return None
    return round(volume / previous * 100, 2)


def build_pipeline_stages(
    counts: Mapping[str, StageCounts],
    services: Mapping[str, Mapping],
) -> list[dict]:
    """Assemble the seven graph nodes. Pure — no database, no clock."""
    out: list[dict] = []
    for spec in STAGE_SPECS:
        c = counts.get(spec.id) or StageCounts()
        nodes = [services[s] for s in spec.services if s in services]
        previous_id = CONVERSION_FROM.get(spec.id)
        previous = counts.get(previous_id) if previous_id else None
        out.append(
            {
                "id": spec.id,
                "label": spec.label,
                "sublabel": spec.sublabel,
                "status": _roll_up_status([n.get("status", "idle") for n in nodes]),
                "throughput_per_min": _roll_up_throughput(
                    [n.get("throughput_per_min") for n in nodes]
                ),
                "volume": c.volume,
                "dropped": c.dropped,
                "conversion_pct": _conversion(
                    c.volume, previous.volume if previous else None
                ),
                "last_at": c.last_at.isoformat() if c.last_at else None,
                "last_summary": c.last_summary,
            }
        )
    return out
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_systems_pipeline.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/systems_pipeline.py tests/test_systems_pipeline.py
git commit -m "feat(systems): pure per-stage pipeline shaping

Unknown stays None end to end; conversion is reported only between stages
counting the same unit."
```

---

### Task 4: Per-stage summaries and the shared reason collapser

Small pure functions, written before the queries that feed them.

**Files:**
- Modify: `services/api-gateway/app/systems_pipeline.py`
- Modify: `services/api-gateway/app/funnel.py:113-130`
- Test: `tests/test_systems_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_systems_pipeline.py`:

```python
from types import SimpleNamespace


def test_signal_summary_names_the_symbol_score_and_outcome() -> None:
    row = SimpleNamespace(symbol="SOL", opportunity_score=72, escalated=True,
                          block_reason="unknown")
    assert sp.summarize_signal(row) == "SOL · score 72 · escaladé"


def test_signal_summary_says_why_a_non_escalated_signal_stopped() -> None:
    row = SimpleNamespace(symbol="SOL", opportunity_score=13, escalated=False,
                          block_reason="score_below_threshold")
    assert sp.summarize_signal(row) == "SOL · score 13 · score_below_threshold"


def test_execution_summary_carries_status_and_fill() -> None:
    row = SimpleNamespace(symbol="BTC", direction="long", status="filled",
                          fill_price=101.5, pnl=12.0)
    assert sp.summarize_trade(row) == "BTC · long · filled @ 101.5 · PnL 12.0"


def test_execution_summary_omits_a_fill_it_does_not_have() -> None:
    row = SimpleNamespace(symbol="BTC", direction="long", status="submitted",
                          fill_price=None, pnl=None)
    assert sp.summarize_trade(row) == "BTC · long · submitted"
```

Append to `tests/test_funnel_aggregation.py`:

```python
def test_collapse_reason_is_the_shared_number_normaliser() -> None:
    """The stage drawer groups rejection reasons the same way the funnel does;
    two different collapsers would make the two panels disagree on the same rows."""
    assert funnel.collapse_reason("score 13 below threshold 70") == (
        "score N below threshold N"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_systems_pipeline.py tests/test_funnel_aggregation.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'summarize_signal'` and `'collapse_reason'`.

- [ ] **Step 3: Implement**

In `services/api-gateway/app/funnel.py`, add above `_merge_block_reasons` (line 113):

```python
def collapse_reason(reason: str) -> str:
    """Group reasons that differ only by the value written into their text.

    Public because the per-stage drawer groups the same rows; a second copy of
    this rule would make the two panels disagree about the same rejections.
    """
    return _NUMBER.sub("N", reason)
```

and inside `_merge_block_reasons`, replace `_NUMBER.sub("N", reason)` with `collapse_reason(reason)`.

In `services/api-gateway/app/systems_pipeline.py`, append:

```python
def summarize_content(row) -> str:
    head = (row.title or row.text or "").strip().replace("\n", " ")
    head = head[:60] + "…" if len(head) > 60 else head
    return f"{row.source} · {row.kind}" + (f" · {head}" if head else "")


def summarize_scored(row) -> str:
    # scored_at can be set with a null score when the model abstained; "—" says
    # that, where a formatted 0.00 would claim a neutral verdict we never made.
    score = f"{row.sentiment_score:+.2f}" if row.sentiment_score is not None else "—"
    return f"{row.source} · sentiment {score}"


def summarize_signal(row) -> str:
    outcome = "escaladé" if row.escalated else row.block_reason
    return f"{row.symbol} · score {row.opportunity_score} · {outcome}"


def summarize_journal(row) -> str:
    return (
        f"{row.symbol} · Sonnet {row.sonnet_direction or '—'} "
        f"· score {row.sonnet_score if row.sonnet_score is not None else '—'}"
    )


def summarize_decision(row) -> str:
    return f"{row.symbol} · {row.direction} · confiance {row.confidence:.0%}"


def summarize_approved(row) -> str:
    return f"{row.symbol} · {row.direction} · taille {row.position_size_pct:.1%}"


def summarize_trade(row) -> str:
    out = f"{row.symbol} · {row.direction} · {row.status}"
    if row.fill_price is not None:
        out += f" @ {row.fill_price}"
    if row.pnl is not None:
        out += f" · PnL {row.pnl}"
    return out
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_systems_pipeline.py tests/test_funnel_aggregation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/systems_pipeline.py services/api-gateway/app/funnel.py tests/test_systems_pipeline.py tests/test_funnel_aggregation.py
git commit -m "feat(systems): per-stage item summaries; share the reason collapser"
```

---

### Task 5: The TTL cache, with honest failure behaviour

`/systems/overview` is polled every 8 s by every open terminal. The aggregates scan four tables, so they get a 30 s cache — and when they fail, the endpoint says so rather than reporting zeros.

**Files:**
- Modify: `services/api-gateway/app/systems_pipeline.py`
- Test: `tests/test_systems_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_systems_pipeline.py`:

```python
import pytest


class _Recorder:
    """Stands in for the aggregate query; counts calls and can be made to fail."""

    def __init__(self, value, fail=False):
        self.value = value
        self.fail = fail
        self.calls = 0

    async def __call__(self, session, window):
        self.calls += 1
        if self.fail:
            raise RuntimeError("db down")
        return self.value


@pytest.fixture(autouse=True)
def _clear_cache():
    sp.STAGE_CACHE.clear()
    yield
    sp.STAGE_CACHE.clear()


async def test_a_second_poll_inside_the_ttl_does_not_query_again() -> None:
    fetch = _Recorder({"triage": sp.StageCounts(volume=7)})
    first, stale = await sp.stage_counts_cached(None, "24h", now=0.0, fetch=fetch)
    second, _ = await sp.stage_counts_cached(None, "24h", now=20.0, fetch=fetch)
    assert fetch.calls == 1
    assert first == second
    assert stale is False


async def test_the_cache_expires_after_its_ttl() -> None:
    fetch = _Recorder({"triage": sp.StageCounts(volume=7)})
    await sp.stage_counts_cached(None, "24h", now=0.0, fetch=fetch)
    await sp.stage_counts_cached(None, "24h", now=31.0, fetch=fetch)
    assert fetch.calls == 2


async def test_each_window_is_cached_separately() -> None:
    fetch = _Recorder({"triage": sp.StageCounts(volume=7)})
    await sp.stage_counts_cached(None, "1h", now=0.0, fetch=fetch)
    await sp.stage_counts_cached(None, "24h", now=0.0, fetch=fetch)
    assert fetch.calls == 2


async def test_a_failed_query_serves_the_last_good_value_marked_stale() -> None:
    good = _Recorder({"triage": sp.StageCounts(volume=7)})
    await sp.stage_counts_cached(None, "24h", now=0.0, fetch=good)
    broken = _Recorder(None, fail=True)
    counts, stale = await sp.stage_counts_cached(None, "24h", now=100.0, fetch=broken)
    assert stale is True
    assert counts["triage"].volume == 7


async def test_a_failed_query_with_no_cache_reports_unknown_not_zero() -> None:
    """The whole point of this panel: a zero it cannot vouch for is a lie."""
    broken = _Recorder(None, fail=True)
    counts, stale = await sp.stage_counts_cached(None, "24h", now=0.0, fetch=broken)
    assert stale is True
    stages = sp.build_pipeline_stages(counts, _services())
    assert all(s["volume"] is None for s in stages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_systems_pipeline.py -k cache or stale -v`
Expected: FAIL — `AttributeError: module has no attribute 'STAGE_CACHE'`.

- [ ] **Step 3: Implement the cache**

Append to `services/api-gateway/app/systems_pipeline.py` (add `import logging`, `import time` and `from collections.abc import Awaitable, Callable` at the top):

```python
logger = logging.getLogger(__name__)

# /systems/overview is polled every 8s by every open terminal and these
# aggregates scan four tables. 30s of staleness on a volume counter is invisible
# to an operator; a fourfold query amplification is not.
CACHE_TTL_S = 30.0


class _StageCache:
    def __init__(self, ttl_s: float = CACHE_TTL_S) -> None:
        self._ttl = ttl_s
        self._entries: dict[str, tuple[float, dict[str, StageCounts]]] = {}

    def fresh(self, window: str, now: float) -> dict[str, StageCounts] | None:
        entry = self._entries.get(window)
        if entry and now - entry[0] < self._ttl:
            return entry[1]
        return None

    def last(self, window: str) -> dict[str, StageCounts] | None:
        entry = self._entries.get(window)
        return entry[1] if entry else None

    def put(self, window: str, now: float, value: dict[str, StageCounts]) -> None:
        self._entries[window] = (now, value)

    def clear(self) -> None:
        self._entries.clear()


STAGE_CACHE = _StageCache()


async def stage_counts_cached(
    session,
    window: str,
    *,
    now: float | None = None,
    fetch: Callable[..., Awaitable[dict[str, StageCounts]]] | None = None,
) -> tuple[dict[str, StageCounts], bool]:
    """Return (counts, stale).

    A failed aggregate serves the last good value flagged stale, or an empty
    mapping — which shapes into `null` fields, never into zeros. Reporting 0 for
    a count we could not take is the exact defect this module exists to remove.
    """
    now = time.monotonic() if now is None else now
    fetch = fetch or fetch_stage_counts

    hit = STAGE_CACHE.fresh(window, now)
    if hit is not None:
        return hit, False
    try:
        counts = await fetch(session, window)
    except Exception:
        logger.exception("pipeline aggregates failed (window=%s)", window)
        last = STAGE_CACHE.last(window)
        return (last, True) if last is not None else ({}, True)
    STAGE_CACHE.put(window, now, counts)
    return counts, False
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_systems_pipeline.py -v`
Expected: PASS (`asyncio_mode = "auto"` in `pyproject.toml` runs the async tests without a marker).

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/systems_pipeline.py tests/test_systems_pipeline.py
git commit -m "feat(systems): TTL cache for pipeline aggregates; failures report unknown"
```

---

### Task 6: The windowed aggregate queries

**Files:**
- Modify: `services/api-gateway/app/systems_pipeline.py`
- Test: `tests/test_systems_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_systems_pipeline.py`:

```python
def test_window_hours_covers_exactly_the_three_supported_windows() -> None:
    assert sp.WINDOW_HOURS == {"1h": 1, "24h": 24, "7d": 168}


def test_every_stage_reports_a_count_for_the_ids_in_the_catalog() -> None:
    """A stage in the catalog with no aggregate would render a permanent blank
    that looks exactly like a dead stage."""
    assert set(sp.STAGE_IDS) == {
        "collect", "sentiment", "triage", "senior", "decision", "risk", "execute",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_systems_pipeline.py -k window_hours -v`
Expected: FAIL — `AttributeError: module has no attribute 'WINDOW_HOURS'`.

- [ ] **Step 3: Implement the aggregates**

Append to `services/api-gateway/app/systems_pipeline.py`, adding these imports at the top:

```python
from datetime import UTC, timedelta

from sqlalchemy import and_, func, select

from cmi_common.db.models import (
    Decision,
    DecisionJournal,
    PipelineRejection,
    Price,
    RawContent,
    Signal,
    Trade,
)
```

```python
WINDOW_HOURS = {"1h": 1, "24h": 24, "7d": 168}
DEFAULT_WINDOW = "24h"

# `signals`, `prices`, `decision_journal`, `pipeline_rejections`, `decisions`
# and `trades` all store naive UTC; `raw_content` is the one table with
# tz-aware columns. Mixing the two raises at query time, so both cutoffs are
# computed once and passed explicitly.
EXECUTED_STATUSES = ("submitted", "filled", "closed")
FAILED_STATUSES = ("failed", "rejected")


def _cutoffs(window: str) -> tuple[datetime, datetime]:
    hours = WINDOW_HOURS[window]
    aware = datetime.now(tz=UTC) - timedelta(hours=hours)
    return aware.replace(tzinfo=None), aware


async def _count(session, model, *where) -> int:
    stmt = select(func.count()).select_from(model).where(and_(*where))
    return int((await session.execute(stmt)).scalar_one())


async def _latest(session, model, order_col, *where):
    stmt = select(model).order_by(order_col.desc()).limit(1)
    if where:
        stmt = stmt.where(and_(*where))
    return (await session.execute(stmt)).scalars().first()


async def fetch_stage_counts(session, window: str) -> dict[str, StageCounts]:
    """One StageCounts per stage over `window`. Raises on DB failure —
    `stage_counts_cached` decides what an operator should see."""
    since, since_aware = _cutoffs(window)

    prices = await _count(session, Price, Price.time >= since)
    content = await _count(session, RawContent, RawContent.fetched_at >= since_aware)
    last_content = await _latest(
        session, RawContent, RawContent.fetched_at, RawContent.fetched_at >= since_aware
    )

    scored = await _count(session, RawContent, RawContent.scored_at >= since_aware)
    backlog = await _count(session, RawContent, RawContent.scored_at.is_(None))
    last_scored = await _latest(
        session, RawContent, RawContent.scored_at, RawContent.scored_at.is_not(None)
    )

    analyses = await _count(session, Signal, Signal.time >= since)
    not_escalated = await _count(
        session, Signal, Signal.time >= since, Signal.escalated.is_(False)
    )
    last_signal = await _latest(session, Signal, Signal.time, Signal.time >= since)

    escalated = await _count(
        session, Signal, Signal.time >= since, Signal.escalated.is_(True)
    )
    # The measured production bottleneck: escalated but never handed to Sonnet
    # because the hourly budget was spent. Two separate numbers, on purpose.
    budget_skipped = await _count(
        session,
        DecisionJournal,
        DecisionJournal.time >= since,
        DecisionJournal.escalated.is_(True),
        DecisionJournal.sonnet_called.is_(False),
    )
    last_journal = await _latest(
        session,
        DecisionJournal,
        DecisionJournal.time,
        DecisionJournal.time >= since,
        DecisionJournal.sonnet_called.is_(True),
    )

    decisions = await _count(session, Decision, Decision.created_at >= since)
    decision_rejected = await _count(
        session,
        PipelineRejection,
        PipelineRejection.time >= since,
        PipelineRejection.stage == "decision_engine",
    )
    last_decision = await _latest(
        session, Decision, Decision.created_at, Decision.created_at >= since
    )

    approved = await _count(session, Trade, Trade.created_at >= since)
    risk_rejected = await _count(
        session,
        PipelineRejection,
        PipelineRejection.time >= since,
        PipelineRejection.stage == "risk_engine",
    )
    last_approved = await _latest(
        session, Trade, Trade.created_at, Trade.created_at >= since
    )

    executed = await _count(
        session, Trade, Trade.created_at >= since, Trade.status.in_(EXECUTED_STATUSES)
    )
    failed = await _count(
        session, Trade, Trade.created_at >= since, Trade.status.in_(FAILED_STATUSES)
    )
    last_executed = await _latest(
        session,
        Trade,
        Trade.created_at,
        Trade.created_at >= since,
        Trade.status.in_(EXECUTED_STATUSES),
    )

    return {
        "collect": StageCounts(
            volume=prices + content,
            last_at=getattr(last_content, "fetched_at", None),
            last_summary=summarize_content(last_content) if last_content else None,
        ),
        "sentiment": StageCounts(
            volume=scored,
            dropped=backlog,
            last_at=getattr(last_scored, "scored_at", None),
            last_summary=summarize_scored(last_scored) if last_scored else None,
        ),
        "triage": StageCounts(
            volume=analyses,
            dropped=not_escalated,
            last_at=getattr(last_signal, "time", None),
            last_summary=summarize_signal(last_signal) if last_signal else None,
        ),
        "senior": StageCounts(
            volume=escalated,
            dropped=budget_skipped,
            last_at=getattr(last_journal, "time", None),
            last_summary=summarize_journal(last_journal) if last_journal else None,
        ),
        "decision": StageCounts(
            volume=decisions,
            dropped=decision_rejected,
            last_at=getattr(last_decision, "created_at", None),
            last_summary=summarize_decision(last_decision) if last_decision else None,
        ),
        "risk": StageCounts(
            volume=approved,
            dropped=risk_rejected,
            last_at=getattr(last_approved, "created_at", None),
            last_summary=summarize_approved(last_approved) if last_approved else None,
        ),
        "execute": StageCounts(
            volume=executed,
            dropped=failed,
            last_at=getattr(last_executed, "created_at", None),
            last_summary=summarize_trade(last_executed) if last_executed else None,
        ),
    }
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_systems_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/systems_pipeline.py tests/test_systems_pipeline.py
git commit -m "feat(systems): windowed per-stage aggregates from Postgres"
```

---

### Task 7: Wire the aggregates into `/systems/overview`

**Files:**
- Modify: `services/api-gateway/app/read_api.py:973-1045,1117-1165`
- Test: `tests/test_api_gateway_read.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_gateway_read.py`:

```python
systems_pipeline = load_service_module("api-gateway", "systems_pipeline")


def _health(service, status="healthy", throughput=None):
    detail = {} if throughput is None else {"throughput_per_min": throughput}
    return SimpleNamespace(
        service=service, status=status, healthy=status == "healthy",
        latency_ms=3.0, detail=detail,
    )


def test_unmeasured_service_throughput_is_null_not_zero() -> None:
    """A service whose /metrics has only been scraped once has no rate yet.
    Reporting 0 there is what made the whole graph look dead."""
    snap = read_api.assemble_systems_snapshot([_health("ai-worker-haiku")])
    haiku = next(s for s in snap["services"] if s["id"] == "ai-worker-haiku")
    assert haiku["throughput_per_min"] is None


def test_overview_pipeline_carries_the_stage_counts_it_is_given() -> None:
    counts = {"triage": systems_pipeline.StageCounts(volume=42, dropped=40)}
    snap = read_api.assemble_systems_snapshot(
        [_health("ai-worker-haiku", throughput=9)], counts=counts
    )
    triage = next(s for s in snap["pipeline"] if s["id"] == "triage")
    assert triage["volume"] == 42
    assert triage["dropped"] == 40
    assert triage["throughput_per_min"] == 9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_gateway_read.py -k throughput_is_null or pipeline_carries -v`
Expected: FAIL — `assert 0 is None`, then `TypeError: assemble_systems_snapshot() got an unexpected keyword argument 'counts'`.

- [ ] **Step 3: Modify `assemble_systems_snapshot`**

In `services/api-gateway/app/read_api.py`, add near the other imports:

```python
from .systems_pipeline import (
    DEFAULT_WINDOW,
    STAGE_BY_ID,
    StageCounts,
    build_pipeline_stages,
    stage_counts_cached,
)
```

Delete the `_PIPELINE` constant (lines 973-981) — the catalog now lives in `systems_pipeline.STAGE_SPECS`.

Change the signature and the throughput line:

```python
def assemble_systems_snapshot(
    rows: Iterable[Any],
    *,
    counts: dict[str, StageCounts] | None = None,
    stale: bool = False,
    window: str = DEFAULT_WINDOW,
    now: datetime | None = None,
) -> dict:
    """Build the SystemsSnapshot from persisted health rows. Pure."""
```

Inside the services loop, replace the `throughput_per_min` entry:

```python
                # None, not 0: two /metrics scrapes are needed before a rate
                # exists, and a zero we cannot vouch for reads as a dead service.
                "throughput_per_min": (
                    int(detail["throughput_per_min"])
                    if detail.get("throughput_per_min") is not None
                    else None
                ),
```

Replace the `pipeline = [...]` block (lines 1013-1020) with:

```python
    pipeline = build_pipeline_stages(counts or {}, smap)
```

Replace the `events_per_min` line in `summary`:

```python
        "events_per_min": int(
            sum(s["throughput_per_min"] or 0 for s in services)
        ),
```

and add to the returned dict, next to `"pipeline": pipeline`:

```python
        "pipeline_window": window,
        "pipeline_stale": stale,
```

- [ ] **Step 4: Wire the endpoint**

Replace the `systems_overview` signature and its first two lines (`read_api.py:1117-1120`):

```python
@router.get("/systems/overview")
async def systems_overview(
    window: str = Query(DEFAULT_WINDOW, pattern="^(1h|24h|7d)$"),
    session: AsyncSession = Depends(get_session_dep),
) -> dict:
    from sqlalchemy import text as _text

    rows = (await session.execute(select(ServiceHealth))).scalars().all()
    counts, stale = await stage_counts_cached(session, window)
    snap = assemble_systems_snapshot(rows, counts=counts, stale=stale, window=window)
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_api_gateway_read.py tests/test_read_contract.py tests/test_systems_pipeline.py -v`
Expected: PASS. If `test_read_contract` fails on missing `pipeline_window` / `pipeline_stale`, that is Task 9 — note it and continue.

- [ ] **Step 6: Commit**

```bash
git add services/api-gateway/app/read_api.py tests/test_api_gateway_read.py
git commit -m "feat(systems): overview serves windowed stage volumes; null throughput

An unmeasured throughput is now null rather than 0, which is what made every
connector in the Command Center graph read as a dead pipeline."
```

---

### Task 8: `GET /systems/stage/{id}` — the drawer's data

**Files:**
- Modify: `services/api-gateway/app/systems_pipeline.py`
- Modify: `services/api-gateway/app/read_api.py` (mount the route)
- Test: `tests/test_systems_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_systems_pipeline.py`:

```python
def _signal_row(**kw):
    base = dict(
        symbol="SOL", opportunity_score=72, confidence=0.81, factors_present=3,
        escalated=True, block_reason="unknown", time=NOW,
        payload={"correlation_id": "cid-9"},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_stage_item_shape_is_stable() -> None:
    item = sp.signal_item(_signal_row())
    assert set(item) == {"at", "symbol", "summary", "detail", "correlation_id"}
    assert item["correlation_id"] == "cid-9"
    assert item["at"] == NOW.isoformat()


def test_stage_item_tolerates_a_payload_without_a_correlation_id() -> None:
    assert sp.signal_item(_signal_row(payload={}))["correlation_id"] is None


def test_breakdown_collapses_reasons_that_differ_only_by_a_number() -> None:
    rows = [("score 13 below threshold 70", 4), ("score 14 below threshold 70", 6)]
    assert sp.reason_breakdown(rows) == [
        {"key": "score N below threshold N", "count": 10}
    ]


def test_every_catalog_stage_has_a_detail_builder() -> None:
    """A stage in the graph with no builder would open an empty drawer, which
    reads exactly like a stage nothing ever crossed."""
    assert set(sp.STAGE_IDS) == set(sp.DETAIL_BUILDERS)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_systems_pipeline.py -k stage_item or breakdown -v`
Expected: FAIL — `AttributeError: module has no attribute 'signal_item'`.

- [ ] **Step 3: Implement item builders and the detail handler**

Append to `services/api-gateway/app/systems_pipeline.py` (add `from typing import Any` and `from .funnel import collapse_reason` to the imports; `MAX_ITEMS` bounds what a single request can pull):

```python
MAX_ITEMS = 50
DEFAULT_ITEMS = 20


def _item(at, symbol, summary, detail, correlation_id=None) -> dict:
    return {
        "at": at.isoformat() if at else None,
        "symbol": symbol,
        "summary": summary,
        "detail": detail,
        "correlation_id": correlation_id,
    }


def content_item(row) -> dict:
    return _item(row.fetched_at, (row.symbols or [None])[0], summarize_content(row),
                 {"source": row.source, "kind": row.kind, "url": row.url})


def scored_item(row) -> dict:
    return _item(row.scored_at, (row.symbols or [None])[0], summarize_scored(row),
                 {"score": row.sentiment_score, "model": row.sentiment_model})


def signal_item(row) -> dict:
    return _item(
        row.time, row.symbol, summarize_signal(row),
        {"score": row.opportunity_score, "confidence": row.confidence,
         "factors_present": row.factors_present, "block_reason": row.block_reason},
        (row.payload or {}).get("correlation_id"),
    )


def journal_item(row) -> dict:
    return _item(
        row.time, row.symbol, summarize_journal(row),
        {"score": row.score, "sonnet_score": row.sonnet_score,
         "sonnet_validated": row.sonnet_validated, "skip_reason": row.skip_reason},
        row.correlation_id,
    )


def decision_item(row) -> dict:
    return _item(
        row.created_at, row.symbol, summarize_decision(row),
        {"direction": row.direction, "score": row.opportunity_score,
         "ai_validated": row.ai_validated},
        row.correlation_id,
    )


def approved_item(row) -> dict:
    return _item(
        row.created_at, row.symbol, summarize_approved(row),
        {"size_pct": row.position_size_pct, "stop_loss": row.stop_loss,
         "take_profit": row.take_profit, "rr": row.risk_reward_ratio},
        row.correlation_id,
    )


def trade_item(row) -> dict:
    return _item(
        row.created_at, row.symbol, summarize_trade(row),
        {"status": row.status, "fill_price": row.fill_price, "pnl": row.pnl},
        row.correlation_id,
    )


def reason_breakdown(rows) -> list[dict]:
    """Group rejection reasons the way the funnel does, biggest first."""
    totals: dict[str, int] = {}
    for reason, count in rows:
        totals[collapse_reason(str(reason))] = totals.get(
            collapse_reason(str(reason)), 0
        ) + int(count)
    return [
        {"key": k, "count": v}
        for k, v in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]


async def _grouped(session, col, *where) -> list[dict]:
    rows = (
        await session.execute(
            select(col, func.count()).where(and_(*where)).group_by(col)
        )
    ).all()
    return [
        {"key": str(k), "count": int(c)}
        for k, c in sorted(rows, key=lambda r: r[1], reverse=True)
    ]


async def _rejection_breakdown(session, stage_name, since) -> list[dict]:
    rows = (
        await session.execute(
            select(PipelineRejection.reason, func.count())
            .where(
                and_(
                    PipelineRejection.time >= since,
                    PipelineRejection.stage == stage_name,
                )
            )
            .group_by(PipelineRejection.reason)
        )
    ).all()
    return reason_breakdown(rows)


@dataclass(frozen=True)
class _DetailCtx:
    """Everything a per-stage builder needs, so each one stays a two-liner."""

    session: Any  # AsyncSession; typed loosely so this module stays import-light
    since: datetime
    since_aware: datetime
    limit: int
    counts: StageCounts

    async def rows(self, model, order_col, *where):
        stmt = (
            select(model)
            .where(and_(*where))
            .order_by(order_col.desc())
            .limit(self.limit)
        )
        return (await self.session.execute(stmt)).scalars().all()


async def _detail_collect(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(
        RawContent, RawContent.fetched_at, RawContent.fetched_at >= ctx.since_aware
    )
    breakdown = await _grouped(
        ctx.session, RawContent.source, RawContent.fetched_at >= ctx.since_aware
    )
    breakdown.append(
        {"key": "prices", "count": await _count(ctx.session, Price, Price.time >= ctx.since)}
    )
    return [content_item(r) for r in rows], breakdown


async def _detail_sentiment(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(
        RawContent, RawContent.scored_at, RawContent.scored_at >= ctx.since_aware
    )
    return [scored_item(r) for r in rows], [
        {"key": "scoré", "count": ctx.counts.volume or 0},
        {"key": "en attente", "count": ctx.counts.dropped or 0},
    ]


async def _detail_triage(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(Signal, Signal.time, Signal.time >= ctx.since)
    breakdown = await _grouped(
        ctx.session, Signal.block_reason,
        Signal.time >= ctx.since, Signal.escalated.is_(False),
    )
    return [signal_item(r) for r in rows], breakdown


async def _detail_senior(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(
        DecisionJournal, DecisionJournal.time,
        DecisionJournal.time >= ctx.since, DecisionJournal.sonnet_called.is_(True),
    )
    breakdown = await _grouped(
        ctx.session, DecisionJournal.skip_reason,
        DecisionJournal.time >= ctx.since,
        DecisionJournal.escalated.is_(True),
        DecisionJournal.sonnet_called.is_(False),
    )
    return [journal_item(r) for r in rows], breakdown


async def _detail_decision(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(Decision, Decision.created_at, Decision.created_at >= ctx.since)
    breakdown = await _rejection_breakdown(ctx.session, "decision_engine", ctx.since)
    return [decision_item(r) for r in rows], breakdown


async def _detail_risk(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(Trade, Trade.created_at, Trade.created_at >= ctx.since)
    breakdown = await _rejection_breakdown(ctx.session, "risk_engine", ctx.since)
    return [approved_item(r) for r in rows], breakdown


async def _detail_execute(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(
        Trade, Trade.created_at,
        Trade.created_at >= ctx.since, Trade.status.in_(EXECUTED_STATUSES),
    )
    breakdown = await _grouped(ctx.session, Trade.status, Trade.created_at >= ctx.since)
    return [trade_item(r) for r in rows], breakdown


# Explicit dispatch rather than an if/elif chain: a stage added to STAGE_SPECS
# without a builder here is caught by a test, not by an operator staring at an
# empty drawer that reads like a stage nothing ever crossed.
DETAIL_BUILDERS = {
    "collect": _detail_collect,
    "sentiment": _detail_sentiment,
    "triage": _detail_triage,
    "senior": _detail_senior,
    "decision": _detail_decision,
    "risk": _detail_risk,
    "execute": _detail_execute,
}


async def fetch_stage_detail(session, stage_id: str, window: str, limit: int) -> dict:
    """Items + breakdown for one stage. `stage_id` is validated by the caller."""
    spec = STAGE_BY_ID[stage_id]
    since, since_aware = _cutoffs(window)
    counts = (await stage_counts_cached(session, window))[0].get(
        stage_id, StageCounts()
    )
    ctx = _DetailCtx(session, since, since_aware, limit, counts)
    items, breakdown = await DETAIL_BUILDERS[stage_id](ctx)

    return {
        "id": spec.id,
        "label": spec.label,
        "window": window,
        "volume": counts.volume,
        "dropped": counts.dropped,
        "breakdown": breakdown,
        "items": items,
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }
```

- [ ] **Step 4: Mount the route**

In `services/api-gateway/app/read_api.py`, add after `systems_funnel`:

```python
@router.get("/systems/stage/{stage_id}")
async def systems_stage(
    stage_id: str,
    window: str = Query(DEFAULT_WINDOW, pattern="^(1h|24h|7d)$"),
    limit: int = Query(20, ge=1, le=50),
    session: AsyncSession = Depends(get_session_dep),
) -> dict:
    """The items behind one stage of the pipeline graph.

    An unknown id is a 404, not an empty payload: an empty list would read as
    "this stage processed nothing", which is the confusion this whole panel
    exists to remove.
    """
    if stage_id not in STAGE_BY_ID:
        raise HTTPException(status_code=404, detail=f"unknown stage {stage_id!r}")
    return await fetch_stage_detail(session, stage_id, window, limit)
```

Ensure `HTTPException` is imported from `fastapi` at the top of `read_api.py` (add it to the existing `from fastapi import ...` line if absent).

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_systems_pipeline.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/api-gateway/app/systems_pipeline.py services/api-gateway/app/read_api.py tests/test_systems_pipeline.py
git commit -m "feat(systems): GET /systems/stage/{id} serves per-stage items"
```

---

### Task 9: Contract manifest and parity test

**Files:**
- Modify: `services/api-gateway/app/read_contract.py:72-80`
- Modify: `tests/test_read_contract.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_read_contract.py`:

```python
systems_pipeline = load_service_module("api-gateway", "systems_pipeline")


def test_systems_overview_declares_the_pipeline_window_and_staleness() -> None:
    snap = read_api.assemble_systems_snapshot([])
    assert set(snap) >= CONTRACT["systems/overview"]


def test_pipeline_stage_shape_matches_the_contract() -> None:
    snap = read_api.assemble_systems_snapshot([])
    for stage in snap["pipeline"]:
        assert set(stage) == CONTRACT["systems/overview.pipeline[]"]


def test_stage_detail_item_shape_matches_the_contract() -> None:
    row = SimpleNamespace(
        symbol="SOL", opportunity_score=72, confidence=0.8, factors_present=3,
        escalated=True, block_reason="unknown", time=NOW,
        payload={"correlation_id": "cid-1"},
    )
    assert set(systems_pipeline.signal_item(row)) == CONTRACT["systems/stage.items[]"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_read_contract.py -k pipeline or stage_detail -v`
Expected: FAIL — `KeyError: 'systems/overview.pipeline[]'`.

- [ ] **Step 3: Extend the manifest**

In `services/api-gateway/app/read_contract.py`, replace the `systems/overview` entry and add the two new ones:

```python
    "systems/overview": {
        "summary", "services", "pipeline", "kafka", "collectors", "workers",
        "infra", "pipeline_window", "pipeline_stale",
    },
    # Nested shapes: the graph reads these per node, so drift here is invisible
    # to a top-level key check and shows up only as `undefined` in the browser.
    "systems/overview.pipeline[]": {
        "id", "label", "sublabel", "status", "throughput_per_min", "volume",
        "dropped", "conversion_pct", "last_at", "last_summary",
    },
    "systems/stage": {
        "id", "label", "window", "volume", "dropped", "breakdown", "items",
        "updated_at",
    },
    "systems/stage.items[]": {
        "at", "symbol", "summary", "detail", "correlation_id",
    },
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_read_contract.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/read_contract.py tests/test_read_contract.py
git commit -m "test(read-api): pin the pipeline stage and stage-detail shapes"
```

---

### Task 10: Indexes for the range scans

**Files:**
- Create: `migrations/alembic/versions/0015_pipeline_stage_indexes.py`
- Modify: `libs/cmi_common/cmi_common/db/models.py:219-276`

- [ ] **Step 1: Add the index declarations to the models**

In `libs/cmi_common/cmi_common/db/models.py`, in `RawContent.__table_args__` (line 270), add two entries alongside the existing ones:

```python
        Index("ix_raw_content_fetched_at", "fetched_at"),
        # The existing ix_raw_content_unscored is partial (WHERE scored_at IS
        # NULL) and cannot serve a `scored_at >= W` range scan.
        Index("ix_raw_content_scored_at", "scored_at"),
```

Add to `Decision` (after line 216):

```python
    __table_args__ = (Index("ix_decisions_created_at", "created_at"),)
```

Add to `Trade` (after line 242, next to the `decision` relationship):

```python
    __table_args__ = (Index("ix_trades_created_at", "created_at"),)
```

- [ ] **Step 2: Write the migration**

Create `migrations/alembic/versions/0015_pipeline_stage_indexes.py`:

```python
"""indexes for the Command Center per-stage volume queries

/systems/overview is polled every 8s and now counts rows over a 1h/24h/7d
window on four tables. Without these, the 7d window sequentially scans
raw_content on every cache miss.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_raw_content_fetched_at", "raw_content", ["fetched_at"])
    op.create_index("ix_raw_content_scored_at", "raw_content", ["scored_at"])
    op.create_index("ix_decisions_created_at", "decisions", ["created_at"])
    op.create_index("ix_trades_created_at", "trades", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_trades_created_at", table_name="trades")
    op.drop_index("ix_decisions_created_at", table_name="decisions")
    op.drop_index("ix_raw_content_scored_at", table_name="raw_content")
    op.drop_index("ix_raw_content_fetched_at", table_name="raw_content")
```

- [ ] **Step 3: Verify the migration applies**

Run: `make migrate`
Expected: `Running upgrade 0014 -> 0015` with no error. If the stack is not up, run `make up` first.

- [ ] **Step 4: Commit**

```bash
git add migrations/alembic/versions/0015_pipeline_stage_indexes.py libs/cmi_common/cmi_common/db/models.py
git commit -m "perf(db): index the columns the per-stage volume queries range over"
```

---

### Task 11: Frontend contract, API client and formatters

**Files:**
- Modify: `frontend/src/lib/types/systems.ts:80-109`
- Modify: `frontend/src/lib/api/endpoints.ts:171-175`
- Modify: `frontend/src/lib/format.ts`

- [ ] **Step 1: Update the types**

In `frontend/src/lib/types/systems.ts`, replace `PipelineStage` (lines 80-86):

```ts
export interface PipelineStage {
  id: string;
  label: string;
  sublabel: string;
  status: ServiceHealth;
  /** null until two /metrics scrapes exist — never conflate "not measured" with 0. */
  throughput_per_min: number | null;
  /** Items that crossed this stage over the selected window. */
  volume: number | null;
  /** Rejected here, or still pending (sentiment backlog, Sonnet budget). */
  dropped: number | null;
  /** Survival from the previous stage; null where the two count different units. */
  conversion_pct: number | null;
  last_at: string | null;
  last_summary: string | null;
}

export type SystemsWindow = '1h' | '24h' | '7d';

export interface StageItem {
  at: string | null;
  symbol: string | null;
  summary: string;
  detail: Record<string, string | number | boolean | null>;
  /** When set, the row opens the existing DecisionTraceDrawer. */
  correlation_id: string | null;
}

export interface StageDetail {
  id: string;
  label: string;
  window: string;
  volume: number | null;
  dropped: number | null;
  breakdown: { key: string; count: number }[];
  items: StageItem[];
  updated_at: string;
}
```

In the same file, make `ServiceNode.throughput_per_min` `number | null` — it is the same measurement, and `ServiceGrid` renders it with the same "0 means dead" confusion. Add to `SystemsSnapshot` (line 101):

```ts
  pipeline_window: string;
  pipeline_stale: boolean;
```

- [ ] **Step 2: Update the API client**

In `frontend/src/lib/api/endpoints.ts`, replace `systemsApi` (lines 171-175):

```ts
export const systemsApi = {
  overview: (window: SystemsWindow = '24h') =>
    api.get<SystemsSnapshot>(`/systems/overview?window=${window}`).then((r) => r.data),
  funnel: (window: SystemsWindow = '24h') =>
    api.get<FunnelStats>(`/systems/funnel?window=${window}`).then((r) => r.data),
  stage: (id: string, window: SystemsWindow = '24h', limit = 20) =>
    api
      .get<StageDetail>(`/systems/stage/${id}?window=${window}&limit=${limit}`)
      .then((r) => r.data),
};
```

Import `StageDetail` and `SystemsWindow` alongside the existing `SystemsSnapshot` / `FunnelStats` imports.

- [ ] **Step 3: Add the two formatters**

In `frontend/src/lib/format.ts`, append (`fmtNum` and `fmtRelative` already return `—` for null, so reuse those for volumes and ages):

```ts
/** Throughput. An unmeasured rate is "—/m", never "0/m": that confusion is what
 *  made the Command Center graph read as a permanently dead pipeline. */
export function fmtRate(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—/m';
  return `${Math.round(v)}/m`;
}

/** Stage-to-stage survival; blank when the two stages count different units. */
export function fmtConversion(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '';
  return `${v.toFixed(1)}%`;
}
```

- [ ] **Step 4: Verify types compile**

Run: `cd frontend && npm run typecheck`
Expected: errors only in the files Tasks 12-13 still have to update (`PipelineFlow.tsx`, `ServiceGrid.tsx`, `lib/mock/systems.ts`, `command/page.tsx`). Note them; they are fixed next.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types/systems.ts frontend/src/lib/api/endpoints.ts frontend/src/lib/format.ts
git commit -m "feat(frontend): pipeline stage contract with nullable measurements"
```

---

### Task 12: Mock BFF fixtures

The mock terminal is the default (`NEXT_PUBLIC_USE_MOCK=1`), so it has to serve the new shape — including, deliberately, one unknown value and one true zero.

**Files:**
- Modify: `frontend/src/lib/mock/systems.ts:248-259`
- Modify: `frontend/src/app/api/mock/systems/overview/route.ts`
- Create: `frontend/src/app/api/mock/systems/stage/[id]/route.ts`

- [ ] **Step 1: Update the mock pipeline**

In `frontend/src/lib/mock/systems.ts`, replace the `pipeline()` function (lines 248-259):

```ts
// ── Pipeline flow stages ────────────────────────────────────────────────────────
// `sentiment` deliberately ships throughput_per_min: null and `execute` a real
// volume of 0: the mock has to exercise both "not measured" (—) and "measured,
// nothing happened" (0), because conflating them is the bug this panel fixes.
function pipeline(svc: ServiceNode[]): PipelineStage[] {
  const tp = (id: string) => svc.find((s) => s.id === id)?.throughput_per_min ?? null;
  const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
  const defs: [string, string, string, number | null, number | null, number | null, string | null, string | null][] = [
    ['collect', 'Collecte', 'Marché · Social · News', 8420, null, null, ago(1), 'reddit · social · BTC breaking 70k'],
    ['sentiment', 'Sentiment', 'Scoring L1', 6180, 240, null, ago(2), 'bluesky · sentiment +0.42'],
    ['triage', 'Triage', 'Haiku', 7735, 7406, null, ago(3), 'SOL · score 72 · escaladé'],
    ['senior', 'Analyse', 'Sonnet', 329, 328, 4.3, ago(21), 'SOL · Sonnet long · score 78'],
    ['decision', 'Décision', 'Fusion signaux', 12, 317, 3.6, ago(46), 'SOL · long · confiance 81%'],
    ['risk', 'Risque', 'Garde-fous', 4, 8, 33.3, ago(52), 'SOL · long · taille 5.0%'],
    ['execute', 'Exécution', 'Kraken Futures', 0, 0, 0, null, null],
  ];
  const tpFor: Record<string, number | null> = {
    collect: (tp('coingecko') ?? 0) + (tp('collector-social') ?? 0) + (tp('collector-news') ?? 0),
    sentiment: null,
    triage: tp('ai-haiku'), senior: tp('ai-sonnet'), decision: tp('decision'),
    risk: tp('risk'), execute: tp('trading-engine'),
  };
  return defs.map(([id, label, sublabel, volume, dropped, conversion_pct, last_at, last_summary]) => ({
    id, label, sublabel,
    status: id === 'collect' ? 'degraded' : 'healthy',
    throughput_per_min: tpFor[id] ?? null,
    volume, dropped, conversion_pct, last_at, last_summary,
  }));
}
```

In the same file, add `pipeline_window: '24h'` and `pipeline_stale: false` to the object returned by `getSystemsSnapshot()`.

- [ ] **Step 2: Accept the window parameter**

Replace `frontend/src/app/api/mock/systems/overview/route.ts`:

```ts
import { NextRequest, NextResponse } from 'next/server';
import { getSystemsSnapshot } from '@/lib/mock/systems';

export async function GET(req: NextRequest) {
  const window = req.nextUrl.searchParams.get('window') ?? '24h';
  return NextResponse.json({ ...getSystemsSnapshot(), pipeline_window: window });
}
```

- [ ] **Step 3: Add the stage-detail mock**

Create `frontend/src/app/api/mock/systems/stage/[id]/route.ts`:

```ts
import { NextRequest, NextResponse } from 'next/server';
import { getSystemsSnapshot } from '@/lib/mock/systems';
import type { StageItem } from '@/lib/types/systems';

const SYMBOLS = ['BTC', 'ETH', 'SOL', 'ARB', 'LINK'];

function items(id: string, n: number): StageItem[] {
  return Array.from({ length: n }, (_, i) => {
    const symbol = SYMBOLS[i % SYMBOLS.length];
    return {
      at: new Date(Date.now() - (i + 1) * 90_000).toISOString(),
      symbol,
      summary: `${symbol} · ${id} · échantillon ${i + 1}`,
      detail: { score: 40 + ((i * 7) % 60), confiance: 0.5 + (i % 5) / 10 },
      correlation_id: i % 3 === 0 ? `mock-cid-${i}` : null,
    };
  });
}

export async function GET(req: NextRequest, ctx: { params: { id: string } }) {
  const { id } = ctx.params;
  const stage = getSystemsSnapshot().pipeline.find((s) => s.id === id);
  if (!stage) {
    return NextResponse.json({ detail: `unknown stage '${id}'` }, { status: 404 });
  }
  const window = req.nextUrl.searchParams.get('window') ?? '24h';
  const limit = Number(req.nextUrl.searchParams.get('limit') ?? 20);
  const n = stage.volume === 0 ? 0 : Math.min(limit, 12);
  return NextResponse.json({
    id: stage.id,
    label: stage.label,
    window,
    volume: stage.volume,
    dropped: stage.dropped,
    breakdown: [
      { key: 'reddit', count: 120 },
      { key: 'bluesky', count: 84 },
      { key: 'rss', count: 41 },
    ],
    items: items(id, n),
    updated_at: new Date().toISOString(),
  });
}
```

- [ ] **Step 4: Verify**

Run: `cd frontend && npm run typecheck`
Expected: no errors in the three files above (`PipelineFlow.tsx` / `ServiceGrid.tsx` / `page.tsx` may still fail — Task 13).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/mock/systems.ts frontend/src/app/api/mock/systems
git commit -m "feat(mock): serve the windowed pipeline and per-stage detail"
```

---

### Task 13: Render volume, drop and age on the graph nodes

**Files:**
- Modify: `frontend/src/components/systems/PipelineFlow.tsx`
- Modify: `frontend/src/components/systems/ServiceGrid.tsx:79`

- [ ] **Step 1: Rewrite the connector and node**

In `frontend/src/components/systems/PipelineFlow.tsx`, replace the imports and both components (keep `PipelineFlow` itself, adjusting only what is noted in Step 2):

```tsx
'use client';

import { Box, Stack, Tooltip, Typography } from '@mui/material';
import { HEALTH_COLOR, HealthDot } from './common';
import { HEALTH_LABEL, type PipelineStage } from '@/lib/types/systems';
import { fmtConversion, fmtNum, fmtRate, fmtRelative } from '@/lib/format';

function Connector({ stage }: { stage: PipelineStage }) {
  const conversion = fmtConversion(stage.conversion_pct);
  return (
    <Box sx={{ position: 'relative', flex: '0 0 auto', width: { xs: 54, md: 72 }, height: 118, display: 'grid', placeItems: 'center' }}>
      <svg width="100%" height="40" viewBox="0 0 72 40" preserveAspectRatio="none" aria-hidden>
        <line x1="0" y1="20" x2="66" y2="20" stroke="rgba(255,255,255,0.12)" strokeWidth="2" />
        <line x1="0" y1="20" x2="66" y2="20" stroke="#4d9fff" strokeWidth="2" className="flow-dash" opacity="0.9" />
        <path d="M66 20 L58 16 L58 24 Z" fill="#4d9fff" />
      </svg>
      <Typography className="mono" sx={{ position: 'absolute', top: 12, fontSize: 10, color: 'text.secondary', whiteSpace: 'nowrap' }}>
        {fmtRate(stage.throughput_per_min)}
      </Typography>
      {conversion && (
        <Typography className="mono" sx={{ position: 'absolute', bottom: 12, fontSize: 10, fontWeight: 700, color: (stage.conversion_pct ?? 100) < 10 ? '#ff5370' : 'text.secondary', whiteSpace: 'nowrap' }}>
          {conversion}
        </Typography>
      )}
    </Box>
  );
}

function Node({ stage, index, now, onSelect }: { stage: PipelineStage; index: number; now: number; onSelect: (id: string) => void }) {
  const color = HEALTH_COLOR[stage.status];
  // An empty stage says so in words. A bare "0" is what made a stalled pipeline
  // and an unmeasured one look identical.
  const empty = stage.volume === 0;
  return (
    <Tooltip title={`${stage.label} — ${HEALTH_LABEL[stage.status]} · ${fmtRate(stage.throughput_per_min)}${stage.last_summary ? ` · ${stage.last_summary}` : ''}`}>
      <Box
        className="reveal"
        role="button"
        tabIndex={0}
        onClick={() => onSelect(stage.id)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect(stage.id); }}
        sx={{
          ['--d' as string]: `${index * 70}ms`,
          flex: '0 0 auto', width: { xs: 128, md: 148 }, height: 118, borderRadius: 3, p: 1.5,
          display: 'flex', flexDirection: 'column', justifyContent: 'space-between', position: 'relative',
          cursor: 'pointer', outline: 'none',
          background: `linear-gradient(160deg, ${color}1a, rgba(255,255,255,0.02))`,
          border: `1px solid ${color}40`,
          boxShadow: `0 0 0 1px ${color}10, 0 8px 24px rgba(0,0,0,0.35)`,
          transition: 'transform 120ms ease, box-shadow 120ms ease',
          '&:hover, &:focus-visible': { transform: 'translateY(-2px)', boxShadow: `0 0 0 1px ${color}40, 0 12px 28px rgba(0,0,0,0.45)` },
        }}
      >
        <Stack direction="row" alignItems="center" justifyContent="space-between">
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <HealthDot status={stage.status} size={8} />
            <Typography variant="caption" sx={{ fontWeight: 700, fontSize: 10.5, color: 'text.secondary' }}>
              {String(index + 1).padStart(2, '0')}
            </Typography>
          </Stack>
          <Typography variant="caption" className="mono" sx={{ fontSize: 9.5, color: 'text.disabled' }}>
            {fmtRelative(stage.last_at, now)}
          </Typography>
        </Stack>
        <Box>
          <Typography sx={{ fontWeight: 700, fontSize: 14, lineHeight: 1.1, fontFamily: '"Sora", sans-serif' }}>
            {stage.label}
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block', fontSize: 10.5 }}>
            {stage.sublabel}
          </Typography>
          {empty ? (
            <Typography variant="caption" color="text.disabled" sx={{ display: 'block', fontSize: 10 }}>
              aucun élément
            </Typography>
          ) : (
            <Stack direction="row" spacing={0.75} alignItems="baseline">
              <Typography className="mono" sx={{ fontSize: 13, fontWeight: 700 }}>
                {fmtNum(stage.volume, 0)}
              </Typography>
              {stage.dropped != null && stage.dropped > 0 && (
                <Typography className="mono" sx={{ fontSize: 10, color: '#ff5370' }}>
                  ▼{fmtNum(stage.dropped, 0)}
                </Typography>
              )}
            </Stack>
          )}
        </Box>
      </Box>
    </Tooltip>
  );
}
```

- [ ] **Step 2: Thread the new props through `PipelineFlow`**

Replace the `PipelineFlow` signature and its `stages.map` body:

```tsx
export function PipelineFlow({ stages, onSelect }: { stages: PipelineStage[]; onSelect: (id: string) => void }) {
  // One clock for the whole row: per-node Date.now() would make sibling ages
  // disagree by a few ms and re-render on every tick.
  const now = Date.now();
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', overflowX: 'auto', minWidth: 0, pb: 1, '&::-webkit-scrollbar': { height: 6 } }}>
      {stages.map((stage, i) => (
        <Box key={stage.id} sx={{ display: 'flex', alignItems: 'center' }}>
          <Node stage={stage} index={i} now={now} onSelect={onSelect} />
          {i < stages.length - 1 && <Connector stage={stages[i + 1]} />}
        </Box>
      ))}
    </Box>
  );
}
```

Keep the existing `minWidth: 0` comment block above the `Box` — it documents a real layout trap.

- [ ] **Step 3: Fix ServiceGrid for the nullable rate**

In `frontend/src/components/systems/ServiceGrid.tsx:79`, replace:

```tsx
        <Metric label="Débit" value={fmtRate(s.throughput_per_min)} color={accent} />
```

and add `import { fmtRate } from '@/lib/format';` at the top.

- [ ] **Step 4: Verify**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: the only remaining error is `command/page.tsx` not passing `onSelect` — Task 14.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/systems/PipelineFlow.tsx frontend/src/components/systems/ServiceGrid.tsx
git commit -m "feat(command): graph nodes show volume, drops and last-item age"
```

---

### Task 14: The stage drawer and the shared window selector

**Files:**
- Create: `frontend/src/components/command/WindowSelector.tsx`
- Create: `frontend/src/components/command/StageDetailDrawer.tsx`
- Modify: `frontend/src/app/(app)/command/page.tsx`
- Modify: `frontend/src/components/command/FunnelPanel.tsx:41-52`

- [ ] **Step 1: Write the window selector**

Create `frontend/src/components/command/WindowSelector.tsx`:

```tsx
'use client';
import { ToggleButton, ToggleButtonGroup } from '@mui/material';
import type { SystemsWindow } from '@/lib/types/systems';

const WINDOWS: SystemsWindow[] = ['1h', '24h', '7d'];

export function WindowSelector({ value, onChange }: { value: SystemsWindow; onChange: (w: SystemsWindow) => void }) {
  return (
    <ToggleButtonGroup
      size="small"
      exclusive
      value={value}
      // `null` arrives when the active button is clicked again; keeping the
      // current window is right, clearing it would leave the panels unlabelled.
      onChange={(_, v: SystemsWindow | null) => v && onChange(v)}
      sx={{ '& .MuiToggleButton-root': { px: 1.25, py: 0.25, fontSize: 11, borderColor: 'rgba(255,255,255,0.12)' } }}
    >
      {WINDOWS.map((w) => (
        <ToggleButton key={w} value={w} className="mono">{w}</ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}
```

- [ ] **Step 2: Write the drawer**

Create `frontend/src/components/command/StageDetailDrawer.tsx`:

```tsx
'use client';
import { useQuery } from '@tanstack/react-query';
import { Box, Chip, CircularProgress, Drawer, IconButton, Stack, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { systemsApi } from '@/lib/api/endpoints';
import { fmtNum, fmtRelative } from '@/lib/format';
import type { SystemsWindow } from '@/lib/types/systems';

export function StageDetailDrawer({
  stageId,
  window: w,
  onClose,
  onTrace,
}: {
  stageId: string | null;
  window: SystemsWindow;
  onClose: () => void;
  onTrace: (cid: string) => void;
}) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['systems', 'stage', stageId, w],
    queryFn: () => systemsApi.stage(stageId!, w),
    enabled: !!stageId,
    refetchInterval: 30000,
  });
  const now = Date.now();

  return (
    <Drawer
      anchor="right"
      open={!!stageId}
      onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', sm: 520 }, bgcolor: 'rgba(8,11,20,0.92)', backdropFilter: 'blur(16px)', p: 2.5 } }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Box>
          <Typography variant="overline" color="text.secondary">Étape · {w}</Typography>
          <Typography variant="h6">{data?.label ?? '…'}</Typography>
        </Box>
        <IconButton onClick={onClose}><CloseIcon /></IconButton>
      </Stack>

      <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
        <Box>
          <Typography variant="caption" color="text.secondary">Volume</Typography>
          <Typography className="mono" sx={{ fontSize: 20, fontWeight: 700 }}>{fmtNum(data?.volume ?? null, 0)}</Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">Écartés / en attente</Typography>
          <Typography className="mono" sx={{ fontSize: 20, fontWeight: 700, color: '#ff5370' }}>{fmtNum(data?.dropped ?? null, 0)}</Typography>
        </Box>
      </Stack>

      <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
        {data?.breakdown.map((b) => (
          <Chip key={b.key} size="small" variant="outlined" label={`${b.key} (${b.count})`} sx={{ fontSize: 10, height: 22 }} />
        ))}
      </Stack>

      {/* Three distinct states. An error that renders as an empty list would say
          "nothing crossed this stage", which is a different fact entirely. */}
      {isLoading && <Stack alignItems="center" sx={{ py: 4 }}><CircularProgress size={22} /></Stack>}
      {isError && (
        <Stack spacing={1} sx={{ py: 3 }}>
          <Typography variant="body2" color="error">Chargement impossible.</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ cursor: 'pointer', textDecoration: 'underline' }} onClick={() => refetch()}>
            Réessayer
          </Typography>
        </Stack>
      )}
      {data && !data.items.length && !isError && (
        <Typography variant="body2" color="text.secondary" sx={{ py: 3 }}>
          Aucun élément sur cette fenêtre.
        </Typography>
      )}

      <Stack spacing={1.25}>
        {data?.items.map((item, i) => (
          <Box
            key={`${item.at}-${i}`}
            onClick={() => item.correlation_id && onTrace(item.correlation_id)}
            sx={{
              p: 1.25, borderRadius: 2, border: '1px solid rgba(255,255,255,0.08)',
              cursor: item.correlation_id ? 'pointer' : 'default',
              '&:hover': item.correlation_id ? { borderColor: 'primary.main' } : undefined,
            }}
          >
            <Stack direction="row" justifyContent="space-between" alignItems="center">
              <Typography variant="body2" sx={{ fontWeight: 600 }}>{item.summary}</Typography>
              <Typography variant="caption" color="text.secondary" className="mono">{fmtRelative(item.at, now)}</Typography>
            </Stack>
            <Stack direction="row" flexWrap="wrap" useFlexGap sx={{ gap: 0.5, mt: 0.5 }}>
              {Object.entries(item.detail).map(([k, v]) => (
                <Chip key={k} size="small" variant="outlined" className="mono" label={`${k}: ${v ?? '—'}`} sx={{ height: 18, fontSize: 9.5 }} />
              ))}
            </Stack>
          </Box>
        ))}
      </Stack>
    </Drawer>
  );
}
```

- [ ] **Step 3: Make FunnelPanel take the window as a prop**

In `frontend/src/components/command/FunnelPanel.tsx`, change the component signature and query (lines 41-46):

```tsx
export function FunnelPanel({ window: w = '24h' }: { window?: SystemsWindow }) {
  const { data } = useQuery({
    queryKey: ['systems', 'funnel', w],
    queryFn: () => systemsApi.funnel(w),
    refetchInterval: 30000,
  });
```

and add `import type { SystemsWindow } from '@/lib/types/systems';` to the existing type import line.

- [ ] **Step 4: Wire the page**

In `frontend/src/app/(app)/command/page.tsx`, add the imports

```tsx
import { StageDetailDrawer } from '@/components/command/StageDetailDrawer';
import { WindowSelector } from '@/components/command/WindowSelector';
import type { SystemsWindow } from '@/lib/types/systems';
```

add the state next to `traceCid` (line 32) — the variable is `range`, **not** `window`: a state named `window` shadows the DOM global inside this client component:

```tsx
  const [range, setRange] = useState<SystemsWindow>('24h');
  const [stageId, setStageId] = useState<string | null>(null);
```

make the systems query window-aware (line 36):

```tsx
  const systems = useQuery({
    queryKey: ['systems', 'overview', range],
    queryFn: () => systemsApi.overview(range),
    refetchInterval: 8000,
  });
```

replace the pipeline `Box` (lines 48-50):

```tsx
          <Box className="cmi-glass reveal" sx={{ borderRadius: 3, p: 2 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
              <Typography variant="overline" color="text.secondary">
                Pipeline · {systems.data?.pipeline_window ?? range}
                {systems.data?.pipeline_stale ? ' · données en cache' : ''}
              </Typography>
              <WindowSelector value={range} onChange={setRange} />
            </Stack>
            {systems.data && <PipelineFlow stages={systems.data.pipeline} onSelect={setStageId} />}
          </Box>
```

pass the window to the funnel (line 55): `<FunnelPanel window={range} />`

and mount the drawer next to the trace drawer (line 64):

```tsx
      <StageDetailDrawer
        stageId={stageId}
        window={range}
        onClose={() => setStageId(null)}
        onTrace={setTraceCid}
      />
```

Add `Typography` to the existing `@mui/material` import.

- [ ] **Step 5: Verify**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: both clean.

Then run: `cd frontend && npm run dev` with `NEXT_PUBLIC_USE_MOCK=1`, open `http://localhost:3000/command`, and confirm: the Sentiment connector reads `—/m` (unknown), the Exécution node reads "aucun élément" (a real zero), switching to `7d` updates both the graph and the Entonnoir, and clicking Triage opens the drawer with items whose rows open the trace.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/command frontend/src/app/\(app\)/command/page.tsx
git commit -m "feat(command): per-stage drawer and a window selector shared with the funnel"
```

---

### Task 15: Live plausibility checks

**Files:**
- Modify: `scripts/verify_read_live.py`

The harness already has the right shape: `_check()` validates keys against `CONTRACT`, and a `PLAUSIBILITY` dict maps an endpoint name to `(predicate, message)` pairs that must hold. Both new checks slot into that structure.

- [ ] **Step 1: Add the predicates**

In `scripts/verify_read_live.py`, next to `_has_rows` / `_any_sentiment` / `_any_liquidity` (around lines 39-55):

```python
def _pipeline_is_measured(resp) -> bool:
    """At least one stage must have moved something, or moved it recently.

    A shape check cannot catch the failure this endpoint exists for: seven
    stages of well-formed zeros is exactly what the broken metric name produced.
    """
    return any(
        (s.get("volume") or 0) > 0 or s.get("last_at") is not None
        for s in resp.get("pipeline", [])
    )


def _throughput_is_not_all_null(resp) -> bool:
    """Null everywhere means the /metrics scrape never produced two samples —
    honest, but still a broken observability path worth failing on."""
    return any(
        s.get("throughput_per_min") is not None for s in resp.get("pipeline", [])
    )
```

- [ ] **Step 2: Register them in `PLAUSIBILITY`**

Add an entry to the `PLAUSIBILITY` dict, alongside the existing `"market/tokens"` one:

```python
    "systems/overview": [
        (_pipeline_is_measured, "every pipeline stage is empty — the pipeline is "
                                "stopped, or the stage aggregates are broken"),
        (_throughput_is_not_all_null, "no stage has a throughput — the /metrics "
                                      "scrape never yielded two samples"),
    ],
```

- [ ] **Step 3: Pass the window explicitly and add the stage call**

In `main()`, replace the `systems/overview` entry (line 96) and add the stage-detail call right after it. **`window` must be passed explicitly**: calling the handler directly bypasses FastAPI, so the `Query(...)` default object would reach `_cutoffs()` and raise `KeyError`.

```python
            ("systems/overview", read_api.systems_overview(window="24h", session=s)),
            ("systems/stage", read_api.systems_stage(
                stage_id="triage", window="24h", limit=5, session=s)),
```

- [ ] **Step 4: Assert the 404**

After the `for name, coro in calls:` loop, before the `return`:

```python
        # An unknown stage must 404 rather than return an empty payload: an
        # empty drawer reads as "nothing crossed this stage", which is a
        # different fact entirely.
        try:
            await read_api.systems_stage(
                stage_id="nope", window="24h", limit=5, session=s
            )
            print("FAIL systems/stage  ->  unknown id did not raise 404")
            failures.append("systems/stage/404")
        except HTTPException as exc:
            if exc.status_code != 404:
                print(f"FAIL systems/stage  ->  unknown id returned {exc.status_code}")
                failures.append("systems/stage/404")
            else:
                print("OK   systems/stage/404")
```

Add `from fastapi import HTTPException` to the imports at the top.

- [ ] **Step 5: Verify**

Run: `docker compose exec api-gateway python scripts/verify_read_live.py` against a running stack.
Expected: exit 0, with `OK systems/overview` and `OK systems/stage/404`. A `THIN systems/overview` line means the pipeline really is empty — investigate before dismissing it, that is the message doing its job.

- [ ] **Step 6: Commit**

```bash
git add scripts/verify_read_live.py
git commit -m "test(read-plane): assert the pipeline graph reports real volumes"
```

---

### Task 16: Full verification

- [ ] **Step 1: Lint and type-check everything**

Run: `make lint`
Expected: ruff, black and mypy clean. Fix anything reported.

- [ ] **Step 2: Run the whole Python suite**

Run: `make test`
Expected: all green. Pay attention to `tests/test_api_gateway_read.py` and `tests/test_read_contract.py` — they cover the endpoints that changed shape.

- [ ] **Step 3: Front-end checks**

Run: `cd frontend && npm run typecheck && npm run lint && npm run build`
Expected: all three clean.

- [ ] **Step 4: End-to-end against the live stack**

Run: `make up && make migrate`, then:

```bash
curl -s "http://localhost:8000/systems/overview?window=24h" | python -m json.tool | head -60
curl -s "http://localhost:8000/systems/stage/triage?window=24h&limit=5" | python -m json.tool
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8000/systems/stage/nope"
```

Expected: stage volumes that match `curl -s "http://localhost:8000/systems/funnel?window=24h"` for `analyses`/`escalated`/`decisions`/`approved`/`executed`; a `404` for the unknown stage. If the two endpoints disagree on a stage, that is a real bug — the whole point of reusing the funnel's definitions is that they cannot.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix(systems): address verification findings"
```

---

## Notes for the implementer

- **The one rule that matters:** never turn an unknown into a `0`. Both the metric fix and the null-propagation exist for that reason. If a defensive `or 0` creeps into the counts path, the original bug is back in a new disguise.
- **Two time zones in one query set:** `raw_content.fetched_at` / `scored_at` are tz-aware; every other time column in this feature is naive UTC. `_cutoffs()` returns both — use the right one or SQLAlchemy raises at query time.
- **Async tests need no marker** (`asyncio_mode = "auto"`), but never import a service's `app` package bare — `tests/conftest.py` fails the run if you do. Always use `service_modules.load_service_module`.
- **Calling a handler directly bypasses FastAPI's defaults.** `window: str = Query("24h", …)` means a direct call like `read_api.systems_overview(session=s)` passes the `Query` object itself, not `"24h"` — and `_cutoffs()` then raises a `KeyError`. Offline tests and `scripts/verify_read_live.py` must pass `window="24h"` explicitly.
