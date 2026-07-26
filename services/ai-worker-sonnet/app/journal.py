"""Build a journal entry from an analysis, plus whatever the analyst decided.

Pure: no I/O, no Redis, no Kafka. The worker publishes what this returns.
"""

from __future__ import annotations

from cmi_common.events import AnalysisEvent
from cmi_common.events.journal import JournalEntryEvent

# Scorer weights — must mirror ScorerConfig in ai-worker-haiku/app/scorer.py.
FACTOR_WEIGHTS = {
    "momentum": 0.35,
    "volume": 0.25,
    "sentiment": 0.25,
    "liquidity": 0.15,
}
# Below this gap between the top two contributions, "dominant" is meaningless.
DOMINANCE_MARGIN = 0.02


def dominant_factor(factors: dict[str, float]) -> tuple[str | None, float | None]:
    """Factor with the largest *contribution to the score*, i.e. weight x value.

    Not a raw argmax: momentum and volume both saturate at 1.0 on a strong move
    (observed on DEXE, whose 24h change swung 26.8% -> 47.5% while normalized
    momentum stayed pinned at 1.0), so an argmax would choose between them by
    dict ordering rather than by meaning. Near-ties report "mixed" rather than
    inventing a winner and, with it, a spurious cohort.
    """
    if not factors:
        return None, None
    ranked = sorted(
        ((w * factors.get(name, 0.0), name) for name, w in FACTOR_WEIGHTS.items()),
        reverse=True,
    )
    top_share, top_name = ranked[0]
    if len(ranked) > 1 and top_share - ranked[1][0] < DOMINANCE_MARGIN:
        return "mixed", round(top_share, 6)
    return top_name, round(top_share, 6)


def build_entry(
    analysis: AnalysisEvent,
    *,
    escalated: bool,
    sonnet_called: bool = False,
    sonnet_validated: bool | None = None,
    sonnet_score: int | None = None,
    sonnet_confidence: float | None = None,
    sonnet_direction: str | None = None,
    skip_reason: str | None = None,
) -> JournalEntryEvent:
    factors = analysis.meta.get("factors") or {}
    features = analysis.meta.get("features") or {}
    name, share = dominant_factor(factors)
    rank = features.get("market_cap_rank")
    return JournalEntryEvent(
        correlation_id=analysis.correlation_id,
        symbol=analysis.symbol,
        signal_event_id=analysis.event_id,
        factors=factors,
        features=features,
        score=analysis.opportunity_score,
        confidence=analysis.confidence,
        factors_present=analysis.factors_present,
        escalated=escalated,
        dominant_factor=name,
        dominant_factor_share=share,
        market_cap_rank=int(rank) if rank is not None else None,
        sonnet_called=sonnet_called,
        sonnet_validated=sonnet_validated,
        sonnet_score=sonnet_score,
        sonnet_confidence=sonnet_confidence,
        sonnet_direction=sonnet_direction,
        skip_reason=skip_reason,
    )
