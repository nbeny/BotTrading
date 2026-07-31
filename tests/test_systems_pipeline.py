"""Pipeline stage shaping: pure, no database.

The panel exists because the graph reported 0/m everywhere while the pipeline
was alive. The cases below pin the one rule that matters: an unmeasured value
stays None all the way out, and is never smoothed into a zero.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

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
