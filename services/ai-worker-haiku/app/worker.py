"""Claude Haiku worker: fast triage / correlation / opportunity scoring.

Consumes market and sentiment events, correlates them per symbol, and emits
AnalysisEvent on market.analysis.events. Social/news presence is derived from
``SentimentEvent.input_kind`` (collectors no longer emit Social/NewsEvent).
Strong signals are flagged with ``escalate=True`` for the senior Sonnet analyst.
"""

from __future__ import annotations

import logging

from cmi_common.ai import ClaudeClient
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

logger = logging.getLogger(__name__)
SERVICE = "ai-worker-haiku"

SYSTEM = (
    "You are a fast crypto market triage analyst. You correlate price, volume, "
    "DEX liquidity, news and social signals for a single token and output a "
    "concise opportunity assessment. Respond ONLY with a JSON object: "
    '{"opportunity_score": int 0-100, "confidence": float 0-1, '
    '"reason": str, "summary": str}. Be conservative; low-liquidity or '
    "contradictory signals must lower the score."
)


class HaikuWorker:
    def __init__(
        self,
        claude: ClaudeClient,
        store: FeatureStore,
        producer: EventProducer,
        *,
        escalation_threshold: int = 75,
    ) -> None:
        self._claude = claude
        self._store = store
        self._producer = producer
        self._threshold = escalation_threshold

    async def handle(self, event: BaseEvent) -> None:
        symbol, fields, topic = self._extract(event)
        if symbol is None:
            return
        EVENTS_CONSUMED.labels(SERVICE, topic, event.event_type).inc()
        features = await self._store.update(symbol, fields)
        # Only analyze once we have at least a price/dex anchor plus one signal.
        if not self._ready(features):
            return
        analysis = await self._analyze(symbol, features, event.correlation_id)
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

    async def _analyze(
        self, symbol: str, features: dict, correlation_id: str
    ) -> AnalysisEvent:
        prompt = (
            f"Token: {symbol}\nCorrelated features (last 15m):\n"
            + "\n".join(f"- {k}: {v}" for k, v in features.items())
        )
        resp = await self._claude.complete(system=SYSTEM, prompt=prompt, service=SERVICE)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001 - degrade gracefully
            data = {"opportunity_score": 0, "confidence": 0.0, "reason": "parse-error"}
        score = int(max(0, min(100, data.get("opportunity_score", 0))))
        return AnalysisEvent(
            source=Source.AI_HAIKU,
            correlation_id=correlation_id,
            symbol=symbol,
            opportunity_score=score,
            confidence=float(data.get("confidence", 0.0)),
            reason=data.get("reason", ""),
            summary=data.get("summary", ""),
            price_change_pct_24h=features.get("price_change_pct_24h"),
            volume_spike_ratio=features.get("volume_spike_ratio"),
            sentiment_score=features.get("sentiment_score"),
            social_growth=features.get("social_growth"),
            escalate=score >= self._threshold,
            meta={"features": features},
        )
