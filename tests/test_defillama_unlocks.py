"""Extracting the next unlock from a DefiLlama emissions document.

Fixture shape verified against the live response for `aave` on 2026-07-31:
metadata.events[] carries {timestamp, noOfTokens[], unlockType} and reaches
back to 2017, while supplyMetrics.maxSupply is the denominator.
"""

from __future__ import annotations

from datetime import UTC, datetime

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


def test_missing_max_supply_yields_no_reading_rather_than_a_zero() -> None:
    # No denominator means the percentage is unknown, and unknown must not be
    # served as a confident 0.0.
    due = datetime(2026, 8, 10, tzinfo=UTC)
    doc = _doc([_event(due, 25_000)], max_supply=0.0)
    assert unlocks.next_unlock(doc, now=NOW) is None


def test_document_without_events_yields_no_unlock() -> None:
    assert unlocks.next_unlock(_doc([]), now=NOW) is None
