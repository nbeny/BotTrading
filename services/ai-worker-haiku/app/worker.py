"""Claude Haiku worker: fast triage / correlation / opportunity scoring.

Consumes market and sentiment events, correlates them per symbol, and emits
AnalysisEvent on market.analysis.events. Social/news presence is derived from
``SentimentEvent.input_kind`` (collectors no longer emit Social/NewsEvent).
Strong signals are flagged with ``escalate=True`` for the senior Sonnet analyst.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time

from cmi_common.events import (
    AnalysisEvent,
    BaseEvent,
    DerivativesEvent,
    DeveloperEvent,
    DexEvent,
    FundamentalsEvent,
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

# One collector poll delivers a symbol's PriceEvent and VolumeEvent seconds
# apart, both derived from the same upstream response. Scoring on each arrival
# published two analyses per cycle -- measured 200 prices/min producing 376
# analyses/min -- the first being a strictly partial view of the second.
#
# So the symbol is allowed to settle: the analysis is emitted once the events of
# its window have stopped arriving. This aggregates the *inference*, never the
# events. FeatureStore.update runs on every single one, and its merge is
# monotone in keys, so the analysis that does get published is computed on a
# superset of the inputs of the ones that do not.
SETTLE_S = float(os.getenv("CMI_ANALYSIS_SETTLE_S", "3"))
# Ceiling, so a symbol that receives something every two seconds still gets
# analysed. Without it, settling on a busy symbol would become suppression.
MAX_DELAY_S = float(os.getenv("CMI_ANALYSIS_MAX_DELAY_S", "20"))
# How often the sweeper looks for symbols that have gone quiet.
SWEEP_S = float(os.getenv("CMI_ANALYSIS_SWEEP_S", "1"))


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
        clock=None,
    ) -> None:
        self._store = store
        self._producer = producer
        self._cfg = scorer_config or ScorerConfig()
        self._clock = clock or time.monotonic
        # symbol -> (last event seen at, first event of this window seen at,
        # correlation id to carry onto the analysis)
        self._pending: dict[str, tuple[float, float, str]] = {}
        self._stop = asyncio.Event()

    async def handle(self, event: BaseEvent) -> None:
        symbol, fields, topic = self._extract(event)
        if symbol is None:
            return
        EVENTS_CONSUMED.labels(SERVICE, topic, event.event_type).inc()
        # Unconditional: the event is folded into the symbol's state whether or
        # not it triggers an inference. Nothing arriving here is discarded.
        await self._store.update(symbol, fields)
        now = self._clock()
        _last, first, _corr = self._pending.get(symbol, (now, now, ""))
        self._pending[symbol] = (now, first, event.correlation_id)

    async def flush_settled(self) -> None:
        """Emit one analysis per symbol whose window has gone quiet.

        Read from the store rather than from what `handle` passed in: that is
        what makes this an aggregation of the window instead of a replay of the
        last event to arrive.
        """
        now = self._clock()
        due = [
            (symbol, corr)
            for symbol, (last, first, corr) in self._pending.items()
            if now - last >= SETTLE_S or now - first >= MAX_DELAY_S
        ]
        for symbol, correlation_id in due:
            del self._pending[symbol]
            features = await self._store.get(symbol)
            # Only score once we have at least a price/dex anchor plus one signal.
            if not self._ready(features):
                continue
            analysis = self._score(symbol, features, correlation_id)
            await self._producer.publish(Topic.ANALYSIS, analysis)
            EVENTS_PRODUCED.labels(
                SERVICE, Topic.ANALYSIS.value, analysis.event_type
            ).inc()

    def pending_symbols(self) -> int:
        return len(self._pending)

    async def run(self) -> None:
        """Sweeper loop. One task for the whole worker rather than one timer per
        symbol: the universe is ~200 symbols and a task each would cost more
        than the work it schedules."""
        while not self._stop.is_set():
            try:
                await self.flush_settled()
            except Exception:
                # A scoring or publish failure must not kill the sweeper and
                # strand every pending symbol behind it.
                logger.warning("analysis sweep failed", exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=SWEEP_S)

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _ready(f: dict) -> bool:
        has_market = any(k in f for k in ("price_change_pct_24h", "liquidity_usd"))
        has_signal = any(
            k in f for k in ("sentiment_score", "has_social", "volume_spike_ratio")
        )
        return has_market and has_signal

    def _extract(self, event: BaseEvent):
        if isinstance(event, PriceEvent):
            return (
                event.symbol,
                {
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
                },
                Topic.PRICE.value,
            )
        if isinstance(event, VolumeEvent):
            return (
                event.symbol,
                {
                    "volume_spike_ratio": event.volume_spike_ratio,
                },
                Topic.VOLUME.value,
            )
        if isinstance(event, DexEvent):
            return (
                event.symbol,
                {
                    "liquidity_usd": float(event.liquidity_usd or 0),
                    "price_change_pct_1h": event.price_change_pct_1h,
                    "is_new_pool": event.is_new_pool,
                },
                Topic.DEX.value,
            )
        if isinstance(event, SentimentEvent):
            fields = {
                "sentiment_score": event.sentiment_score,
                "sentiment_confidence": event.confidence,
            }
            # Carried straight through to the decision engine's social_score
            # axis, which read features["social_growth"] from the day it was
            # written and never found it: nothing computed the value, so 20% of
            # the model weight sat at zero. sentiment-service owns the mention
            # counts and now derives it. Only set when present -- a missing
            # baseline must stay absent rather than become a flat 0.0.
            if event.social_growth is not None:
                fields["social_growth"] = event.social_growth
            # Since Plan-1, social/news reach haiku only via sentiment; derive
            # presence flags from input_kind (collectors no longer emit
            # Social/NewsEvent on Kafka).
            if event.input_kind == "news":
                fields["has_news"] = True
            elif event.input_kind == "social":
                fields["has_social"] = True
            return event.symbol, fields, Topic.SENTIMENT.value
        if isinstance(event, DerivativesEvent):
            # Context, not a trigger: _ready() is deliberately not relaxed for
            # these. Scoring a symbol we have no price for would invent an
            # opportunity out of an exchange statistic.
            return (
                event.symbol,
                {
                    "funding_rate_8h": event.funding_rate_8h,
                    "funding_annualized_pct": event.funding_annualized_pct,
                    "open_interest_usd": (
                        float(event.open_interest_usd)
                        if event.open_interest_usd is not None
                        else None
                    ),
                    "open_interest_change_pct_24h": event.open_interest_change_pct_24h,
                    "long_short_account_ratio": event.long_short_account_ratio,
                },
                Topic.DERIVATIVES.value,
            )
        if isinstance(event, FundamentalsEvent):
            # has_unlock_schedule is a bool and False is meaningful, but the
            # store only drops None on merge, so False survives — which is what
            # keeps "DefiLlama does not track this" distinct from "we read the
            # schedule and nothing is coming".
            return (
                event.symbol,
                {
                    "tvl_usd": (
                        float(event.tvl_usd) if event.tvl_usd is not None else None
                    ),
                    "tvl_change_pct_7d": event.tvl_change_pct_7d,
                    "fees_change_pct_7d": event.fees_change_pct_7d,
                    # Stored absolute rather than as days remaining: a stored
                    # countdown would age silently between the collector's poll
                    # and the decision that reads it.
                    "next_unlock_at": (
                        event.next_unlock_at.isoformat()
                        if event.next_unlock_at
                        else None
                    ),
                    "next_unlock_pct_supply": event.next_unlock_pct_supply,
                    "has_unlock_schedule": event.has_unlock_schedule,
                },
                Topic.FUNDAMENTALS.value,
            )
        if isinstance(event, DeveloperEvent):
            # Contexte, pas déclencheur — même statut que DerivativesEvent :
            # _ready() n'est délibérément pas relâché pour ces événements.
            # Scorer un symbole dont on n'a pas le prix inventerait une
            # opportunité à partir d'une statistique de dépôt.
            #
            # all_repos_archived est un bool dont False est significatif, comme
            # has_unlock_schedule : le store ne laisse tomber que les None à la
            # fusion, donc False survit — ce qui garde « on a regardé, ce n'est
            # pas mort » distinct de « on n'a pas regardé ». Pour la même
            # raison, le 0.0 de commit_ratio_4w d'un projet entièrement archivé
            # traverse comme la mesure qu'il est.
            return (
                event.symbol,
                {
                    "commit_ratio_4w": event.commit_ratio_4w,
                    "pr_ratio_4w": event.pr_ratio_4w,
                    "days_since_push": event.days_since_push,
                    "star_growth_pct_7d": event.star_growth_pct_7d,
                    "all_repos_archived": event.all_repos_archived,
                    "dev_repo_count": event.repo_count,
                },
                Topic.DEVELOPER.value,
            )
        return None, {}, ""

    def _score(self, symbol: str, features: dict, correlation_id: str) -> AnalysisEvent:
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
            meta={
                "features": features,
                "factors": r.factors,
                "triage": "deterministic",
            },
        )
