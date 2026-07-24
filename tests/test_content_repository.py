"""raw_item_to_row mapping + FakeContentRepository dedup/queue/aggregate."""

from __future__ import annotations

from datetime import datetime, timezone

from cmi_common.sources import FakeContentRepository, RawItem
from cmi_common.sources.repository import raw_item_to_row


def _item(**kw) -> RawItem:
    base = dict(source="bluesky", kind="social", external_id="1", text="$BTC")
    base.update(kw)
    return RawItem(**base)


def test_raw_item_to_row_maps_fields() -> None:
    row = raw_item_to_row(_item(symbols=["BTC"], engagement=3.0))
    assert row["source"] == "bluesky"
    assert row["external_id"] == "1"
    assert row["symbols"] == ["BTC"]
    assert row["engagement"] == 3.0
    assert row["scored_at"] is None


async def test_fake_insert_dedups_on_source_external_id() -> None:
    repo = FakeContentRepository()
    n1 = await repo.insert_items([_item(external_id="a"), _item(external_id="b")])
    n2 = await repo.insert_items([_item(external_id="a")])  # duplicate
    assert n1 == 2
    assert n2 == 0
    assert len(repo.rows) == 2


async def test_fake_fetch_unscored_and_mark() -> None:
    repo = FakeContentRepository()
    await repo.insert_items([_item(external_id="a"), _item(external_id="b")])
    unscored = await repo.fetch_unscored(limit=10)
    assert len(unscored) == 2
    await repo.mark_scored(unscored[0].id, score=0.5, confidence=0.8, model="m")
    assert len(await repo.fetch_unscored(limit=10)) == 1


async def test_fake_upsert_aggregate_accumulates() -> None:
    repo = FakeContentRepository()
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    await repo.upsert_aggregate(
        symbol="BTC", kind="social", window_start=ts, window_size=3600,
        mentions=1, unique_authors=1, engagement_sum=2.0, avg_sentiment=0.5,
        weighted_sentiment=0.4,
    )
    key = ("BTC", "social", ts, 3600)
    assert repo.aggregates[key]["mentions"] == 1


async def test_fake_upsert_aggregate_conflict_matches_sql_semantics() -> None:
    # Second upsert into the same window: counts accumulate; sentiment values
    # become the mentions-weighted running mean (mirrors ON CONFLICT DO UPDATE).
    import pytest

    repo = FakeContentRepository()
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    kw = dict(symbol="BTC", kind="social", window_start=ts, window_size=3600)
    await repo.upsert_aggregate(
        **kw, mentions=1, unique_authors=1, engagement_sum=2.0,
        avg_sentiment=0.5, weighted_sentiment=0.4,
    )
    await repo.upsert_aggregate(
        **kw, mentions=2, unique_authors=3, engagement_sum=5.0,
        avg_sentiment=0.9, weighted_sentiment=0.8,
    )
    agg = repo.aggregates[("BTC", "social", ts, 3600)]
    assert agg["mentions"] == 3            # accumulated
    assert agg["engagement_sum"] == 7.0    # accumulated
    assert agg["unique_authors"] == 4      # accumulated (1 + 3)
    # weighted running mean: (0.5*1 + 0.9*2) / 3 = 0.7667
    assert agg["avg_sentiment"] == pytest.approx((0.5 * 1 + 0.9 * 2) / 3)
    # (0.4*1 + 0.8*2) / 3 = 0.6667
    assert agg["weighted_sentiment"] == pytest.approx((0.4 * 1 + 0.8 * 2) / 3)
