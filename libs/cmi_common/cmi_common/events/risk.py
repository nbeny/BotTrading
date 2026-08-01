"""Risk events produced by the independent risk-engine."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import BaseEvent, EventType, Source
from .decision import Direction


class RiskApprovedEvent(BaseEvent):
    """Published on ``risk.approved.events`` — the actionable trading signal.

    Example::

        {
            "event_type": "RiskApprovedEvent",
            "symbol": "SOL",
            "entry_price": 150,
            "stop_loss": 142,
            "take_profit": 165,
            "confidence": 0.87
        }
    """

    event_type: Literal[EventType.RISK_APPROVED] = EventType.RISK_APPROVED
    source: Source = Source.RISK_ENGINE
    symbol: str
    direction: Direction = Direction.LONG
    entry_price: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    confidence: float = Field(..., ge=0, le=1)
    # Position sizing outputs from the risk engine.
    position_size_pct: float = Field(
        default=0.0, ge=0, le=1, description="Fraction of portfolio"
    )
    risk_reward_ratio: float = Field(default=0.0, ge=0)
    max_exposure_usd: float | None = Field(default=None, ge=0)
    decision_event_id: str | None = None

    # --- opportunity metadata, propagated from the DecisionEvent this approval
    # was raised for (control-api / trading-engine surface this event as the
    # frontend's `Opportunity`, which needs all four). Defaulted so an older
    # producer's event — or a payload reconstructed from a partial Redis record
    # — still validates; see the "moitié B" idiom in journal.py. `opportunity_score`
    # mirrors DecisionEvent's 0..100 int scale (not `confidence`'s 0..1) and stays
    # `None`, not 0, when the originating decision is unknown: a measured zero and
    # an absent score must stay distinguishable downstream (see CLAUDE.md on
    # scoring — the same corollary applies here).
    opportunity_score: int | None = Field(default=None, ge=0, le=100)
    rationale: str = ""
    key_risks: list[str] = Field(default_factory=list)
    ai_validated: bool = False

    # Provenance of the *analysis* this approval was raised for — e.g. which
    # AI worker or engine produced the underlying decision. Distinct from
    # `source` above, which is this event's own producer and is always
    # `risk_engine`: the approving service, not the analysis behind it. The
    # frontend's `Opportunity.source` (see the mock, frontend/src/lib/mock/
    # store.ts) means the latter, so `_signal_payload` reads this field, not
    # `source`. Defaulted so an older producer's event — or a payload
    # reconstructed from a partial Redis record — still validates; see the
    # "moitié B" idiom in journal.py.
    decision_source: Source | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_levels(self) -> RiskApprovedEvent:
        if self.direction == Direction.LONG:
            if not (self.stop_loss < self.entry_price < self.take_profit):
                raise ValueError(
                    "LONG requires stop_loss < entry_price < take_profit"
                )
        elif self.direction == Direction.SHORT:
            if not (self.take_profit < self.entry_price < self.stop_loss):
                raise ValueError(
                    "SHORT requires take_profit < entry_price < stop_loss"
                )
        return self

    def partition_key(self) -> str:
        return self.symbol


class RiskRejectedEvent(BaseEvent):
    """Emitted (on ``decision.events`` DLQ / audit topic) when a decision is
    blocked by the risk engine, for observability and back-testing."""

    event_type: Literal[EventType.RISK_REJECTED] = EventType.RISK_REJECTED
    source: Source = Source.RISK_ENGINE
    symbol: str
    reason: str
    decision_event_id: str | None = None

    def partition_key(self) -> str:
        return self.symbol
