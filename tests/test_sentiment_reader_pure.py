"""Pure window/decay math for the sentiment reader (no DB)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cmi_common.sources.reader import (
    WINDOWS,
    BucketRow,
    aggregate_buckets,
    window_delta,
)


def test_window_delta_known_windows() -> None:
    assert window_delta("24h") == timedelta(hours=24)
    assert window_delta("7d") == timedelta(days=7)
    assert window_delta("6mo") == timedelta(days=182)
    assert window_delta("5y") == timedelta(days=1825)


def test_windows_constant_matches_approved_set() -> None:
    assert WINDOWS == ("1h", "24h", "7d", "30d", "6mo", "1y", "5y")


def test_aggregate_no_decay_is_plain_mean() -> None:
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    rows = [
        BucketRow(now - timedelta(hours=1), mentions=2, score_sum=1.0,
                  confidence_sum=1.6, weighted_score_sum=0.8, engagement_sum=4.0),
        BucketRow(now - timedelta(hours=2), mentions=1, score_sum=-0.5,
                  confidence_sum=0.5, weighted_score_sum=-0.25, engagement_sum=1.0),
    ]
    out = aggregate_buckets(rows, now=now, half_life_h=None)
    assert out["mentions"] == 3
    assert out["avg"] == pytest.approx((1.0 - 0.5) / 3)
    assert out["weighted_avg"] == pytest.approx((0.8 - 0.25) / (1.6 + 0.5))
    assert out["engagement"] == pytest.approx(5.0)


def test_aggregate_empty_is_zeros() -> None:
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    out = aggregate_buckets([], now=now, half_life_h=None)
    assert out == {"mentions": 0, "avg": 0.0, "weighted_avg": 0.0, "engagement": 0.0}


def test_aggregate_decay_weights_recent_more() -> None:
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    recent = BucketRow(now - timedelta(hours=1), mentions=1, score_sum=1.0,
                       confidence_sum=1.0, weighted_score_sum=1.0, engagement_sum=0.0)
    old = BucketRow(now - timedelta(hours=100), mentions=1, score_sum=-1.0,
                    confidence_sum=1.0, weighted_score_sum=-1.0, engagement_sum=0.0)
    out = aggregate_buckets([recent, old], now=now, half_life_h=1.0)
    # 1h half-life crushes the 100h-old bearish bucket -> weighted_avg strongly positive
    assert out["weighted_avg"] > 0.9


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Minimal AsyncSession double: execute() ignores the stmt, returns rows."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _FakeResult(self._rows)


async def test_series_returns_exactly_points_gap_filled() -> None:
    from cmi_common.sources import SqlSentimentAggReader

    now = datetime(2024, 1, 2, 10, 30, tzinfo=timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    two_ago = current_hour - timedelta(hours=2)
    # _fetch_buckets projects (bucket_start, mentions, score_sum, confidence_sum,
    # weighted_score_sum, engagement_sum).
    rows = [(two_ago, 2, 1.0, 1.6, 0.8, 4.0)]
    reader = SqlSentimentAggReader(_FakeSession(rows))

    out = await reader.series(symbol="BTC", kind="all", points=12, now=now)

    assert len(out) == 12                                    # fixed, gap-free
    assert out[0]["hour"] == (current_hour - timedelta(hours=11)).isoformat()
    assert out[-1]["hour"] == current_hour.isoformat()       # oldest→newest
    assert out[9]["mentions"] == 2 and out[9]["sentiment"] == pytest.approx(0.5)
    assert out[0]["mentions"] == 0 and out[0]["sentiment"] == 0.0  # zero-filled


class _QueueSession:
    """AsyncSession double returning a different result per execute() call."""

    def __init__(self, *result_rows):
        self._q = list(result_rows)

    async def execute(self, _stmt):
        return _FakeResult(self._q.pop(0))


async def test_window_stats_unions_hourly_and_daily() -> None:
    from cmi_common.sources import SqlSentimentAggReader

    now = datetime(2024, 6, 1, tzinfo=timezone.utc)
    hourly = [(now - timedelta(hours=1), 1, 1.0, 1.0, 1.0, 0.0)]     # recent
    daily = [(now - timedelta(days=200), 1, -1.0, 1.0, -1.0, 0.0)]   # aged-out
    # window_stats fetches hourly first, then daily.
    reader = SqlSentimentAggReader(_QueueSession(hourly, daily))

    out = await reader.window_stats(symbol="BTC", kind="all", window="5y", now=now)

    assert out["mentions"] == 2                       # both tables unioned
    assert out["avg"] == pytest.approx(0.0)           # (1.0 + -1.0) / 2
    assert out["window"] == "5y"
