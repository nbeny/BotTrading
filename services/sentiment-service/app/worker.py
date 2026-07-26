"""DB-sourced sentiment worker.

Polls ``raw_content`` for unscored rows, scores each with the HF scorer, writes
the score back, upserts the per-symbol/window aggregate, and publishes one
``SentimentEvent`` per (item x detected symbol) so downstream keeps receiving
per-symbol sentiment. Replaces the former Kafka news/social consumer.

Delivery semantics: a row is marked scored *before* its ``SentimentEvent`` is
published, so publishing is best-effort — if the process dies (or ``publish``
raises) mid-row, the row is already ``scored_at != NULL`` and won't be retried,
dropping the un-published event(s). This favours never double-scoring a row over
never losing an event, which is acceptable for a sentiment feed. A failed
``publish`` propagates to ``run_periodic`` (logged, not silently swallowed).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol

from cmi_common.events.sentiment import SentimentEvent
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED
from cmi_common.sources import ContentRepository

logger = logging.getLogger(__name__)
SERVICE = "sentiment-service"


class ScoreResult(Protocol):
    score: float
    confidence: float
    model_name: str


class Scorer(Protocol):
    def score(self, text: str) -> ScoreResult: ...


class SentimentDbWorker:
    def __init__(
        self,
        repository: ContentRepository,
        scorer: Scorer,
        producer: EventProducer,
        *,
        batch: int = 100,
    ) -> None:
        self._repo = repository
        self._scorer = scorer
        self._producer = producer
        self._batch = batch

    async def run_once(self) -> int:
        rows = await self._repo.fetch_unscored(self._batch)
        # One grouped lookup for the whole batch rather than one per row. This
        # service owns the mention counts, so it is where the derivative belongs;
        # ai-worker-haiku is a pure Kafka consumer with no database to compute it
        # from, which is why its social_score axis had been dead since Plan-1.
        growth = await self._repo.mention_growth(
            sorted({s for row in rows for s in (row.symbols or [])})
        )
        for row in rows:
            text = f"{row.title}. {row.text}" if row.title else row.text
            result = self._scorer.score(text)
            EVENTS_CONSUMED.labels(SERVICE, "raw_content", row.kind).inc()
            await self._repo.mark_scored(
                row.id,
                score=result.score,
                confidence=result.confidence,
                model=result.model_name,
            )
            # Every stored row carries at least one symbol: the collectors'
            # normalizer assigns MARKET to symbol-less crypto content and drops
            # everything else. A fallback here would mask a broken invariant --
            # but silence would hide it just as well, so say so out loud. Rows
            # written before the normalizer shipped land here until the table is
            # rebuilt, and so would any future writer that skips the poll loop.
            symbols = row.symbols
            if not symbols:
                logger.warning(
                    "raw_content row %s from %s has no symbols; scored but not "
                    "aggregated. Upstream normalization was bypassed.",
                    row.id,
                    row.source,
                )
            bucket_start = _floor_hour(row.published_at)
            engagement = float(row.engagement or 0.0)
            for symbol in symbols:
                await self._repo.upsert_aggregate(
                    symbol=symbol,
                    kind=row.kind,
                    bucket_start=bucket_start,
                    mentions=1,
                    score_sum=result.score,
                    confidence_sum=result.confidence,
                    weighted_score_sum=result.score * result.confidence,
                    engagement_sum=engagement,
                )
                await self._producer.publish(
                    Topic.SENTIMENT,
                    SentimentEvent(
                        symbol=symbol,
                        sentiment_score=result.score,
                        confidence=result.confidence,
                        model_name=result.model_name,
                        input_kind=row.kind,
                        sample_size=1,
                        social_growth=growth.get(symbol),
                        meta={"source": row.source},
                    ),
                )
                EVENTS_PRODUCED.labels(
                    SERVICE, Topic.SENTIMENT.value, "SentimentEvent"
                ).inc()
        logger.info("sentiment worker scored %d rows", len(rows))
        return len(rows)


def _floor_hour(dt: datetime | None) -> datetime:
    base = dt if dt is not None else datetime.now(tz=UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    return base.replace(minute=0, second=0, microsecond=0)
