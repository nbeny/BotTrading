"""Persistence for raw ingested content + the sentiment aggregate rollup.

``raw_item_to_row`` is the pure mapping (unit-tested). ``SqlContentRepository``
is the async SQLAlchemy implementation (integration-tested against Postgres).
``FakeContentRepository`` is an in-memory double used by loop/worker unit tests
— it mirrors the same protocol so tests never need a live database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ContentSentimentAgg, ContentSentimentAggDaily, RawContent
from .raw import RawItem


def raw_item_to_row(item: RawItem) -> dict[str, Any]:
    """Map a RawItem to a ``raw_content`` insert dict (unscored)."""
    return {
        "source": item.source,
        "kind": item.kind,
        "external_id": item.external_id,
        "url": item.url,
        "author": item.author,
        "title": item.title,
        "text": item.text,
        "symbols": item.symbols,
        "engagement": item.engagement,
        "lang": item.lang,
        "published_at": item.published_at,
        "scored_at": None,
    }


@dataclass
class UnscoredRow:
    """A minimal projection of an unscored raw_content row for the worker."""

    id: int
    source: str
    kind: str
    title: str | None
    text: str
    symbols: list[str]
    engagement: float | None
    published_at: datetime | None


class ContentRepository(Protocol):
    async def insert_items(self, items: list[RawItem]) -> int: ...

    async def fetch_unscored(self, limit: int) -> list[UnscoredRow]: ...

    async def mark_scored(
        self, row_id: int, *, score: float, confidence: float, model: str
    ) -> None: ...

    async def upsert_aggregate(
        self,
        *,
        symbol: str,
        kind: str,
        bucket_start: datetime,
        mentions: int,
        score_sum: float,
        confidence_sum: float,
        weighted_score_sum: float,
        engagement_sum: float,
    ) -> None: ...

    async def mention_growth(
        self, symbols: list[str], *, now: datetime | None = None
    ) -> dict[str, float]: ...

    async def compact_hourly_to_daily(self, *, older_than: datetime) -> int: ...


class SqlContentRepository:
    """AsyncSession-backed repository. One instance per unit of work."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_items(self, items: list[RawItem]) -> int:
        if not items:
            return 0
        rows = [raw_item_to_row(i) for i in items]
        stmt = pg_insert(RawContent).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["source", "external_id"])
        result = await self._session.execute(stmt)
        await self._session.commit()
        # ON CONFLICT DO NOTHING: rowcount reflects only rows actually inserted.
        return int(getattr(result, "rowcount", 0) or 0)

    async def fetch_unscored(self, limit: int) -> list[UnscoredRow]:
        stmt = (
            select(RawContent)
            .where(RawContent.scored_at.is_(None))
            .order_by(RawContent.fetched_at)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            UnscoredRow(
                id=r.id,
                source=r.source,
                kind=r.kind,
                title=r.title,
                text=r.text,
                symbols=list(r.symbols or []),
                engagement=r.engagement,
                published_at=r.published_at,
            )
            for r in rows
        ]

    async def mark_scored(
        self, row_id: int, *, score: float, confidence: float, model: str
    ) -> None:
        stmt = (
            update(RawContent)
            .where(RawContent.id == row_id)
            .values(
                sentiment_score=score,
                sentiment_confidence=confidence,
                sentiment_model=model,
                scored_at=_utcnow(),
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def upsert_aggregate(
        self,
        *,
        symbol: str,
        kind: str,
        bucket_start: datetime,
        mentions: int,
        score_sum: float,
        confidence_sum: float,
        weighted_score_sum: float,
        engagement_sum: float,
    ) -> None:
        values = {
            "symbol": symbol,
            "kind": kind,
            "bucket_start": bucket_start,
            "mentions": mentions,
            "score_sum": score_sum,
            "confidence_sum": confidence_sum,
            "weighted_score_sum": weighted_score_sum,
            "engagement_sum": engagement_sum,
            "updated_at": _utcnow(),
        }
        stmt = pg_insert(ContentSentimentAgg).values(**values)
        # All columns are additive: on conflict, accumulate. Means (avg /
        # weighted_avg) are derived at read time from these sums, so there is no
        # running-mean drift here.
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "kind", "bucket_start"],
            set_={
                "mentions": ContentSentimentAgg.mentions + stmt.excluded.mentions,
                "score_sum": ContentSentimentAgg.score_sum + stmt.excluded.score_sum,
                "confidence_sum": (
                    ContentSentimentAgg.confidence_sum + stmt.excluded.confidence_sum
                ),
                "weighted_score_sum": (
                    ContentSentimentAgg.weighted_score_sum
                    + stmt.excluded.weighted_score_sum
                ),
                "engagement_sum": (
                    ContentSentimentAgg.engagement_sum + stmt.excluded.engagement_sum
                ),
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()

    #: Hours of history the growth baseline averages over, excluding the live hour.
    GROWTH_BASELINE_HOURS = 24

    async def mention_growth(
        self, symbols: list[str], *, now: datetime | None = None
    ) -> dict[str, float]:
        """Recent chatter for each symbol against its own recent normal.

        Returns ``{symbol: ratio}`` where ``0.0`` means "as talked about as
        usual", ``0.5`` means +50%, ``-0.5`` means half the usual. That is the
        shape ``Features.social_growth`` documents and ``_norm_social`` expects.

        The baseline is the symbol's own mean hourly mentions over the preceding
        24 hours, so a coin that is permanently noisy does not read as
        permanently surging — it is the change that carries information, not the
        volume. Symbols with no baseline yet are omitted rather than reported as
        flat: absent and average are different claims, and the scorer treats
        them differently.

        The measured hour is the last **complete** one, never the hour in
        progress. Comparing a partial hour against complete ones made the metric
        structurally negative: measured against production, every symbol read
        -1.0 at 37 minutes past, because the live bucket held 3 mentions where
        the previous hour held 73. The cost is up to an hour of lag, which is
        the right trade for a buzz signal that is not supposed to be tick-level.

        One grouped query for the whole batch; the caller resolves a poll's
        worth of symbols at once rather than per row.
        """
        if not symbols:
            return {}
        now = now or datetime.now(tz=UTC)
        live_hour = now.replace(minute=0, second=0, microsecond=0)
        current_start = live_hour - timedelta(hours=1)
        baseline_start = current_start - timedelta(hours=self.GROWTH_BASELINE_HOURS)

        a = ContentSentimentAgg
        rows = (
            await self._session.execute(
                select(
                    a.symbol,
                    func.sum(a.mentions)
                    .filter(a.bucket_start >= current_start)
                    .label("current"),
                    func.sum(a.mentions)
                    .filter(a.bucket_start < current_start)
                    .label("baseline"),
                    func.count(func.distinct(a.bucket_start))
                    .filter(a.bucket_start < current_start)
                    .label("baseline_hours"),
                )
                .where(a.symbol.in_(symbols))
                .where(a.bucket_start >= baseline_start)
                .where(a.bucket_start < live_hour)
                .group_by(a.symbol)
            )
        ).all()

        out: dict[str, float] = {}
        for symbol, current, baseline, baseline_hours in rows:
            if not baseline_hours or not baseline:
                continue
            per_hour = float(baseline) / float(baseline_hours)
            if per_hour <= 0:
                continue
            out[symbol] = float(current or 0) / per_hour - 1.0
        return out

    async def compact_hourly_to_daily(self, *, older_than: datetime) -> int:
        """Roll hourly buckets older than ``older_than`` into daily buckets.

        The daily upsert (additive) and the delete of the compacted hourly rows
        run in one transaction, so a day is never represented in both tables and
        re-running is a no-op. ``date_trunc(..., 'UTC')`` floors to UTC midnight
        regardless of the session timezone. Returns the hourly row count removed.
        """
        h = ContentSentimentAgg
        d = ContentSentimentAggDaily
        day = func.date_trunc("day", h.bucket_start, "UTC")
        src = (
            select(
                h.symbol,
                h.kind,
                day.label("bucket_start"),
                func.sum(h.mentions).label("mentions"),
                func.sum(h.score_sum).label("score_sum"),
                func.sum(h.confidence_sum).label("confidence_sum"),
                func.sum(h.weighted_score_sum).label("weighted_score_sum"),
                func.sum(h.engagement_sum).label("engagement_sum"),
            )
            .where(h.bucket_start < older_than)
            .group_by(h.symbol, h.kind, day)
        )
        ins = pg_insert(d).from_select(
            [
                "symbol",
                "kind",
                "bucket_start",
                "mentions",
                "score_sum",
                "confidence_sum",
                "weighted_score_sum",
                "engagement_sum",
            ],
            src,
        )
        stmt = ins.on_conflict_do_update(
            index_elements=["symbol", "kind", "bucket_start"],
            set_={
                "mentions": d.mentions + ins.excluded.mentions,
                "score_sum": d.score_sum + ins.excluded.score_sum,
                "confidence_sum": d.confidence_sum + ins.excluded.confidence_sum,
                "weighted_score_sum": (
                    d.weighted_score_sum + ins.excluded.weighted_score_sum
                ),
                "engagement_sum": d.engagement_sum + ins.excluded.engagement_sum,
                "updated_at": func.now(),
            },
        )
        await self._session.execute(stmt)
        result = await self._session.execute(
            delete(h).where(h.bucket_start < older_than)
        )
        await self._session.commit()
        return int(getattr(result, "rowcount", 0) or 0)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class FakeContentRepository:
    """In-memory double mirroring ContentRepository for unit tests."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    aggregates: dict[tuple[str, str, datetime], dict[str, Any]] = field(
        default_factory=dict
    )
    daily: dict[tuple[str, str, datetime], dict[str, Any]] = field(default_factory=dict)
    _seq: int = 0

    async def insert_items(self, items: list[RawItem]) -> int:
        seen = {(r["source"], r["external_id"]) for r in self.rows}
        inserted = 0
        for item in items:
            key = (item.source, item.external_id)
            if key in seen:
                continue
            seen.add(key)
            self._seq += 1
            row = raw_item_to_row(item)
            row["id"] = self._seq
            self.rows.append(row)
            inserted += 1
        return inserted

    async def fetch_unscored(self, limit: int) -> list[UnscoredRow]:
        out = [r for r in self.rows if r["scored_at"] is None][:limit]
        return [
            UnscoredRow(
                id=r["id"],
                source=r["source"],
                kind=r["kind"],
                title=r["title"],
                text=r["text"],
                symbols=list(r["symbols"]),
                engagement=r["engagement"],
                published_at=r["published_at"],
            )
            for r in out
        ]

    async def mark_scored(
        self, row_id: int, *, score: float, confidence: float, model: str
    ) -> None:
        for r in self.rows:
            if r["id"] == row_id:
                r["scored_at"] = _utcnow()
                r["sentiment_score"] = score
                r["sentiment_confidence"] = confidence
                r["sentiment_model"] = model
                return

    async def upsert_aggregate(
        self,
        *,
        symbol: str,
        kind: str,
        bucket_start: datetime,
        mentions: int,
        score_sum: float,
        confidence_sum: float,
        weighted_score_sum: float,
        engagement_sum: float,
    ) -> None:
        key = (symbol, kind, bucket_start)
        cur = self.aggregates.get(key)
        inc = {
            "mentions": mentions,
            "score_sum": score_sum,
            "confidence_sum": confidence_sum,
            "weighted_score_sum": weighted_score_sum,
            "engagement_sum": engagement_sum,
        }
        if cur is None:
            self.aggregates[key] = inc
        else:
            for k, v in inc.items():
                cur[k] += v

    async def mention_growth(
        self, symbols: list[str], *, now: datetime | None = None
    ) -> dict[str, float]:
        """Same semantics as the SQL implementation, over the in-memory buckets."""
        if not symbols:
            return {}
        now = now or datetime.now(tz=UTC)
        live_hour = now.replace(minute=0, second=0, microsecond=0)
        current_start = live_hour - timedelta(hours=1)
        baseline_start = current_start - timedelta(
            hours=SqlContentRepository.GROWTH_BASELINE_HOURS
        )
        wanted = set(symbols)
        current: dict[str, int] = {}
        baseline: dict[str, int] = {}
        hours: dict[str, set[datetime]] = {}
        for (symbol, _kind, bucket), agg in self.aggregates.items():
            if symbol not in wanted or not (baseline_start <= bucket < live_hour):
                continue
            if bucket >= current_start:
                current[symbol] = current.get(symbol, 0) + agg["mentions"]
            else:
                baseline[symbol] = baseline.get(symbol, 0) + agg["mentions"]
                hours.setdefault(symbol, set()).add(bucket)
        out: dict[str, float] = {}
        for symbol, total in baseline.items():
            n = len(hours.get(symbol, ()))
            if not n or not total:
                continue
            per_hour = total / n
            if per_hour <= 0:
                continue
            out[symbol] = current.get(symbol, 0) / per_hour - 1.0
        return out

    async def compact_hourly_to_daily(self, *, older_than: datetime) -> int:
        compacted = 0
        for key in list(self.aggregates):
            symbol, kind, bucket_start = key
            if bucket_start >= older_than:
                continue
            day = bucket_start.replace(hour=0, minute=0, second=0, microsecond=0)
            dkey = (symbol, kind, day)
            src = self.aggregates.pop(key)
            dst = self.daily.get(dkey)
            if dst is None:
                self.daily[dkey] = dict(src)
            else:
                for k in src:
                    dst[k] += src[k]
            compacted += 1
        return compacted
