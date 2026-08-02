"""Extracting the next unlock from a DefiLlama emissions document.

Fixture shape verified against the live response for `aave` on 2026-07-31:
metadata.events[] carries {timestamp, noOfTokens[], unlockType} and reaches
back to 2017, while supplyMetrics.maxSupply is the denominator.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from service_modules import load_service_module

unlocks = load_service_module("collector-defillama", "domain.unlocks")

NOW = datetime(2026, 7, 31, tzinfo=UTC)


def _doc(events: list[dict], max_supply: float = 1_000_000.0) -> dict:
    return {
        "gecko_id": "aave",
        "name": "Aave",
        "metadata": {"events": events},
        "supplyMetrics": {"maxSupply": max_supply},
        "documentedData": {"ignored": "megabytes of chart series"},
    }


def _event(when: datetime, tokens: float) -> dict:
    return {
        "timestamp": int(when.timestamp()),
        "noOfTokens": [tokens],
        "unlockType": "cliff",
        "category": "Uncategorized",
        "description": "A cliff of {tokens[0]} tokens",
    }


def test_returns_the_next_future_unlock_within_thirty_days() -> None:
    due = datetime(2026, 8, 10, tzinfo=UTC)
    result = unlocks.next_unlock(_doc([_event(due, 25_000)]), now=NOW)
    assert result is not None
    assert result.at == due
    assert result.pct_supply == 2.5  # 25_000 / 1_000_000 -> percentage points


def test_historical_unlocks_are_ignored() -> None:
    # events[] reaches back years; without the future filter the "next" unlock
    # would be one from 2017 and every token would look maximally diluted.
    past = datetime(2017, 12, 9, tzinfo=UTC)
    assert unlocks.next_unlock(_doc([_event(past, 360_000)]), now=NOW) is None


def test_unlocks_beyond_thirty_days_are_ignored() -> None:
    far = datetime(2026, 10, 1, tzinfo=UTC)
    assert unlocks.next_unlock(_doc([_event(far, 50_000)]), now=NOW) is None


def test_several_unlocks_in_the_window_are_summed_and_dated_at_the_earliest() -> None:
    first = datetime(2026, 8, 5, tzinfo=UTC)
    second = datetime(2026, 8, 20, tzinfo=UTC)
    result = unlocks.next_unlock(
        _doc([_event(second, 30_000), _event(first, 20_000)]), now=NOW
    )
    assert result is not None
    assert result.at == first
    assert result.pct_supply == 5.0


def test_multiple_token_amounts_in_one_event_are_summed() -> None:
    due = datetime(2026, 8, 10, tzinfo=UTC)
    doc = _doc([{"timestamp": int(due.timestamp()), "noOfTokens": [10_000, 5_000]}])
    result = unlocks.next_unlock(doc, now=NOW)
    assert result is not None
    assert result.pct_supply == 1.5


def test_an_unsizable_unlock_raises_rather_than_reading_as_clean() -> None:
    # Returning None here would be read one layer up as "we looked, nothing is
    # coming" -- has_unlock_schedule=True, fundamentals axis 1.0 -- so a token
    # about to dilute would score as perfectly healthy. Raising leaves the key
    # absent and the axis honestly unknown.
    due = datetime(2026, 8, 10, tzinfo=UTC)
    with pytest.raises(ValueError):
        unlocks.next_unlock(_doc([_event(due, 25_000)], max_supply=0.0), now=NOW)


def test_a_missing_denominator_is_irrelevant_when_nothing_is_scheduled() -> None:
    # No unlock in the window is the good news regardless of the denominator,
    # so this must stay a clean None rather than raising.
    assert unlocks.next_unlock(_doc([], max_supply=0.0), now=NOW) is None


def test_document_without_events_yields_no_unlock() -> None:
    assert unlocks.next_unlock(_doc([]), now=NOW) is None


def test_a_malformed_timestamp_raises_rather_than_being_skipped() -> None:
    # Skipping the bad event would report the remaining ones as the whole
    # picture. Raising lets the caller drop the token to "unknown".
    doc = _doc([{"timestamp": "not-a-number", "noOfTokens": [1_000]}])
    with pytest.raises(ValueError):
        unlocks.next_unlock(doc, now=NOW)


def test_a_zero_size_marker_does_not_date_a_later_real_unlock() -> None:
    marker = datetime(2026, 8, 10, tzinfo=UTC)
    real = datetime(2026, 8, 25, tzinfo=UTC)
    result = unlocks.next_unlock(
        _doc([_event(marker, 0), _event(real, 20_000)]), now=NOW
    )
    assert result is not None
    assert result.at == real
    assert result.pct_supply == 2.0


def test_an_event_exactly_at_the_horizon_is_included() -> None:
    result = unlocks.next_unlock(
        _doc([_event(NOW + timedelta(days=30), 10_000)]), now=NOW
    )
    assert result is not None


def test_an_event_exactly_now_is_not_in_the_future() -> None:
    assert unlocks.next_unlock(_doc([_event(NOW, 10_000)]), now=NOW) is None
