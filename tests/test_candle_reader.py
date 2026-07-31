"""Pure-function tests for the candle reader.

The SQL methods need Postgres and are covered by scripts/verify_read_live.py;
the closedness and interval maths are pure and tested here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cmi_common.sources.candles import interval_delta, is_closed

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def test_interval_delta_known_intervals():
    assert interval_delta("1h") == timedelta(hours=1)
    assert interval_delta("15m") == timedelta(minutes=15)


def test_interval_delta_rejects_unknown():
    with pytest.raises(ValueError, match="unknown interval"):
        interval_delta("4h")


def test_is_closed_true_for_a_fully_elapsed_bucket():
    assert is_closed(datetime(2026, 7, 29, 11, 0, tzinfo=UTC), "1h", NOW) is True


def test_is_closed_false_for_the_forming_bucket():
    """The 12:00 candle closes at 13:00; at 12:00 it holds one tick."""
    assert is_closed(NOW, "1h", NOW) is False


def test_is_closed_is_exact_at_the_boundary():
    """A bucket ending exactly at `now` is closed: its last second has elapsed."""
    assert is_closed(datetime(2026, 7, 29, 11, 45, tzinfo=UTC), "15m", NOW) is True
