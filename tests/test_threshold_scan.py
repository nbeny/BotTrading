"""L'analyse du scan de seuil, pure : mêmes verdicts que le CLI, sans base.

Le garde central de ce module est le refus : un axe muet doit empêcher toute
proposition de seuil, parce qu'un axe absent est EXCLU du dénominateur de
renormalisation, pas noté zéro -- un seuil calibré ainsi vaudrait pour un
modèle amputé de ce poids.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from service_modules import load_service_module

ts = load_service_module("decision-engine", "threshold_scan")

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _full_scan(total: int = 1000) -> "ts.Scan":
    """Un scan où les huit axes sont largement présents et le régime lu."""
    scan = ts.Scan(since=NOW - timedelta(days=7))
    scan.total = total
    scan.regime_seen = total
    scan.min_time = NOW - timedelta(days=7)
    scan.min_time_with_regime = NOW - timedelta(days=7)
    for axis in ts.AXIS_PROBE:
        scan.presence[axis] = total
    scan.score_counts = Counter({50: total // 2, 80: total // 2})
    scan.confidence_pass_counts = Counter({80: total // 2})
    scan.by_day = Counter({(NOW - timedelta(days=d)).date().isoformat(): total // 7 for d in range(7)})
    scan.best_by_symbol_day = {("BTC", "2026-08-07"): 80}
    return scan


def test_full_scan_proposes_a_threshold() -> None:
    report = ts.analyze(_full_scan(), days=7, target_per_day=200, now=NOW)
    assert report.refusal is None
    assert report.proposal is not None
    assert report.proposal["threshold"] > 0
    assert len(report.axes) == len(ts.AXIS_PROBE)
    assert all(not a["mute"] for a in report.axes)


def test_axes_are_ordered_by_weight_desc() -> None:
    report = ts.analyze(_full_scan(), days=7, target_per_day=200, now=NOW)
    weights = [a["weight"] for a in report.axes]
    assert weights == sorted(weights, reverse=True)


def test_mute_axis_refuses_and_proposes_nothing() -> None:
    scan = _full_scan()
    scan.presence["positioning"] = 0          # le cas du 2026-08-04
    report = ts.analyze(scan, days=7, target_per_day=200, now=NOW)
    assert report.refusal is not None
    assert report.refusal["code"] == "MUTE_AXES"
    assert "positioning" in report.refusal["title"]
    # Le texte qui distingue « collecteur casse » de « axe legitimement rare »
    # est la valeur du refus : il doit voyager avec lui.
    assert "collecte" in report.refusal["detail"]
    assert "fundamentals" in report.refusal["detail"]
    assert report.proposal is None


def test_raw_count_travels_with_the_percentage() -> None:
    """1 ligne sur 1 281 511 s'affiche « 0.0% » : le compte brut doit suivre."""
    scan = _full_scan(total=1_281_511)
    scan.presence["positioning"] = 1
    report = ts.analyze(scan, days=7, target_per_day=200, now=NOW)
    positioning = next(a for a in report.axes if a["key"] == "positioning")
    assert positioning["seen"] == 1
    assert positioning["mute"] is True


def test_absent_regime_refuses() -> None:
    scan = _full_scan()
    scan.regime_seen = 0
    scan.min_time_with_regime = None
    report = ts.analyze(scan, days=7, target_per_day=200, now=NOW)
    assert report.refusal["code"] == "NO_REGIME"
    assert report.proposal is None


def test_regime_gap_refuses_and_suggests_a_shorter_window() -> None:
    scan = _full_scan()
    scan.min_time_with_regime = NOW - timedelta(days=2)   # journalise depuis 2j
    report = ts.analyze(scan, days=7, target_per_day=200, now=NOW)
    assert report.refusal["code"] == "REGIME_GAP"
    assert report.refusal["suggested_days"] == 2
    assert report.proposal is None


def test_empty_window_refuses_rather_than_dividing_by_zero() -> None:
    report = ts.analyze(ts.Scan(since=NOW - timedelta(days=7)), days=7, target_per_day=200, now=NOW)
    assert report.refusal is not None
    assert report.window["total"] == 0
    assert report.proposal is None


def test_report_serialises_to_json_safe_primitives() -> None:
    import json

    report = ts.analyze(_full_scan(), days=7, target_per_day=200, now=NOW)
    json.dumps(report.to_payload())          # ne doit pas lever
