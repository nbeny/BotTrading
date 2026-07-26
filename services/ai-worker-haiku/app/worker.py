"""Claude Haiku worker: fast triage / correlation / opportunity scoring.

Consumes market and sentiment events, correlates them per symbol, and emits
AnalysisEvent on market.analysis.events. Social/news presence is derived from
``SentimentEvent.input_kind`` (collectors no longer emit Social/NewsEvent).
Strong signals are flagged with ``escalate=True`` for the senior Sonnet analyst.
"""

from __future__ import annotations

import logging

from cmi_common.events import (
    AnalysisEvent,
    BaseEvent,
    DexEvent,
    PriceEvent,
    SentimentEvent,
    VolumeEvent,
)
from cmi_common.events.base import Source
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED

from .features import FeatureStore
from .scorer import ScorerConfig, local_opportunity

logger = logging.getLogger(__name__)
SERVICE = "ai-worker-haiku"


class HaikuWorker:
    """LLM-free triage: a deterministic scorer runs on every ready symbol and
    flags the strong+ambiguous ones for the senior (Sonnet/LLM) worker. No Claude
    call here, so triage is free, unlimited and always produces data."""

    def __init__(
        self,
        store: FeatureStore,
        producer: EventProducer,
        *,
        scorer_config: ScorerConfig | None = None,
    ) -> None:
        self._store = store
        self._producer = producer
        self._cfg = scorer_config or ScorerConfig()

    async def handle(self, event: BaseEvent) -> None:
        symbol, fields, topic = self._extract(event)
        if symbol is None:
            return
        EVENTS_CONSUMED.labels(SERVICE, topic, event.event_type).inc()
        features = await self._store.update(symbol, fields)
        # Only score once we have at least a price/dex anchor plus one signal.
        if not self._ready(features):
            return
        analysis = self._score(symbol, features, event.correlation_id)
        await self._producer.publish(Topic.ANALYSIS, analysis)
        EVENTS_PRODUCED.labels(SERVICE, Topic.ANALYSIS.value, analysis.event_type).inc()

    @staticmethod
    def _ready(f: dict) -> bool:
        has_market = any(k in f for k in ("price_change_pct_24h", "liquidity_usd"))
        has_signal = any(
            k in f for k in ("sentiment_score", "has_social", "volume_spike_ratio")
        )
        return has_market and has_signal

    def _extract(self, event: BaseEvent):
        if isinstance(event, PriceEvent):
            return event.symbol, {
                "price": float(event.price_usd),
                "price_change_pct_24h": event.price_change_pct_24h,
                # Carried for the scorer's liquidity proxy: a CEX-listed pair
                # gets no DexEvent, so without this its liquidity factor stays
                # a neutral guess forever.
                "volume_24h_usd": (
                    float(event.volume_24h_usd)
                    if event.volume_24h_usd is not None
                    else None
                ),
                "market_cap_rank": event.market_cap_rank,
                "is_trending": event.is_trending,
            }, Topic.PRICE.value
        if isinstance(event, VolumeEvent):
            return event.symbol, {
                "volume_spike_ratio": event.volume_spike_ratio,
            }, Topic.VOLUME.value
        if isinstance(event, DexEvent):
            return event.symbol, {
                "liquidity_usd": float(event.liquidity_usd or 0),
                "price_change_pct_1h": event.price_change_pct_1h,
                "is_new_pool": event.is_new_pool,
            }, Topic.DEX.value
        if isinstance(event, SentimentEvent):
            fields = {
                "sentiment_score": event.sentiment_score,
                "sentiment_confidence": event.confidence,
            }
            # Since Plan-1, social/news reach haiku only via sentiment; derive
            # presence flags from input_kind (collectors no longer emit
            # Social/NewsEvent on Kafka).
            if event.input_kind == "news":
                fields["has_news"] = True
            elif event.input_kind == "social":
                fields["has_social"] = True
            return event.symbol, fields, Topic.SENTIMENT.value
        return None, {}, ""

    def _score(
        self, symbol: str, features: dict, correlation_id: str
    ) -> AnalysisEvent:
        r = local_opportunity(features, self._cfg)
        return AnalysisEvent(
            source=Source.AI_HAIKU,
            correlation_id=correlation_id,
            symbol=symbol,
            opportunity_score=r.opportunity_score,
            confidence=r.confidence,
            reason=r.reason,
            summary="",
            price_change_pct_24h=features.get("price_change_pct_24h"),
            volume_spike_ratio=features.get("volume_spike_ratio"),
            sentiment_score=features.get("sentiment_score"),
            social_growth=features.get("social_growth"),
            escalate=r.escalate,
            ambiguous=r.ambiguous,
            block_reason=r.block_reason,
            factors_present=r.factors_present,
            liquidity_source=r.liquidity_source,
            meta={"features": features, "factors": r.factors, "triage": "deterministic"},
        )
