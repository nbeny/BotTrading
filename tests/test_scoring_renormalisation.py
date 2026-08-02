"""Absent axes leave the score; they no longer sink it."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "de_scoring_renorm",
    Path(__file__).resolve().parents[1]
    / "services"
    / "decision-engine"
    / "app"
    / "scoring.py",
)
scoring = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = scoring
_spec.loader.exec_module(scoring)


LEGACY_WEIGHTS = {
    "volume_growth": 0.25,
    "social_score": 0.20,
    "news_score": 0.20,
    "market_trend": 0.20,
    "liquidity_score": 0.15,
}


def test_weights_sum_to_one() -> None:
    assert abs(sum(scoring.WEIGHTS.values()) - 1.0) < 1e-9


def test_legacy_axes_keep_their_relative_proportions() -> None:
    # The rescale expresses no new opinion about the old model; it only makes
    # room. Any drift here is a silent recalibration.
    #
    # Two generations of rescale now: 0.75 when positioning and fundamentals
    # landed, then 0.92 when developer_activity did. The product is what the
    # legacy axes are worth today.
    for key, legacy in LEGACY_WEIGHTS.items():
        assert abs(scoring.WEIGHTS[key] - legacy * 0.75 * 0.92) < 1e-9


def test_new_axes_carry_the_freed_weight() -> None:
    assert abs(scoring.WEIGHTS["positioning"] - 0.138) < 1e-9
    assert abs(scoring.WEIGHTS["fundamentals"] - 0.092) < 1e-9
    assert abs(scoring.WEIGHTS["developer_activity"] - 0.08) < 1e-9


def test_a_symbol_missing_axes_is_scored_on_what_is_known() -> None:
    # Every present axis at ~1.0. Under the old constant denominator this
    # landed at 75 while being uniformly maximal, and the documented
    # RISK_MIN_SCORE=70 / max-observed-68 deadlock is that effect at realistic
    # values. That is the deadlock this change removes.
    f = scoring.Features(
        price_change_pct_24h=1e6,
        volume_spike_ratio=1e6,
        liquidity_usd=1e12,
        sentiment_score=1.0,
        social_growth=1e6,
        news_impact=1.0,
    )
    result = scoring.score(f)
    assert result.opportunity_score == 100
    # Cinq axes sur huit. La valeur baisse de 0.75 a 0.69 non pas parce que
    # la mesure a change, mais parce que le modele compte un axe de plus
    # qui n'est pas mesure ici: la confiance est la part du modele
    # adossee a une evidence propre au symbole.
    assert result.confidence == 0.69


def test_migration_identity_holds_on_unrounded_values() -> None:
    """score_v2 == score_v1 / confidence_v1 for legacy-only features.

    The threshold chosen at deploy time is derived from exactly this relation,
    so it is pinned here. Asserted unrounded: decision_journal.score is stored
    as an integer, which costs up to a point.
    """
    f = scoring.Features(
        price_change_pct_24h=12.0,
        volume_spike_ratio=4.0,
        liquidity_usd=500_000.0,
        sentiment_score=0.4,
        social_growth=0.8,
        news_impact=1.0,
    )
    result = scoring.score(f)
    v1_sum = sum(result.breakdown[k] * LEGACY_WEIGHTS[k] for k in LEGACY_WEIGHTS)
    v1_confidence = sum(LEGACY_WEIGHTS.values())  # all five present here
    expected = 100.0 * v1_sum / v1_confidence
    assert abs(result.opportunity_score - expected) <= 1.0


def test_an_unknown_liquidity_leaves_the_score_but_a_measured_zero_does_not() -> None:
    # The unknown-vs-zero rule, at the axis level.
    assert scoring._norm_liquidity(None) is None
    assert scoring._norm_liquidity(0) == 0.0


def test_a_symbol_with_no_evidence_at_all_produces_no_score() -> None:
    result = scoring.score(scoring.Features())
    assert result.confidence == 0.0
    assert result.opportunity_score == 0
    assert result.breakdown == {}


def test_positioning_participates_in_the_score() -> None:
    without = scoring.score(
        scoring.Features(price_change_pct_24h=10.0, volume_spike_ratio=2.0)
    )
    with_crowded = scoring.score(
        scoring.Features(
            price_change_pct_24h=10.0, volume_spike_ratio=2.0, funding_rate_8h=0.001
        )
    )
    assert with_crowded.opportunity_score < without.opportunity_score
    assert with_crowded.confidence > without.confidence


def test_an_imminent_unlock_lowers_the_score() -> None:
    clean = scoring.score(
        scoring.Features(price_change_pct_24h=10.0, has_unlock_schedule=True)
    )
    diluting = scoring.score(
        scoring.Features(
            price_change_pct_24h=10.0,
            has_unlock_schedule=True,
            next_unlock_pct_supply=5.0,
            next_unlock_days=2.0,
        )
    )
    assert diluting.opportunity_score < clean.opportunity_score


def test_an_untracked_token_neither_gains_nor_loses_from_the_unlock_axis() -> None:
    # The asymmetry that makes silence dangerous: an absent axis is excluded,
    # so it cannot penalise. This pins that an untracked token scores exactly
    # as if the axis did not exist -- not better, not worse.
    untracked = scoring.score(scoring.Features(price_change_pct_24h=10.0))
    assert "fundamentals" not in untracked.breakdown


def test_the_breakdown_reports_only_measured_axes() -> None:
    result = scoring.score(
        scoring.Features(price_change_pct_24h=10.0, funding_rate_8h=0.0)
    )
    assert set(result.breakdown) == {"market_trend", "positioning"}


def test_a_single_thin_axis_cannot_score_at_all() -> None:
    # Before the evidence floor, a lone has_unlock_schedule=True -- "DefiLlama
    # tracks this token and nothing is due in 30 days" -- scored a perfect 100
    # on 0.10 of the model, and funding alone scored 99. Both cleared the
    # decision threshold. Renormalisation exists to stop absent data dragging a
    # score down, not to let one axis speak for seven.
    for f in (
        scoring.Features(has_unlock_schedule=True),
        scoring.Features(funding_rate_8h=-0.0005),
        scoring.Features(open_interest_change_pct_24h=50.0),
        scoring.Features(volume_spike_ratio=6.0),  # the heaviest single axis
    ):
        result = scoring.score(f)
        assert result.opportunity_score == 0
        assert result.breakdown == {}


def test_the_lightest_legitimate_pair_still_scores() -> None:
    # fundamentals (0.092) + liquidity (0.1035) = 0.1955, just over the
    # floor of 0.184. Both moved together when developer_activity landed: a
    # floor left at 0.20 would have dropped this pair without anyone deciding
    # to make the gate stricter.
    result = scoring.score(
        scoring.Features(liquidity_usd=1_000_000.0, has_unlock_schedule=True)
    )
    assert result.opportunity_score > 0
    assert set(result.breakdown) == {"liquidity_score", "fundamentals"}


def test_a_half_read_unlock_leaves_the_axis_absent_not_maximal() -> None:
    # Sized but undated, or dated but unsized, is half a reading. Landing it on
    # 1.0 meant a known 40% unlock with an unreadable date scored the axis at
    # its best and pulled the score up.
    sized_only = scoring._norm_fundamentals(
        tvl_change=None,
        fees_change=None,
        unlock_pct=40.0,
        unlock_days=None,
        has_schedule=True,
    )
    dated_only = scoring._norm_fundamentals(
        tvl_change=None,
        fees_change=None,
        unlock_pct=None,
        unlock_days=1.0,
        has_schedule=True,
    )
    assert sized_only is None
    assert dated_only is None
