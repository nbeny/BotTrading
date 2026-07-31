"""Pipeline stage shaping: pure, no database.

The panel exists because the graph reported 0/m everywhere while the pipeline
was alive. The cases below pin the one rule that matters: an unmeasured value
stays None all the way out, and is never smoothed into a zero.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

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


def test_decision_summary_reads_confidence_as_a_fraction() -> None:
    """`confidence` is written as 0..1 by decision-engine's scorer. Pinning the
    rendering here is what would catch a future switch to a 0..100 scale, which
    would otherwise ship as a confident "8100%" nobody notices until support
    screenshots it."""
    row = SimpleNamespace(symbol="SOL", direction="long", confidence=0.81)
    assert sp.summarize_decision(row) == "SOL · long · confiance 81%"


def test_approved_summary_reads_position_size_as_a_fraction() -> None:
    """risk-engine writes a size fraction bounded by max_position_pct (0.05),
    never a percentage — same factor-of-100 trap as confidence."""
    row = SimpleNamespace(symbol="SOL", direction="long", position_size_pct=0.05)
    assert sp.summarize_approved(row) == "SOL · long · taille 5.0%"


def test_journal_summary_says_dash_where_sonnet_returned_nothing() -> None:
    """Both columns are nullable: Sonnet can be budget-skipped after escalation,
    which is the measured production bottleneck. A fabricated 0 would read as a
    real verdict of zero."""
    row = SimpleNamespace(symbol="SOL", sonnet_direction=None, sonnet_score=None)
    assert sp.summarize_journal(row) == "SOL · Sonnet — · score —"


def test_scored_summary_says_dash_where_the_model_abstained() -> None:
    row = SimpleNamespace(source="reddit", sentiment_score=None)
    assert sp.summarize_scored(row) == "reddit · sentiment —"


def test_content_summary_truncates_a_long_title_rather_than_wrapping() -> None:
    row = SimpleNamespace(source="rss", kind="news", title="x" * 80, text="")
    out = sp.summarize_content(row)
    assert out.startswith("rss · news · ")
    assert out.endswith("…")
    assert len(out.split(" · ")[-1]) == 61  # 60 chars plus the ellipsis


def test_content_summary_falls_back_to_body_when_there_is_no_title() -> None:
    row = SimpleNamespace(source="reddit", kind="social", title=None,
                          text="BTC breaking 70k")
    assert sp.summarize_content(row) == "reddit · social · BTC breaking 70k"


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


async def test_the_ttl_boundary_expires_rather_than_extends() -> None:
    """Exactly TTL-old counts as expired. Either choice is defensible, so the
    point is that it is pinned: an off-by-one here decides whether a poller at
    the 30s mark re-queries or serves a value it can no longer vouch for."""
    fetch = _Recorder({"triage": sp.StageCounts(volume=7)})
    await sp.stage_counts_cached(None, "24h", now=0.0, fetch=fetch)
    await sp.stage_counts_cached(None, "24h", now=sp.CACHE_TTL_S, fetch=fetch)
    assert fetch.calls == 2


async def test_a_caller_mutating_the_result_cannot_corrupt_the_cache() -> None:
    """The cache hands out copies. Without that, one caller dropping a stage key
    would silently narrow what every other open terminal reads next."""
    fetch = _Recorder({"triage": sp.StageCounts(volume=7)})
    first, _ = await sp.stage_counts_cached(None, "24h", now=0.0, fetch=fetch)
    first.pop("triage")
    second, _ = await sp.stage_counts_cached(None, "24h", now=1.0, fetch=fetch)
    assert second["triage"].volume == 7


def test_window_hours_covers_exactly_the_three_supported_windows() -> None:
    assert sp.WINDOW_HOURS == {"1h": 1, "24h": 24, "7d": 168}


def test_every_stage_reports_a_count_for_the_ids_in_the_catalog() -> None:
    """A stage in the catalog with no aggregate would render a permanent blank
    that looks exactly like a dead stage."""
    assert set(sp.STAGE_IDS) == {
        "collect", "sentiment", "triage", "senior", "decision", "risk", "execute",
    }


class _AggResult:
    """Stands in for an `AsyncSession.execute` result: a count query reads
    `scalar_one()`, a "latest row" query reads `scalars().first()`. Values are
    fixed by the test, not derived from the statement, so an implementation
    that queries the wrong table or the wrong predicate produces a wrong
    number instead of being echoed back a right one."""

    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one(self):
        return self._scalar

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeAggSession:
    """Replays a fixed, ordered queue of query results. Order follows
    `fetch_stage_counts`'s own call order (collect, sentiment, triage, senior,
    decision, risk, execute — count(s) then "latest row" per stage); popping
    from an emptied queue fails loudly rather than padding with silent zeros."""

    def __init__(self, queue):
        self._queue = list(queue)

    async def execute(self, _stmt):
        return self._queue.pop(0)


def _agg_queue():
    content_row = SimpleNamespace(
        fetched_at=NOW, source="rss", kind="news", title="BTC pumping", text="",
    )
    scored_row = SimpleNamespace(scored_at=NOW, source="reddit", sentiment_score=0.42)
    signal_row = SimpleNamespace(
        time=NOW, symbol="SOL", opportunity_score=72, escalated=True,
        block_reason="unknown",
    )
    decision_row = SimpleNamespace(
        created_at=NOW, symbol="BTC", direction="long", confidence=0.81,
    )
    approved_row = SimpleNamespace(
        created_at=NOW, symbol="BTC", direction="long", position_size_pct=0.05,
    )
    executed_row = SimpleNamespace(
        created_at=NOW, symbol="BTC", direction="long", status="filled",
        fill_price=101.5, pnl=12.0,
    )
    return [
        _AggResult(scalar=3),  # collect: prices count
        _AggResult(scalar=2),  # collect: content count
        _AggResult(rows=[content_row]),  # collect: last content row
        _AggResult(scalar=4),  # sentiment: scored count
        _AggResult(scalar=10),  # sentiment: backlog (unwindowed)
        _AggResult(rows=[scored_row]),  # sentiment: last scored row
        _AggResult(scalar=7),  # triage: analyses count
        _AggResult(scalar=2),  # triage: not-escalated count
        _AggResult(rows=[signal_row]),  # triage: last signal row
        _AggResult(scalar=0),  # senior: escalated count (measured zero)
        _AggResult(scalar=0),  # senior: budget-skipped count
        _AggResult(rows=[]),  # senior: no journal row in the window
        _AggResult(scalar=3),  # decision: decisions count
        _AggResult(scalar=1),  # decision: rejected count
        _AggResult(rows=[decision_row]),  # decision: last decision row
        _AggResult(scalar=2),  # risk: approved count
        _AggResult(scalar=0),  # risk: rejected count
        _AggResult(rows=[approved_row]),  # risk: last approved row
        _AggResult(scalar=1),  # execute: executed count
        _AggResult(scalar=0),  # execute: failed count
        _AggResult(rows=[executed_row]),  # execute: last executed row
    ]


async def test_fetch_stage_counts_reports_every_stage_from_real_and_zero_rows() -> None:
    counts = await sp.fetch_stage_counts(_FakeAggSession(_agg_queue()), "24h")

    assert set(counts) == set(sp.STAGE_IDS)

    # A stage with rows: real volume, and a last_summary built from the row.
    assert counts["collect"].volume == 5  # 3 prices + 2 content rows
    assert counts["collect"].last_summary == "rss · news · BTC pumping"
    assert counts["execute"].last_summary == "BTC · long · filled @ 101.5 · PnL 12.0"

    # A stage with no rows: a genuine measured zero, not an absent value.
    assert counts["senior"].volume == 0
    assert counts["senior"].last_at is None
    assert counts["senior"].last_summary is None


async def test_fetch_stage_counts_returns_a_fresh_dict_every_call() -> None:
    first = await sp.fetch_stage_counts(_FakeAggSession(_agg_queue()), "24h")
    second = await sp.fetch_stage_counts(_FakeAggSession(_agg_queue()), "24h")

    assert first is not second
    assert first == second  # same measured values...
    first.pop("collect")
    assert "collect" in second  # ...but popping one never touches the other
