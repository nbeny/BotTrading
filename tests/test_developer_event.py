"""DeveloperEvent keeps absent GitHub measurements as None, not 0."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cmi_common.events import DeveloperEvent, EventType, Source
from cmi_common.kafka import TOPIC_EVENT, Topic


def test_measures_default_to_none_not_zero():
    """Un champ absent doit rester None: 0.0 affirmerait une mesure."""
    e = DeveloperEvent(source=Source.GITHUB, symbol="AAVE", coin_id="aave", repo_count=3)
    assert e.commit_ratio_4w is None
    assert e.pr_ratio_4w is None
    assert e.days_since_push is None
    assert e.star_growth_pct_7d is None
    assert e.all_repos_archived is False
    assert e.event_type == EventType.DEVELOPER


def test_round_trip_preserves_none():
    e = DeveloperEvent(
        source=Source.GITHUB, symbol="AAVE", coin_id="aave",
        repo_count=2, commit_ratio_4w=1.5, days_since_push=3,
    )
    back = DeveloperEvent.model_validate_json(e.model_dump_json())
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
            source=Source.GITHUB, symbol="AAVE", coin_id="aave",
            repo_count=1, commit_ratio_4w=-0.5,
        )
