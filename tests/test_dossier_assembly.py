"""Assemblage du dossier token — fonctions pures, sans base.

Le test central est celui de l'axe absent : le scoring v2 renormalise sur le
poids présent, donc un axe non mesuré doit être *exclu* du dict, jamais présent
à 0.0. Une valeur non mesurée qui fuit en lecture confiante déplace toujours le
score dans la direction de cette lecture.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from service_modules import load_service_module

dossier = load_service_module("api-gateway", "dossier")

NOW = datetime(2026, 8, 1, 9, 12, tzinfo=UTC)


def _decision(**kw):
    """Une ligne `decisions`. `payload` est le DecisionEvent sérialisé, dont
    `meta.breakdown` porte la décomposition v2 — voir engine.py:190."""
    breakdown = kw.pop(
        "breakdown",
        {
            "volume_growth": 0.81,
            "social_score": 0.74,
            "news_score": 0.60,
            "market_trend": 0.88,
            "liquidity_score": 0.70,
            "positioning": 0.93,
        },
    )
    base = dict(
        symbol="SOL",
        created_at=NOW,
        opportunity_score=84,
        confidence=0.62,
        payload={"meta": {"breakdown": breakdown}},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_measured_axes_are_reported_with_their_value() -> None:
    score = dossier.build_score(_decision())
    assert score["value"] == 84
    assert score["confidence"] == 0.62
    assert score["axes"]["positioning"] == 0.93
    # Huit depuis developer_activity: axes_total est len(AXIS_KEYS), donc
    # ce chiffre bouge avec la liste et pas avec ce qui est mesure.
    assert score["axes_total"] == 8
    assert score["insufficient_evidence"] is False


def test_an_unmeasured_axis_is_absent_not_zero() -> None:
    score = dossier.build_score(_decision())
    assert "fundamentals" not in score["axes"], (
        "un axe non mesuré doit être absent du dict : présent à 0.0 il serait "
        "compté comme une mesure au pire, ce que la renormalisation interdit"
    )
    assert len(score["axes"]) == 6


def test_an_axis_explicitly_null_is_treated_as_absent() -> None:
    score = dossier.build_score(_decision(breakdown={"volume_growth": None}))
    assert score["axes"] == {}


def test_a_measured_zero_is_kept() -> None:
    score = dossier.build_score(_decision(breakdown={"volume_growth": 0.0}))
    assert score["axes"] == {"volume_growth": 0.0}
    assert score["insufficient_evidence"] is False


def test_the_haiku_four_factor_keys_are_not_mistaken_for_axes() -> None:
    """`DecisionJournal.factors` porte momentum/volume/sentiment/liquidity — le
    triage Haiku, pas les sept axes. Lire cet espace-là donnerait sept tirets en
    permanence ; ce test fige la distinction."""
    score = dossier.build_score(
        _decision(breakdown={"momentum": 0.9, "volume": 0.8, "sentiment": 0.7})
    )
    assert score["axes"] == {}
    assert score["insufficient_evidence"] is True


def test_an_empty_breakdown_is_insufficient_evidence_not_a_zero_score() -> None:
    """Sous `_MIN_PRESENT_WEIGHT`, scoring.py renvoie `ScoreResult(0, 0.0, {})`.
    Ce 0 n'est pas une mesure et ne doit jamais s'afficher comme telle."""
    score = dossier.build_score(
        _decision(breakdown={}, opportunity_score=0, confidence=0.0)
    )
    assert score["insufficient_evidence"] is True
    assert score["value"] is None
    assert score["confidence"] is None
    assert score["computed_at"] is not None


def test_every_missing_link_in_the_payload_chain_degrades_to_insufficient() -> None:
    """`payload` est une colonne JSONB dont le défaut est `{}`, et rien ne
    garantit qu'un producteur y ait mis `meta.breakdown`. Chaque maillon absent
    doit retomber sur « preuves insuffisantes » sans lever — un `AttributeError`
    ici ferait échouer le dossier entier d'un token pour un champ décoratif.
    """
    for payload in (
        None,
        {},
        {"meta": None},
        {"meta": {}},
        {"meta": {"breakdown": None}},
    ):
        score = dossier.build_score(_decision(payload=payload))
        assert score["axes"] == {}, payload
        assert score["insufficient_evidence"] is True, payload
        assert score["value"] is None, payload
        assert score["confidence"] is None, payload


def test_no_decision_reports_unknown_not_zero() -> None:
    score = dossier.build_score(None)
    assert score["value"] is None
    assert score["confidence"] is None
    assert score["axes"] == {}
    # Huit depuis developer_activity: axes_total est len(AXIS_KEYS), donc
    # ce chiffre bouge avec la liste et pas avec ce qui est mesure.
    assert score["axes_total"] == 8
    assert score["computed_at"] is None
    assert score["insufficient_evidence"] is False, (
        "aucune décision n'est pas la même chose que des preuves insuffisantes : "
        "dans le premier cas rien n'a été tenté"
    )


def _journal(**kw):
    """Une ligne `decision_journal`. Source du *parcours* uniquement — sa
    colonne `factors` porte le triage Haiku à quatre facteurs, pas les sept
    axes, et n'est donc jamais lue par le dossier."""
    base = {
        "symbol": "SOL",
        "time": NOW,
        "escalated": True,
        "sonnet_called": True,
        "sonnet_validated": False,
        "skip_reason": None,
        "decision_event_id": None,
        "risk_verdict": None,
        "risk_reason": None,
        "execution_event_id": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _rejection(**kw):
    """`stage` porte la SOURCE de l'événement (`decision_engine`/`risk_engine`),
    jamais un id d'étage : c'est ce que `persister.stage_for` écrit réellement."""
    base = {
        "symbol": "SOL",
        "time": NOW,
        "stage": "risk_engine",
        "reason": "max_exposure",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_execution_reached_is_reported_as_execute() -> None:
    v = dossier.build_pipeline(_journal(execution_event_id="x1"), None)
    assert v["reached_stage"] == "execute"
    assert v["blocked_at"] is None
    assert v["block_reason"] is None


def test_risk_rejection_names_the_stage_and_the_reason() -> None:
    v = dossier.build_pipeline(
        _journal(risk_verdict="rejected", risk_reason="score_below_threshold"), None
    )
    assert v["reached_stage"] == "risk"
    assert v["blocked_at"] == "risk"
    assert v["block_reason"] == "score_below_threshold"


def test_risk_approval_is_not_a_block() -> None:
    v = dossier.build_pipeline(_journal(risk_verdict="approved"), None)
    assert v["reached_stage"] == "risk"
    assert v["blocked_at"] is None
    assert v["block_reason"] is None


def test_triage_refusal_is_a_block_at_triage() -> None:
    """`skip_reason` reste `None` sur un refus de triage réel — ai-worker-sonnet
    ne le renseigne que sur la branche escaladée. C'est donc le repli
    `not_escalated` qui s'observe en production, et lui qu'il faut couvrir."""
    v = dossier.build_pipeline(
        _journal(escalated=False, sonnet_called=False, skip_reason=None), None
    )
    assert v["reached_stage"] == "triage"
    assert v["blocked_at"] == "triage"
    assert v["block_reason"] == "not_escalated"


def test_an_explicit_skip_reason_wins_over_the_fallback() -> None:
    v = dossier.build_pipeline(
        _journal(escalated=False, sonnet_called=False, skip_reason="score_too_low"),
        None,
    )
    assert v["block_reason"] == "score_too_low"


def test_a_published_decision_awaiting_risk_reports_the_decision_stage() -> None:
    v = dossier.build_pipeline(_journal(decision_event_id="d1"), None)
    assert v["reached_stage"] == "decision"
    assert v["blocked_at"] is None


def test_escalated_without_a_sonnet_call_stops_at_triage_without_a_block() -> None:
    """Le cas `cooldown_or_budget` : Haiku a escaladé, Sonnet n'a pas été
    appelé. Rien n'a refusé le signal — il a été mis de côté, ce qui n'est pas
    la même chose et ne doit pas s'afficher comme un blocage."""
    v = dossier.build_pipeline(_journal(escalated=True, sonnet_called=False), None)
    assert v["reached_stage"] == "triage"
    assert v["blocked_at"] is None
    assert v["block_reason"] is None


def test_escalated_but_undecided_claims_no_block() -> None:
    """Sonnet appelé sans décision en aval : en vol ou abandonné, on ne peut pas
    trancher. Affirmer un blocage serait inventer une mesure."""
    v = dossier.build_pipeline(_journal(), None)
    assert v["reached_stage"] == "senior"
    assert v["blocked_at"] is None
    assert v["block_reason"] is None


def test_rejection_without_journal_is_the_fallback() -> None:
    v = dossier.build_pipeline(None, _rejection())
    assert v["reached_stage"] == "risk", "risk_engine doit être normalisé en risk"
    assert v["blocked_at"] == "risk"
    assert v["block_reason"] == "max_exposure"
    assert v["escalated"] is None, (
        "sans ligne de journal on ignore si Haiku avait escaladé : `False` "
        "serait une supposition déguisée en mesure"
    )
    assert v["sonnet_called"] is None


def test_a_decision_engine_rejection_is_normalised_too() -> None:
    v = dossier.build_pipeline(None, _rejection(stage="decision_engine"))
    assert v["reached_stage"] == "decision"


def test_an_unmapped_rejector_stays_visible_under_its_own_name() -> None:
    """`stage_for` laisse passer une source inconnue plutôt que de la masquer ;
    la normalisation doit avoir le même réflexe."""
    v = dossier.build_pipeline(None, _rejection(stage="some_new_service"))
    assert v["reached_stage"] == "some_new_service"


def test_nothing_known_reports_nulls() -> None:
    v = dossier.build_pipeline(None, None)
    assert v == {
        "reached_stage": None,
        "blocked_at": None,
        "block_reason": None,
        "escalated": None,
        "sonnet_called": None,
        "sonnet_validated": None,
        "last_event_at": None,
    }
