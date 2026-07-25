"""Read-side derivation over content_sentiment_agg hourly buckets.

Pure helpers (window_delta, aggregate_buckets) hold the window/decay math and
are unit-tested without a DB. SqlSentimentAggReader runs the range queries and
the read-time distinct-author count; it is integration-tested against Postgres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ContentSentimentAgg, RawContent

WINDOWS: tuple[str, ...] = ("1h", "24h", "7d", "30d", "6mo", "1y", "5y")

_WINDOW_DELTAS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "6mo": timedelta(days=182),
    "1y": timedelta(days=365),
    "5y": timedelta(days=1825),
}

# Distinct-author read is a bounded scan on raw_content; refuse long windows.
_AUTHORS_MAX = timedelta(days=30)


def window_delta(window: str) -> timedelta:
    try:
        return _WINDOW_DELTAS[window]
    except KeyError as exc:
        raise ValueError(f"unknown window {window!r}") from exc


@dataclass(slots=True)
class BucketRow:
    bucket_start: datetime
    mentions: int
    score_sum: float
    confidence_sum: float
    weighted_score_sum: float
    engagement_sum: float


def _decay(bucket_start: datetime, now: datetime, half_life_h: float) -> float:
    age_h = max(0.0, (now - bucket_start).total_seconds() / 3600.0)
    return math.exp(-age_h / half_life_h)


def aggregate_buckets(
    rows: list[BucketRow], *, now: datetime, half_life_h: float | None
) -> dict[str, float]:
    """Collapse buckets into {mentions, avg, weighted_avg, engagement}.

    half_life_h None -> plain sums. Otherwise each bucket is weighted by
    exp(-age_h / half_life_h) for the sentiment means (counts stay integer).
    """
    mentions = sum(r.mentions for r in rows)
    engagement = sum(r.engagement_sum for r in rows)
    if not rows:
        return {"mentions": 0, "avg": 0.0, "weighted_avg": 0.0, "engagement": 0.0}

    if half_life_h is None:
        score_sum = sum(r.score_sum for r in rows)
        conf_sum = sum(r.confidence_sum for r in rows)
        wscore_sum = sum(r.weighted_score_sum for r in rows)
    else:
        score_sum = conf_sum = wscore_sum = 0.0
        for r in rows:
            d = _decay(r.bucket_start, now, half_life_h)
            score_sum += r.score_sum * d
            conf_sum += r.confidence_sum * d
            wscore_sum += r.weighted_score_sum * d

    avg = score_sum / mentions if mentions else 0.0
    weighted_avg = wscore_sum / conf_sum if conf_sum else 0.0
    # Means are returned UNROUNDED (full precision); round at the API edge.
    return {
        "mentions": mentions,
        "avg": avg,
        "weighted_avg": weighted_avg,
        "engagement": engagement,
    }


class SqlSentimentAggReader:
    """AsyncSession-backed reader over content_sentiment_agg / raw_content."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _fetch_buckets(
        self, *, symbol: str | None, kind: str | None, since: datetime
    ) -> list[BucketRow]:
        m = ContentSentimentAgg
        stmt = select(
            m.bucket_start, m.mentions, m.score_sum, m.confidence_sum,
            m.weighted_score_sum, m.engagement_sum,
        ).where(m.bucket_start >= since)
        if symbol is not None:
            stmt = stmt.where(m.symbol == symbol)
        if kind is not None and kind != "all":
            stmt = stmt.where(m.kind == kind)
        rows = (await self._session.execute(stmt)).all()
        return [
            BucketRow(bucket_start=r[0], mentions=r[1], score_sum=r[2],
                      confidence_sum=r[3], weighted_score_sum=r[4], engagement_sum=r[5])
            for r in rows
        ]

    async def window_stats(
        self,
        *,
        symbol: str | None,
        kind: str | None,
        window: str,
        half_life_h: float | None = None,
        now: datetime | None = None,
    ) -> dict[str, float]:
        now = now or datetime.now(tz=UTC)
        since = now - window_delta(window)
        rows = await self._fetch_buckets(symbol=symbol, kind=kind, since=since)
        out = aggregate_buckets(rows, now=now, half_life_h=half_life_h)
        out["window"] = window
        return out

    async def all_windows(
        self, *, symbol: str | None, kind: str | None,
        half_life_h: float | None = None, now: datetime | None = None,
    ) -> list[dict[str, float]]:
        now = now or datetime.now(tz=UTC)
        return [
            await self.window_stats(symbol=symbol, kind=kind, window=w,
                                    half_life_h=half_life_h, now=now)
            for w in WINDOWS
        ]

    async def series(
        self, *, symbol: str | None, kind: str | None, points: int,
        now: datetime | None = None,
    ) -> list[dict[str, float]]:
        """Last `points` hourly buckets, oldest first, as {hour, sentiment}.

        `since` is floored to the hour so at most `points` buckets are returned
        (an unfloored cutoff would span points+1 partial hours).
        """
        now = now or datetime.now(tz=UTC)
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        since = current_hour - timedelta(hours=points - 1)
        rows = await self._fetch_buckets(symbol=symbol, kind=kind, since=since)
        by_hour: dict[datetime, list[BucketRow]] = {}
        for r in rows:
            by_hour.setdefault(r.bucket_start, []).append(r)
        out = []
        for hour in sorted(by_hour):
            agg = aggregate_buckets(by_hour[hour], now=now, half_life_h=None)
            out.append({"hour": hour.isoformat(), "sentiment": agg["avg"],
                        "mentions": agg["mentions"]})
        return out

    async def distinct_authors(
        self, *, symbol: str, window: str, now: datetime | None = None
    ) -> int:
        delta = window_delta(window)
        if delta > _AUTHORS_MAX:
            raise ValueError("distinct authors only for windows <= 30d")
        now = now or datetime.now(tz=UTC)
        since = now - delta
        stmt = (
            select(func.count(distinct(RawContent.author)))
            .where(RawContent.author.is_not(None))
            .where(RawContent.published_at >= since)
            .where(RawContent.symbols.contains([symbol]))  # JSONB @>
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)
