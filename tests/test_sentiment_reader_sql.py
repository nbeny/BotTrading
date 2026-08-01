"""Integration: SqlSentimentAggReader over seeded content_sentiment_agg."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("CMI_TEST_DB_URL"),
    reason="set CMI_TEST_DB_URL to run reader integration tests",
)


@pytest.fixture()
async def session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from cmi_common.db.models import Base

    engine = create_async_engine(os.environ["CMI_TEST_DB_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed(session, symbol, bucket_start, *, score_sum, conf_sum, wsum, n):
    from cmi_common.db.models import ContentSentimentAgg

    session.add(
        ContentSentimentAgg(
            symbol=symbol,
            kind="social",
            bucket_start=bucket_start,
            mentions=n,
            score_sum=score_sum,
            confidence_sum=conf_sum,
            weighted_score_sum=wsum,
            engagement_sum=0.0,
        )
    )
    await session.commit()


async def test_window_derivation_24h_vs_7d(session) -> None:
    from cmi_common.sources import SqlSentimentAggReader

    now = datetime(2024, 1, 10, tzinfo=timezone.utc)
    await _seed(
        session,
        "BTC",
        now - timedelta(hours=2),
        score_sum=1.0,
        conf_sum=1.0,
        wsum=1.0,
        n=1,
    )  # in 24h
    await _seed(
        session,
        "BTC",
        now - timedelta(days=3),
        score_sum=-1.0,
        conf_sum=1.0,
        wsum=-1.0,
        n=1,
    )  # only in 7d

    reader = SqlSentimentAggReader(session)
    d1 = await reader.window_stats(symbol="BTC", kind="all", window="24h", now=now)
    d7 = await reader.window_stats(symbol="BTC", kind="all", window="7d", now=now)

    assert d1["mentions"] == 1 and d1["avg"] == pytest.approx(1.0)
    assert d7["mentions"] == 2 and d7["avg"] == pytest.approx(0.0)


async def test_compaction_and_union_read(session) -> None:
    from cmi_common.sources import SqlContentRepository, SqlSentimentAggReader

    now = datetime(2024, 6, 1, tzinfo=timezone.utc)
    # two old hourly buckets same UTC day (must roll into one daily bucket)
    old_day = datetime(2024, 1, 1, tzinfo=timezone.utc)
    await _seed(
        session,
        "BTC",
        old_day + timedelta(hours=3),
        score_sum=0.5,
        conf_sum=0.5,
        wsum=0.25,
        n=1,
    )
    await _seed(
        session,
        "BTC",
        old_day + timedelta(hours=9),
        score_sum=0.5,
        conf_sum=0.5,
        wsum=0.25,
        n=1,
    )
    # a recent hourly bucket that must survive compaction
    await _seed(
        session,
        "BTC",
        now - timedelta(hours=1),
        score_sum=-1.0,
        conf_sum=1.0,
        wsum=-1.0,
        n=1,
    )

    cutoff = datetime(2024, 5, 1, tzinfo=timezone.utc)
    n = await SqlContentRepository(session).compact_hourly_to_daily(older_than=cutoff)
    assert n == 2  # two old hourly rows compacted

    # daily table now holds the summed old-day bucket at UTC midnight
    from sqlalchemy import select
    from cmi_common.db.models import ContentSentimentAgg, ContentSentimentAggDaily

    daily = (await session.execute(select(ContentSentimentAggDaily))).scalars().all()
    assert len(daily) == 1
    assert daily[0].bucket_start == old_day and daily[0].mentions == 2
    # only the recent hourly bucket remains
    hourly = (await session.execute(select(ContentSentimentAgg))).scalars().all()
    assert len(hourly) == 1

    # 5y window unions the daily (old) + hourly (recent) buckets
    reader = SqlSentimentAggReader(session)
    d5y = await reader.window_stats(symbol="BTC", kind="all", window="5y", now=now)
    assert d5y["mentions"] == 3  # 2 (daily) + 1 (hourly)
    assert d5y["avg"] == pytest.approx((1.0 - 1.0) / 3)
