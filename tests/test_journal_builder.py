"""Construction de l'entrée de journal à partir d'une AnalysisEvent."""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events import AnalysisEvent

jb = load_service_module("ai-worker-sonnet", "journal")


def _analysis(**kw) -> AnalysisEvent:
    base = dict(
        symbol="BTC",
        opportunity_score=42,
        confidence=0.7,
        reason="r",
        factors_present=3,
        meta={
            "factors": {
                "momentum": 0.8,
                "volume": 0.2,
                "sentiment": 0.4,
                "liquidity": 0.5,
            },
            "features": {"market_cap_rank": 150},
        },
    )
    base.update(kw)
    return AnalysisEvent(**base)


def test_dominant_factor_uses_weighted_contribution() -> None:
    """Pas un argmax brut : avec la saturation, momentum et volume valent tous
    deux 1.0 et l'argmax trancherait par ordre de dict, pas par sens."""
    ev = jb.build_entry(_analysis(), escalated=False)
    # momentum 0.8*0.35 = 0.280 ; sentiment 0.4*0.25 = 0.100 ; volume 0.2*0.25 = 0.050
    assert ev.dominant_factor == "momentum"
    assert abs(ev.dominant_factor_share - 0.280) < 1e-6


def test_near_tie_is_reported_as_mixed() -> None:
    """Quand deux contributions sont à moins de 0.02, « dominant » n'a pas de
    sens — un gagnant inventé devient une cohorte inventée."""
    a = _analysis(
        meta={
            "factors": {
                "momentum": 0.30,
                "volume": 0.42,
                "sentiment": 0.0,
                "liquidity": 0.0,
            },
            "features": {},
        }
    )
    # momentum 0.105 ; volume 0.105 -> écart nul
    ev = jb.build_entry(a, escalated=False)
    assert ev.dominant_factor == "mixed"


def test_saturated_factors_do_not_produce_an_arbitrary_winner() -> None:
    """Le cas DEXE réel : momentum et volume tous deux saturés à 1.0."""
    a = _analysis(
        meta={
            "factors": {
                "momentum": 1.0,
                "volume": 1.0,
                "sentiment": 0.35,
                "liquidity": 0.5,
            },
            "features": {},
        }
    )
    ev = jb.build_entry(a, escalated=True)
    # momentum 0.35 vs volume 0.25 : écart 0.10 > 0.02, momentum gagne légitimement
    assert ev.dominant_factor == "momentum"


def test_correlation_id_is_carried_for_downstream_joins() -> None:
    """Le journal se rattache au risque et à l'exécution par ces identifiants ;
    les perdre rendrait la ligne orpheline."""
    a = _analysis()
    ev = jb.build_entry(a, escalated=True)
    assert ev.correlation_id == a.correlation_id
    assert ev.signal_event_id == a.event_id


def test_skip_reason_recorded_when_the_call_was_suppressed() -> None:
    ev = jb.build_entry(_analysis(), escalated=True, skip_reason="cooldown")
    assert ev.sonnet_called is False
    assert ev.skip_reason == "cooldown"


def test_market_cap_rank_is_lifted_from_features() -> None:
    """Axe de cohorte, et clé du MAX_AGE différencié de la déduplication."""
    ev = jb.build_entry(_analysis(), escalated=False)
    assert ev.market_cap_rank == 150


def test_missing_factors_do_not_raise() -> None:
    """Une analyse sans meta.factors doit produire une ligne, pas une exception :
    perdre une ligne de journal est acceptable, casser le worker ne l'est pas."""
    ev = jb.build_entry(_analysis(meta={}), escalated=False)
    assert ev.dominant_factor is None
    assert ev.factors == {}


def test_validated_verdict_is_carried() -> None:
    ev = jb.build_entry(
        _analysis(),
        escalated=True,
        sonnet_called=True,
        sonnet_validated=True,
        sonnet_score=61,
        sonnet_confidence=0.52,
        sonnet_direction="long",
    )
    assert ev.sonnet_validated is True
    assert ev.sonnet_score == 61
    assert ev.sonnet_direction == "long"


def test_score_and_confidence_come_from_the_analysis() -> None:
    """La ligne doit refléter l'analyse telle qu'elle était, pas une valeur
    recalculée : c'est l'état au moment de la décision qu'on journalise."""
    ev = jb.build_entry(
        _analysis(opportunity_score=77, confidence=0.61), escalated=True
    )
    assert ev.score == 77
    assert abs(ev.confidence - 0.61) < 1e-9
