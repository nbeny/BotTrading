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


async def test_fake_upsert_aggregate_creates_bucket() -> None:
    repo = FakeContentRepository()
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    await repo.upsert_aggregate(
        symbol="BTC", kind="social", bucket_start=ts,
        mentions=1, score_sum=0.5, confidence_sum=0.8,
        weighted_score_sum=0.4, engagement_sum=2.0,
    )
    agg = repo.aggregates[("BTC", "social", ts)]
    assert agg["mentions"] == 1
    assert agg["score_sum"] == 0.5
    assert agg["engagement_sum"] == 2.0


async def test_fake_upsert_aggregate_is_additive() -> None:
    repo = FakeContentRepository()
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    kw = dict(symbol="BTC", kind="social", bucket_start=ts)
    await repo.upsert_aggregate(
        **kw, mentions=1, score_sum=0.5, confidence_sum=0.8,
        weighted_score_sum=0.4, engagement_sum=2.0,
    )
    await repo.upsert_aggregate(
        **kw, mentions=2, score_sum=1.8, confidence_sum=1.5,
        weighted_score_sum=1.6, engagement_sum=5.0,
    )
    agg = repo.aggregates[("BTC", "social", ts)]
    assert agg["mentions"] == 3          # 1 + 2
    assert agg["score_sum"] == 2.3       # 0.5 + 1.8
    assert agg["confidence_sum"] == 2.3  # 0.8 + 1.5
    assert agg["weighted_score_sum"] == 2.0  # 0.4 + 1.6
    assert agg["engagement_sum"] == 7.0  # 2.0 + 5.0
