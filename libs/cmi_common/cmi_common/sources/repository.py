"""Persistence for raw ingested content + the sentiment aggregate rollup.

``raw_item_to_row`` is the pure mapping (unit-tested). ``SqlContentRepository``
is the async SQLAlchemy implementation (integration-tested against Postgres).
``FakeContentRepository`` is an in-memory double used by loop/worker unit tests
— it mirrors the same protocol so tests never need a live database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ContentSentimentAgg, RawContent
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
        window_start: datetime,
        window_size: int,
        mentions: int,
        unique_authors: int,
        engagement_sum: float,
        avg_sentiment: float,
        weighted_sentiment: float,
    ) -> None: ...


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
        window_start: datetime,
        window_size: int,
        mentions: int,
        unique_authors: int,
        engagement_sum: float,
        avg_sentiment: float,
        weighted_sentiment: float,
    ) -> None:
        values = {
            "symbol": symbol,
            "kind": kind,
            "window_start": window_start,
            "window_size": window_size,
            "mentions": mentions,
            "unique_authors": unique_authors,
            "engagement_sum": engagement_sum,
            "avg_sentiment": avg_sentiment,
            "weighted_sentiment": weighted_sentiment,
            "updated_at": _utcnow(),
        }
        stmt = pg_insert(ContentSentimentAgg).values(**values)
        # On conflict, counts accumulate and the sentiment values become the
        # mentions-weighted running mean: (old*old_n + inc*inc_n) / (old_n + inc_n).
        # Table-qualified columns reference the pre-update row, excluded the
        # incoming payload, so the denominator uses the OLD count. unique_authors
        # accumulates as an approximate contribution count (an exact distinct
        # count would require querying raw_content, which no consumer needs yet).
        old_n = ContentSentimentAgg.mentions
        inc_n = stmt.excluded.mentions
        total_n = old_n + inc_n
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "kind", "window_start", "window_size"],
            set_={
                "mentions": total_n,
                "unique_authors": (
                    ContentSentimentAgg.unique_authors + stmt.excluded.unique_authors
                ),
                "engagement_sum": (
                    ContentSentimentAgg.engagement_sum + stmt.excluded.engagement_sum
                ),
                "avg_sentiment": (
                    ContentSentimentAgg.avg_sentiment * old_n
                    + stmt.excluded.avg_sentiment * inc_n
                )
                / total_n,
                "weighted_sentiment": (
                    ContentSentimentAgg.weighted_sentiment * old_n
                    + stmt.excluded.weighted_sentiment * inc_n
                )
                / total_n,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class FakeContentRepository:
    """In-memory double mirroring ContentRepository for unit tests."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    aggregates: dict[tuple[str, str, datetime, int], dict[str, Any]] = field(
        default_factory=dict
    )
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
        window_start: datetime,
        window_size: int,
        mentions: int,
        unique_authors: int,
        engagement_sum: float,
        avg_sentiment: float,
        weighted_sentiment: float,
    ) -> None:
        key = (symbol, kind, window_start, window_size)
        cur = self.aggregates.get(key)
        if cur is None:
            self.aggregates[key] = {
                "mentions": mentions,
                "unique_authors": unique_authors,
                "engagement_sum": engagement_sum,
                "avg_sentiment": avg_sentiment,
                "weighted_sentiment": weighted_sentiment,
            }
        else:
            # Mirror SqlContentRepository's ON CONFLICT DO UPDATE: counts
            # accumulate; sentiment values become the mentions-weighted running
            # mean (computed with the OLD mentions before updating it).
            total = cur["mentions"] + mentions
            cur["avg_sentiment"] = (
                cur["avg_sentiment"] * cur["mentions"] + avg_sentiment * mentions
            ) / total
            cur["weighted_sentiment"] = (
                cur["weighted_sentiment"] * cur["mentions"]
                + weighted_sentiment * mentions
            ) / total
            cur["mentions"] = total
            cur["unique_authors"] += unique_authors
            cur["engagement_sum"] += engagement_sum
