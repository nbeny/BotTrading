"""DeveloperEvent keeps absent GitHub measurements as None, not 0.

An event missing from the `AnyEvent` discriminated union publishes perfectly
and fails on *consumption* -- this is exactly what happened to
JournalEntryEvent. The round-trip through `parse_event` is therefore the test
that counts, not plain construction (see `tests/test_account_snapshot_topic.py`
and `tests/test_derivatives_fundamentals_events.py`, which this file follows).
"""

from __future__ import annotations

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


def test_round_trip_preserves_none():
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
    assert back.commit_ratio_4w == 1.5
    assert back.pr_ratio_4w is None
    assert back.days_since_push == 3


def test_topic_is_registered():
    assert Topic.DEVELOPER == "market.developer.events"
    assert TOPIC_EVENT[Topic.DEVELOPER] is DeveloperEvent


def test_ratios_reject_negatives():
    """Un ratio est un rapport de comptages: negatif = bug amont, pas une mesure."""
    with pytest.raises(ValidationError):
        DeveloperEvent(
            source=Source.GITHUB,
            symbol="AAVE",
            coin_id="aave",
            repo_count=1,
            commit_ratio_4w=-0.5,
        )


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
            repo_count=0,
        ).as_kafka_value()
    )
    assert all_dead.all_repos_archived != never_looked.all_repos_archived
    assert all_dead.commit_ratio_4w is None and never_looked.commit_ratio_4w is None
