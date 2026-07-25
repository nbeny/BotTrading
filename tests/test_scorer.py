"""Deterministic opportunity scorer — the LLM-free triage layer.

Loaded via importlib under a unique name so it doesn't shadow the `app` package
of the other services when the whole suite runs in one pytest session.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[1]
    / "services" / "ai-worker-haiku" / "app" / "scorer.py"
)
_spec = importlib.util.spec_from_file_location("haiku_scorer", _PATH)
scorer = importlib.util.module_from_spec(_spec)
sys.modules["haiku_scorer"] = scorer  # dataclasses resolve types via sys.modules
_spec.loader.exec_module(scorer)
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
