"""Claude Sonnet worker: senior analyst validating high-value opportunities.

Consumes market.analysis.events, ignores everything below the escalation bar,
and performs deep multi-signal validation on strong candidates, producing
decision.events with an explicit rationale and enumerated risks.
"""

from __future__ import annotations

import logging

from cmi_common.ai import ClaudeClient
from cmi_common.events import AnalysisEvent, BaseEvent
from cmi_common.events.base import Source
from cmi_common.events.decision import DecisionEvent, Direction
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_CONSUMED, EVENTS_PRODUCED

from .journal import build_entry

logger = logging.getLogger(__name__)
SERVICE = "ai-worker-sonnet"

SYSTEM = (
    "You are a senior crypto market analyst. You receive a pre-scored "
    "opportunity with correlated evidence and must decide whether it is a "
    "genuine, actionable trade idea. Weigh momentum, liquidity, sentiment and "
    "news impact against manipulation and liquidity risk. Respond ONLY with a "
    'JSON object: {"validated": bool, "direction": "long|short|watch", '
    '"opportunity_score": int 0-100, "confidence": float 0-1, '
    '"rationale": str, "key_risks": [str]}. Reject thin, contradictory or '
    "manipulation-prone setups."
)


class SonnetWorker:
    def __init__(
        self,
        claude: ClaudeClient,
        producer: EventProducer,
        cache,
        *,
        max_calls_per_hour: int = 12,
        symbol_cooldown_s: int = 900,
    ) -> None:
        self._claude = claude
        self._producer = producer
        self._cache = cache
        self._max_per_hour = max_calls_per_hour
        self._cooldown_s = symbol_cooldown_s

    async def handle(self, event: BaseEvent) -> None:
        if not isinstance(event, AnalysisEvent):
            return
        EVENTS_CONSUMED.labels(SERVICE, Topic.ANALYSIS.value, event.event_type).inc()

        # Senior analyst only intervenes on important signals. The ones it skips
        # are the control group: without them the gate cannot be evaluated.
        if not event.escalate:
            await self._journal(build_entry(event, escalated=False))
            return

        # Budget/cooldown gate — the only place we spend the subscription. A
        # symbol blocked here is simply re-evaluated on its next escalation.
        if not await self._may_call(event.symbol):
            logger.info("sonnet skip %s (cooldown/budget)", event.symbol)
            await self._journal(
                build_entry(event, escalated=True, skip_reason="cooldown_or_budget")
            )
            return

        decision = await self._validate(event)
        if decision is None:
            await self._journal(
                build_entry(
                    event, escalated=True, sonnet_called=True, sonnet_validated=False
                )
            )
            return

        await self._producer.publish(Topic.DECISION, decision)
        EVENTS_PRODUCED.labels(SERVICE, Topic.DECISION.value, decision.event_type).inc()
        await self._journal(
            build_entry(
                event,
                escalated=True,
                sonnet_called=True,
                sonnet_validated=True,
                sonnet_score=decision.opportunity_score,
                sonnet_confidence=decision.confidence,
                sonnet_direction=decision.direction,
            ),
            decision_event_id=decision.event_id,
        )

    async def _journal(self, entry, *, decision_event_id: str | None = None) -> None:
        """Best effort. Losing a journal row is acceptable; stalling the trading
        pipeline because the audit trail failed is not."""
        try:
            if decision_event_id is not None:
                # BaseEvent is frozen: attaching the id needs a copy, not assignment.
                entry = entry.model_copy(
                    update={"decision_event_id": decision_event_id}
                )
            await self._producer.publish(Topic.JOURNAL, entry)
        except Exception:
            logger.warning("journal publish failed for %s", entry.symbol, exc_info=True)

    async def _may_call(self, symbol: str) -> bool:
        """True if we're allowed to spend an LLM call on this symbol now."""
        if await self._cache.get_json(f"ai:cooldown:{symbol}"):
            return False
        # Fixed-window token bucket shared across AI workers.
        if not await self._cache.allow("ai:budget:calls", self._max_per_hour, 3600):
            return False
        await self._cache.set_json(f"ai:cooldown:{symbol}", 1, ttl_seconds=self._cooldown_s)
        return True

    async def _validate(self, event: AnalysisEvent) -> DecisionEvent | None:
        prompt = (
            f"Symbol: {event.symbol}\n"
            f"Haiku score: {event.opportunity_score} (conf {event.confidence})\n"
            f"Reason: {event.reason}\n"
            f"Summary: {event.summary}\n"
            f"price_change_pct_24h: {event.price_change_pct_24h}\n"
            f"volume_spike_ratio: {event.volume_spike_ratio}\n"
            f"sentiment_score: {event.sentiment_score}\n"
            f"social_growth: {event.social_growth}\n"
            f"Full features: {event.meta.get('features', {})}"
        )
        resp = await self._claude.complete(system=SYSTEM, prompt=prompt, service=SERVICE)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            logger.warning("sonnet response parse failed for %s", event.symbol)
            return None
        if not data.get("validated", False):
            logger.info("sonnet rejected %s", event.symbol)
            return None
        try:
            direction = Direction(data.get("direction", "long"))
        except ValueError:
            direction = Direction.LONG
        return DecisionEvent(
            source=Source.AI_SONNET,
            correlation_id=event.correlation_id,
            symbol=event.symbol,
            direction=direction,
            opportunity_score=int(
                max(0, min(100, data.get("opportunity_score", event.opportunity_score)))
            ),
            confidence=float(data.get("confidence", event.confidence)),
            rationale=data.get("rationale", ""),
            key_risks=list(data.get("key_risks", [])),
            ai_validated=True,
            correlated_event_ids=[event.event_id, *event.correlated_event_ids],
        )
