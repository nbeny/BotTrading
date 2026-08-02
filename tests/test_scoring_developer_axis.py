"""Le huitieme axe: momentum de developpement relatif au projet.

Relatif, et non absolu: Bitcoin a 1 200 contributeurs et un token recent en a
quatre. Un axe bati sur des volumes bruts classerait mecaniquement les grandes
capitalisations en tete et ne dirait rien que le pipeline ne sache deja -- il
dupliquerait la capitalisation sous un autre nom.
"""

from __future__ import annotations

import pytest
from service_modules import load_service_module

_scoring = load_service_module("decision-engine", "scoring")
WEIGHTS = _scoring.WEIGHTS
Features = _scoring.Features
_norm_developer_activity = _scoring._norm_developer_activity
score = _scoring.score


def _norm(**kw):
    base = {
        "commit_ratio": None,
        "pr_ratio": None,
        "days_since_push": None,
        "star_growth": None,
        "all_archived": False,
    }
    base.update(kw)
    return _norm_developer_activity(**base)


def _scorable(**kw):
    """Assez de features pour passer _MIN_PRESENT_WEIGHT."""
    base = {
        "price_change_pct_24h": 5.0,
        "volume_spike_ratio": 2.0,
        "liquidity_usd": 1e6,
        "sentiment_score": 0.5,
    }
    base.update(kw)
    return Features(**base)


def test_weights_still_sum_to_one():
    """_MIN_PRESENT_WEIGHT = 0.20 est un seuil sur cette somme: la laisser
    deriver a 1.08 changerait le sens de la porte sans que rien ne le
    signale."""
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_developer_activity_has_weight():
    assert WEIGHTS["developer_activity"] == pytest.approx(0.08)


def test_the_seven_existing_axes_kept_their_relative_order():
    """La rebalance est un rescale uniforme, pas une reponderation: les sept
    axes existants gardent leurs proportions entre eux."""
    assert WEIGHTS["volume_growth"] > WEIGHTS["social_score"]
    assert WEIGHTS["social_score"] == pytest.approx(WEIGHTS["news_score"])
    assert WEIGHTS["news_score"] == pytest.approx(WEIGHTS["market_trend"])
    assert WEIGHTS["market_trend"] == pytest.approx(WEIGHTS["positioning"])
    assert WEIGHTS["positioning"] > WEIGHTS["liquidity_score"]
    assert WEIGHTS["liquidity_score"] > WEIGHTS["fundamentals"]


def test_habitual_pace_scores_mid():
    assert _norm(commit_ratio=1.0) == pytest.approx(0.5)


def test_tripled_activity_scores_max():
    assert _norm(commit_ratio=3.0) == pytest.approx(1.0)


def test_collapsed_activity_scores_min():
    assert _norm(commit_ratio=1 / 3) == pytest.approx(0.0)


def test_the_scale_is_symmetric_around_the_habitual_pace():
    """Doubler et etre divise par deux doivent s'ecarter autant de 0.5 --
    c'est ce que le logarithme achete, et une echelle lineaire ne le
    donnerait pas."""
    up = _norm(commit_ratio=2.0) - 0.5
    down = 0.5 - _norm(commit_ratio=0.5)
    assert up == pytest.approx(down)


def test_stopped_project_is_a_measured_zero():
    """Ratio 0 avec une baseline reelle: le projet s'est arrete, c'est une
    mesure. Le collector rend None -- et non 0 -- quand la baseline elle-meme
    manque, donc les deux cas ne se confondent pas ici."""
    assert _norm(commit_ratio=0.0) == 0.0


def test_absent_everything_is_none_not_zero():
    value = _norm()
    assert value is None
    assert value != 0.0


def test_all_archived_is_zero_not_none():
    """On a regarde, tout est archive: une observation, pas une absence."""
    assert _norm(all_archived=True) == 0.0


def test_all_archived_wins_over_absent_submeasures():
    """Le court-circuit doit primer meme si aucun sous-signal n'est fourni."""
    assert _norm(all_archived=True, commit_ratio=None) == 0.0


def test_freshness_is_full_within_a_week_and_nil_past_three_months():
    assert _norm(days_since_push=0) == pytest.approx(1.0)
    assert _norm(days_since_push=7) == pytest.approx(1.0)
    assert _norm(days_since_push=90) == pytest.approx(0.0)
    assert _norm(days_since_push=200) == pytest.approx(0.0)


def test_submeasures_are_averaged_over_present_weight_only():
    """Un sous-signal absent est exclu, pas compte a zero -- meme regle que
    pour les axes eux-memes, un cran plus bas."""
    only_commit = _norm(commit_ratio=1.0)
    with_fresh = _norm(commit_ratio=1.0, days_since_push=0)
    assert only_commit == pytest.approx(0.5)
    # 0.40*0.5 + 0.25*1.0 renormalise sur 0.65
    assert with_fresh == pytest.approx((0.40 * 0.5 + 0.25 * 1.0) / 0.65)


def test_absent_axis_is_excluded_from_breakdown():
    assert "developer_activity" not in score(_scorable()).breakdown


def test_present_axis_appears_in_breakdown():
    assert "developer_activity" in score(_scorable(commit_ratio_4w=2.0)).breakdown


def test_a_dead_project_lowers_the_score():
    """L'axe doit peser dans le bon sens: tout archive < rythme habituel."""
    alive = score(_scorable(commit_ratio_4w=1.0)).opportunity_score
    dead = score(_scorable(all_repos_archived=True)).opportunity_score
    assert dead < alive


def test_the_axis_is_symbol_specific_so_it_counts_in_confidence():
    """Contrairement au regime de marche, identique pour tous les symboles."""
    without = score(_scorable()).confidence
    with_dev = score(_scorable(commit_ratio_4w=2.0)).confidence
    assert with_dev > without
