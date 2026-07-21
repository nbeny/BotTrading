"""Consumes news + social events, scores them, publishes sentiment events."""

from __future__ import annotations

import logging

from cmi_common.events import BaseEvent, NewsEvent, SocialEvent
from cmi_common.events.sentiment import SentimentEvent
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED

from .scorer import SentimentScorer

logger = logging.getLogger(__name__)
SERVICE = "sentiment-service"


class SentimentHandler:
    def __init__(self, scorer: SentimentScorer, producer: EventProducer) -> None:
        self._scorer = scorer
        self._producer = producer

    async def handle(self, event: BaseEvent) -> None:
        if isinstance(event, NewsEvent):
            await self._handle_news(event)
        elif isinstance(event, SocialEvent):
            await self._handle_social(event)

    async def _publish(self, ev: SentimentEvent, topic_label: str) -> None:
        await self._producer.publish(Topic.SENTIMENT, ev)
        EVENTS_PRODUCED.labels(SERVICE, Topic.SENTIMENT.value, ev.event_type).inc()

    async def _handle_news(self, event: NewsEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.NEWS.value, event.event_type).inc()
        text = f"{event.title}. {event.body}"
        result = self._scorer.score(text)
        # One sentiment event per tagged symbol so downstream keys by symbol.
        for symbol in event.symbols or ["MARKET"]:
            await self._publish(
                SentimentEvent(
                    correlation_id=event.correlation_id,
                    symbol=symbol,
                    sentiment_score=result.score,
                    confidence=result.confidence,
                    model_name=result.model_name,
                    input_kind="news",
                    sample_size=1,
                    meta={"article_id": event.article_id},
                ),
                Topic.SENTIMENT.value,
            )

    async def _handle_social(self, event: SocialEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.SOCIAL.value, event.event_type).inc()
        result = self._scorer.score(event.text_sample)
        await self._publish(
            SentimentEvent(
                correlation_id=event.correlation_id,
                symbol=event.symbol,
                sentiment_score=result.score,
                confidence=result.confidence,
                model_name=result.model_name,
                input_kind="social",
                sample_size=max(1, event.mentions),
                meta={"subreddit": event.subreddit or ""},
            ),
            Topic.SENTIMENT.value,
        )
