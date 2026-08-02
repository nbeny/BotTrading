"""DeveloperEvent keeps absent GitHub measurements as None, not 0.

An event missing from the `AnyEvent` discriminated union publishes perfectly
and fails on *consumption* -- this is exactly what happened to
JournalEntryEvent. The round-trip through `parse_event` is therefore the test
that counts, not plain construction (see `tests/test_account_snapshot_topic.py`
and `tests/test_derivatives_fundamentals_events.py`, which this file follows).
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from cmi_common.events import DeveloperEvent, EventType, Source, parse_event
from cmi_common.kafka import TOPIC_EVENT, Topic


def test_measures_default_to_none_not_zero():
    """Un champ absent doit rester None: 0.0 affirmerait une mesure."""
    e = DeveloperEvent(
        source=Source.GITHUB, symbol="AAVE", coin_id="aave", repo_count=3
    )
    assert e.commit_ratio_4w is None
    assert e.pr_ratio_4w is None
    assert e.days_since_push is None
    assert e.star_growth_pct_7d is None
    assert e.all_repos_archived is False
    assert e.event_type == EventType.DEVELOPER


def test_round_trip_through_the_discriminated_union():
    """`model_validate_json` alone would still pass if DeveloperEvent were
    dropped from `AnyEvent` -- it bypasses the discriminator entirely. Going
    through `parse_event` is what would have caught the JournalEntryEvent
    failure mode: missing from the union, publishes fine, fails on
    consumption."""
    e = DeveloperEvent(
        source=Source.GITHUB,
        symbol="AAVE",
        coin_id="aave",
        repo_count=2,
        commit_ratio_4w=1.5,
        days_since_push=3,
    )
    back = parse_event(e.as_kafka_value())
    assert isinstance(back, DeveloperEvent)
    assert back.repo_count == 2
    assert back.commit_ratio_4w == 1.5
    assert back.pr_ratio_4w is None
    assert back.days_since_push == 3


def test_topic_is_registered():
    assert Topic.DEVELOPER == "market.developer.events"
    assert TOPIC_EVENT[Topic.DEVELOPER] is DeveloperEvent


@pytest.mark.parametrize("field", ["commit_ratio_4w", "pr_ratio_4w", "days_since_push"])
def test_ratios_reject_negatives(field):
    """Ces trois champs sont des rapports ou des décomptes de comptages:
    negatif = bug amont, pas une mesure."""
    with pytest.raises(ValidationError):
        DeveloperEvent(
            source=Source.GITHUB,
            symbol="AAVE",
            coin_id="aave",
            repo_count=1,
            **{field: -1},
        )


def test_star_growth_accepts_negatives():
    """Contrairement aux trois champs ci-dessus, star_growth_pct_7d est un
    flux (un dépôt peut perdre des étoiles), pas un décompte -- il doit rester
    non borné en bas. C'est l'incident fees_24h_usd sous un autre nom: un
    ``ge=0`` ajouté ici romprait la publication du token entier au premier
    repo en perte d'étoiles."""
    e = DeveloperEvent(
        source=Source.GITHUB,
        symbol="AAVE",
        coin_id="aave",
        repo_count=1,
        star_growth_pct_7d=-0.05,
    )
    assert e.star_growth_pct_7d == -0.05


def test_events_partition_by_symbol():
    """Sans cet override, `BaseEvent.partition_key()` retombe sur un UUID par
    événement: les mises à jour d'un même token se disperseraient sur toutes
    les partitions et perdraient leur ordre relatif."""
    assert (
        DeveloperEvent(
            source=Source.GITHUB, symbol="ETH", coin_id="ethereum", repo_count=1
        ).partition_key()
        == "ETH"
    )


def test_zero_repos_requires_all_archived():
    """repo_count=0 n'est légal qu'accompagné de all_repos_archived=True (« on
    a regardé, tout est mort »). Sans ce garde-fou à la construction, un
    collector pourrait publier repo_count=0 sans avoir rien regardé, et
    l'événement deviendrait infalsifiable une fois dans Redis."""
    with pytest.raises(ValidationError):
        DeveloperEvent(
            source=Source.GITHUB,
            symbol="AAVE",
            coin_id="aave",
            repo_count=0,
            all_repos_archived=False,
        )


def test_wire_payload_rejects_zero_repos_without_all_archived():
    """The constructor-only version of this check proves our own code is
    careful; this proves something more valuable, per this file's header
    docstring: a malformed message from a *third-party* producer on
    ``market.developer.events`` -- one that never went through
    ``DeveloperEvent(...)`` at all -- is rejected on decode too."""
    payload = json.dumps(
        {
            "event_type": "DeveloperEvent",
            "source": "github",
            "symbol": "AAVE",
            "coin_id": "aave",
            "repo_count": 0,
            "all_repos_archived": False,
        }
    ).encode("utf-8")
    with pytest.raises(ValidationError):
        parse_event(payload)


def test_a_measured_zero_and_an_absent_field_stay_distinct():
    """`all_repos_archived=True` avec `repo_count=0` dit « on a regardé, tout
    est mort » ; un champ de mesure resté `None` dit « on n'a jamais regardé ».
    C'est la classe de défaut signature de ce dépôt (14+ instances trouvées,
    aucune n'ayant fait échouer un test) -- on la pin ici après un aller-retour
    Kafka complet, pas seulement à la construction."""
    all_dead = parse_event(
        DeveloperEvent(
            source=Source.GITHUB,
            symbol="AAVE",
            coin_id="aave",
            repo_count=0,
            all_repos_archived=True,
        ).as_kafka_value()
    )
    never_looked = parse_event(
        DeveloperEvent(
            source=Source.GITHUB,
            symbol="XYZ",
            coin_id="xyz",
            repo_count=2,
        ).as_kafka_value()
    )
    assert all_dead.all_repos_archived is True
    assert never_looked.all_repos_archived is False
    assert all_dead.commit_ratio_4w is None
    assert never_looked.commit_ratio_4w is None
