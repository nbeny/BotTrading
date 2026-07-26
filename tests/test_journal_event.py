"""JournalEntryEvent — l'enregistrement d'audit d'une décision d'appel IA.

Chaque analyse produit une ligne, escaladée ou non. Les champs de la moitié B
(discriminants de déduplication) sont déclarés dès maintenant mais restent nuls
jusqu'au chantier de déduplication.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cmi_common.events.journal import JournalEntryEvent


def _minimal(**kw):
    base = dict(
        symbol="BTC",
        signal_event_id="sig-1",
        factors={"momentum": 0.5, "volume": 0.0, "sentiment": 0.3, "liquidity": 0.5},
        features={"price_change_pct_24h": 7.5},
        score=42,
        confidence=0.7,
        factors_present=2,
        escalated=False,
        sonnet_called=False,
    )
    base.update(kw)
    return JournalEntryEvent(**base)


def test_non_escalated_analysis_is_journalled() -> None:
    """Le groupe témoin de Q2 : sans les non escaladées, le gate d'opportunité
    reste invérifiable pour toujours."""
    ev = _minimal()
    assert ev.escalated is False
    assert ev.sonnet_called is False
    assert ev.sonnet_validated is None


def test_dedup_fields_default_to_null() -> None:
    """Moitié B : déclarée, pas encore alimentée."""
    ev = _minimal()
    assert ev.dedup_trigger is None
    assert ev.drift_momentum is None
    assert ev.cooldown_verdict is None


def test_sonnet_verdict_round_trips_through_json() -> None:
    ev = _minimal(
        escalated=True, sonnet_called=True, sonnet_validated=True,
        sonnet_score=61, sonnet_confidence=0.52, sonnet_direction="long",
    )
    restored = JournalEntryEvent.model_validate(ev.model_dump(mode="json"))
    assert restored.sonnet_validated is True
    assert restored.sonnet_score == 61


def test_dominant_factor_is_free_text_including_mixed() -> None:
    """`mixed` est une valeur légitime : quand deux contributions sont à moins de
    0.02 l'une de l'autre, « dominant » n'a pas de sens."""
    ev = _minimal(dominant_factor="mixed", dominant_factor_share=0.26)
    assert ev.dominant_factor == "mixed"


def test_unknown_field_is_rejected() -> None:
    """BaseEvent est en extra='forbid' : une faute de frappe doit exploser à la
    construction, pas produire une colonne silencieusement vide."""
    with pytest.raises(ValidationError):
        _minimal(sonnet_validated_typo=True)


def test_factors_present_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _minimal(factors_present=9)


def test_decision_event_id_is_settable_via_model_copy() -> None:
    """BaseEvent est frozen : le worker attache l'id de décision après coup, et
    model_copy est le seul chemin — une affectation directe lèverait."""
    ev = _minimal()
    linked = ev.model_copy(update={"decision_event_id": "dec-1"})
    assert linked.decision_event_id == "dec-1"
    assert ev.decision_event_id is None


def test_round_trips_through_parse_event() -> None:
    """Le consommateur Kafka (kafka/consumer.py) désérialise via parse_event,
    qui valide contre l'union AnyEvent. Un événement publié mais absent de cette
    union est produit sans erreur et rejeté à la consommation — panne muette du
    côté qui compte.
    """
    from cmi_common.events import parse_event

    ev = _minimal(escalated=True, sonnet_called=True, sonnet_validated=False)
    decoded = parse_event(ev.as_kafka_value())
    assert isinstance(decoded, JournalEntryEvent)
    assert decoded.event_id == ev.event_id
    assert decoded.sonnet_validated is False
