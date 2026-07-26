"""Audit record of one AI-call decision, escalated or not.

Written for every analysis — including the ones that never reach the analyst.
Without that control group the opportunity gate can never be evaluated: you
would only ever observe the signals it let through.

Half of these fields (the dedup discriminants) are declared here but populated
by a later change; they are nullable so the two halves can ship independently.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .base import BaseEvent, EventType, Source


class JournalEntryEvent(BaseEvent):
    """Published on ``journal.entries`` by ai-worker-sonnet, persisted by the
    api-gateway. Carries the full decision context so an outcome can later be
    explained, not merely scored."""

    event_type: Literal[EventType.JOURNAL_ENTRY] = EventType.JOURNAL_ENTRY
    source: Source = Source.AI_SONNET

    symbol: str
    signal_event_id: str

    # --- état décisionnel ---------------------------------------------------
    factors: dict[str, float] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    score: int = Field(..., ge=0, le=100)
    confidence: float = Field(..., ge=0, le=1)
    factors_present: int = Field(..., ge=0, le=4)
    escalated: bool = False

    # --- contexte d'entrée théorique : le « pourquoi », pas seulement le
    # « bon/mauvais ». La volatilité est essentielle : rules._compute_levels
    # applique un stop fixe à 5 %, qui est un vrai signal de sortie sur un actif
    # à 2 % de volatilité et du pur bruit sur un actif à 15 %.
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward_ratio: float | None = None
    volatility_1h: float | None = None
    volatility_24h: float | None = None
    dominant_factor: str | None = None
    dominant_factor_share: float | None = None
    market_cap_rank: int | None = None

    # --- verdict de l'analyste ----------------------------------------------
    sonnet_called: bool = False
    sonnet_validated: bool | None = None
    sonnet_score: int | None = None
    sonnet_confidence: float | None = None
    sonnet_direction: str | None = None
    skip_reason: str | None = None

    # --- moitié B : alimentée par le chantier de déduplication --------------
    cooldown_verdict: bool | None = None
    dedup_verdict: bool | None = None
    dedup_trigger: str | None = None
    drift_momentum: float | None = None
    drift_volume: float | None = None
    drift_sentiment: float | None = None
    drift_liquidity: float | None = None
    sign_flip_chg: bool | None = None
    sign_flip_sentiment: bool | None = None
    score_anchor: int | None = None
    factors_present_anchor: int | None = None
    seconds_since_anchor: int | None = None
    regime: str | None = None
    regime_anchor: str | None = None
    dedup_version: str | None = None
    dedup_quantile: float | None = None
    dedup_deltas: dict[str, float | None] = Field(default_factory=dict)

    # --- aval : chaînage vers risque et exécution ---------------------------
    decision_event_id: str | None = None
    risk_event_id: str | None = None

    def partition_key(self) -> str:
        return self.symbol
