"""Deterministic decision-engine scoring."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load the decision-engine scoring module directly (services aren't a package).
_spec = importlib.util.spec_from_file_location(
    "de_scoring",
    Path(__file__).resolve().parents[1]
    / "services"
    / "decision-engine"
    / "app"
    / "scoring.py",
)
scoring = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = scoring  # required for dataclass(slots=True)
_spec.loader.exec_module(scoring)


def test_empty_features_produce_no_score_at_all() -> None:
    # Was: score 5, from a neutral news axis applied to a symbol we know nothing
    # about. Under renormalisation an axis with no input is *excluded* rather
    # than valued, so an empty symbol has no denominator and therefore no score.
    # The confidence of 0.0 is what carries the emptiness — as it did before,
    # except the score no longer invents a number to sit beside it.
    result = scoring.score(scoring.Features())
    assert result.opportunity_score == 0
    assert result.confidence == 0.0
    assert result.breakdown == {}


def test_bearish_news_scores_below_neutral_news() -> None:
    # The original conflation — silence scoring like panic — is now prevented by
    # exclusion rather than by a neutral constant, so the meaningful comparison
    # is between two symbols that both *have* a news reading. Silence no longer
    # participates in the axis at all, which is a stronger guarantee than
    # outscoring panic: it cannot be scored on evidence nobody collected.
    # A price anchor rides along because a lone news axis is now below the
    # minimum evidence floor -- and because haiku's _ready() means no symbol
    # ever reaches the scorer without one anyway.
    neutral = scoring.score(
        scoring.Features(sentiment_score=0.0, price_change_pct_24h=0.0)
    )
    panicking = scoring.score(
        scoring.Features(sentiment_score=-1.0, price_change_pct_24h=0.0)
    )
    assert panicking.opportunity_score < neutral.opportunity_score


def test_strong_signals_score_high() -> None:
    f = scoring.Features(
        price_change_pct_24h=25,
        volume_spike_ratio=6,
        liquidity_usd=5_000_000,
        sentiment_score=0.8,
        social_growth=1.5,
        news_impact=1.0,
    )
    result = scoring.score(f)
    assert result.opportunity_score > 60
    # Five of eight axes. Derivatives, fundamentals and developer activity are
    # absent for a symbol with no perp, no protocol and no mapped repo, which is
    # most of them — and under renormalisation that costs confidence without
    # costing score. The figure fell from 0.75 to 0.69 when developer_activity
    # landed: the same five axes now back a smaller share of a larger model.
    assert result.confidence == 0.69


def test_confidence_reflects_missing_signals() -> None:
    # Two of seven axes: enough to clear the evidence floor, far from complete.
    f = scoring.Features(price_change_pct_24h=10, volume_spike_ratio=2.0)
    result = scoring.score(f)
    assert 0.0 < result.confidence < 0.5


def test_weights_sum_to_one() -> None:
    assert abs(sum(scoring.WEIGHTS.values()) - 1.0) < 1e-9
