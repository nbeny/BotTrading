"""Assemblage du dossier token — fonctions pures, sans base.

Le test central est celui de l'axe absent : le scoring v2 renormalise sur le
poids présent, donc un axe non mesuré doit être *exclu* du dict, jamais présent
à 0.0. Une valeur non mesurée qui fuit en lecture confiante déplace toujours le
score dans la direction de cette lecture.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from service_modules import load_service_module

dossier = load_service_module("api-gateway", "dossier")

NOW = datetime(2026, 8, 1, 9, 12, tzinfo=timezone.utc)


def _journal(**kw):
    base = dict(
        symbol="SOL",
        time=NOW,
        factors={
            "volume_growth": 0.81,
            "social_score": 0.74,
            "news_score": 0.60,
            "market_trend": 0.88,
            "liquidity_score": 0.70,
            "positioning": 0.93,
        },
        score=84,
        confidence=0.62,
        dominant_factor="positioning",
        dominant_factor_share=0.28,
        escalated=True,
        sonnet_called=True,
        sonnet_validated=False,
        skip_reason=None,
        decision_event_id=None,
        risk_verdict=None,
        risk_reason=None,
        execution_event_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_measured_axes_are_reported_with_their_value() -> None:
    score = dossier.build_score(_journal())
    assert score["value"] == 84
    assert score["confidence"] == 0.62
    assert score["axes"]["positioning"] == 0.93
    assert score["axes_total"] == 7


def test_an_unmeasured_axis_is_absent_not_zero() -> None:
    score = dossier.build_score(_journal())
    assert "fundamentals" not in score["axes"], (
        "un axe non mesuré doit être absent du dict : présent à 0.0 il serait "
        "compté comme une mesure au pire, ce que la renormalisation interdit"
    )
    assert len(score["axes"]) == 6


def test_an_axis_explicitly_null_is_treated_as_absent() -> None:
    score = dossier.build_score(_journal(factors={"volume_growth": None}))
    assert score["axes"] == {}


def test_a_measured_zero_is_kept() -> None:
    score = dossier.build_score(_journal(factors={"volume_growth": 0.0}))
    assert score["axes"] == {"volume_growth": 0.0}


def test_no_journal_reports_unknown_not_zero() -> None:
    score = dossier.build_score(None)
    assert score["value"] is None
    assert score["confidence"] is None
    assert score["axes"] == {}
    assert score["axes_total"] == 7
    assert score["computed_at"] is None
