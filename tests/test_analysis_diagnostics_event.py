"""AnalysisEvent carries the triage diagnostics downstream.

The pipeline funnel reports where signals stop. That is only answerable if the
reason travels with the event rather than being recomputed from the score, which
would not distinguish "score too low" from "score fine, gate not met".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cmi_common.events import AnalysisEvent


def test_defaults_are_backwards_compatible() -> None:
    """Producers that omit the new fields must still validate, and an unset
    block_reason must not read as "this reached the senior analyst"."""
    ev = AnalysisEvent(symbol="BTC", opportunity_score=22, confidence=0.6, reason="r")
    assert ev.ambiguous is False
    assert ev.factors_present == 0
    assert ev.block_reason == "unknown"
    assert ev.liquidity_source == "unknown"


def test_diagnostics_round_trip_through_json() -> None:
    """Kafka carries these as JSON; a field that does not survive a round trip
    is worse than absent because the funnel would silently under-report."""
    ev = AnalysisEvent(
        symbol="BTC",
        opportunity_score=22,
        confidence=0.6,
        reason="r",
        ambiguous=True,
        factors_present=2,
        block_reason="score_below_threshold",
        liquidity_source="volume_proxy",
    )
    restored = AnalysisEvent.model_validate(ev.model_dump(mode="json"))
    assert restored.block_reason == "score_below_threshold"
    assert restored.factors_present == 2
    assert restored.liquidity_source == "volume_proxy"
    assert restored.ambiguous is True


@pytest.mark.parametrize("bad", [-1, 5])
def test_factors_present_is_bounded_to_the_four_factors(bad: int) -> None:
    """The scorer combines exactly four factors; a count outside 0-4 means a
    producer bug, and the funnel's coverage histogram would be nonsense."""
    with pytest.raises(ValidationError):
        AnalysisEvent(
            symbol="BTC",
            opportunity_score=22,
            confidence=0.6,
            reason="r",
            factors_present=bad,
        )
