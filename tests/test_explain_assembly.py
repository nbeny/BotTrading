"""Pure assembly for /decisions/{id}/explain — SimpleNamespace rows, no DB."""

from types import SimpleNamespace

from service_modules import load_service_module

explain = load_service_module("api-gateway", "explain")


def _decision(**kw):
    base = dict(
        event_id="d-1",
        symbol="SOL",
        direction="long",
        opportunity_score=64,
        confidence=0.58,
        payload={"meta": {"breakdown": {"volume_growth": 0.8}}},
        correlation_id="cid-1",
        created_at=None,
        rationale="ok",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _journal(**kw):
    base = dict(
        event_id="j-1",
        symbol="SOL",
        score=64,
        confidence=0.58,
        factors={"momentum": 0.7, "volume": 0.5},
        dominant_factor="momentum",
        escalated=True,
        sonnet_called=True,
        sonnet_validated=True,
        sonnet_score=70,
        sonnet_direction="long",
        skip_reason=None,
        risk_verdict="rejected",
        risk_reason="score 64 < floor 70",
        correlation_id="cid-1",
        decision_event_id="d-1",
        time=None,
        execution_event_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_full_row_assembly() -> None:
    out = explain.build_explain(
        "d-1",
        decision=_decision(),
        journal=_journal(),
        rejection=None,
        trace={"correlation_id": "cid-1", "symbol": "SOL", "stages": []},
        counterfactual={"horizon": "4h", "pnl_pct": 2.1, "outcome": "take_profit"},
    )
    assert out["id"] == "d-1"
    assert out["symbol"] == "SOL"
    assert out["direction"] == "long"
    assert out["score"]["value"] == 64.0  # échelle brute 0-100, jamais /100
    assert out["score"]["axes"] == {"volume_growth": 0.8}
    assert out["triage"]["factors"] == {"momentum": 0.7, "volume": 0.5}
    assert out["risk"] == {"verdict": "rejected", "reason": "score 64 < floor 70"}
    assert out["counterfactual"]["pnl_pct"] == 2.1
    assert out["correlation_id"] == "cid-1"


def test_journal_only_row_pre_v2() -> None:
    """Décision rejetée : pas de ligne decisions, donc pas de breakdown."""
    out = explain.build_explain(
        "j-1",
        decision=None,
        journal=_journal(decision_event_id=None),
        rejection=None,
        trace=None,
        counterfactual=None,
    )
    # aucune décision ≠ preuves insuffisantes : value/axes nuls sont le signal
    assert out["score"]["insufficient_evidence"] is False
    assert out["score"]["value"] is None
    assert out["score"]["axes"] == {}
    assert out["symbol"] == "SOL"
    assert out["direction"] is None
    assert out["trace"] is None


def test_nothing_found_is_callers_problem() -> None:
    """build_explain n'invente rien : au moins une source non nulle requise."""
    out = explain.build_explain(
        "x",
        decision=None,
        journal=None,
        rejection=None,
        trace=None,
        counterfactual=None,
    )
    assert out["symbol"] is None
    assert out["triage"] is None
    assert out["risk"] is None
