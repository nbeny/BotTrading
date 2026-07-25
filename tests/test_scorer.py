"""Deterministic opportunity scorer — the LLM-free triage layer."""

from __future__ import annotations

import pytest
from service_modules import load_service_module

scorer = load_service_module("ai-worker-haiku", "scorer")
ScorerConfig = scorer.ScorerConfig
local_opportunity = scorer.local_opportunity


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError):
        ScorerConfig(w_momentum=0.5, w_volume=0.5, w_sentiment=0.5, w_liquidity=0.5)


def test_strong_aligned_move_scores_high() -> None:
    r = local_opportunity(
        {
            "price_change_pct_24h": 12.0,
            "volume_spike_ratio": 4.0,
            "sentiment_score": 0.7,
            "liquidity_usd": 800_000,
        }
    )
    assert r.opportunity_score >= 70
    assert not r.ambiguous
    assert r.escalate
    assert r.confidence >= 0.6


def test_flat_market_scores_low_and_no_escalate() -> None:
    r = local_opportunity(
        {
            "price_change_pct_24h": 0.3,
            "volume_spike_ratio": 1.0,
            "sentiment_score": 0.0,
            "liquidity_usd": 500_000,
        }
    )
    assert r.opportunity_score < 30
    assert not r.escalate


def test_price_sentiment_disagreement_is_ambiguous() -> None:
    r = local_opportunity(
        {
            "price_change_pct_24h": 9.0,
            "volume_spike_ratio": 3.0,
            "sentiment_score": -0.6,
            "liquidity_usd": 400_000,
        }
    )
    assert r.ambiguous
    assert r.escalate


def test_missing_features_do_not_crash() -> None:
    r = local_opportunity({"price_change_pct_24h": 5.0})
    assert 0 <= r.opportunity_score <= 100
    assert isinstance(r.escalate, bool)
    assert "deterministic triage" in r.reason
