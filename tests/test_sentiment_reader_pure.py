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
