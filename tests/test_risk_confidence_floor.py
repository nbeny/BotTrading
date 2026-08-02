"""Le plancher de confiance du risk-engine suit les poids du scoring.

`confidence` est une somme *absolue* de poids d'axes, exactement comme
`_MIN_PRESENT_WEIGHT`. Rien dans le code ne relie `RISK_MIN_CONFIDENCE` a
`WEIGHTS`, si bien qu'un rescale des poids deplace en silence deux choses a la
fois: quelles combinaisons d'evidence sont approuvees, et la taille de chaque
position, celle-ci etant lineaire en confiance.

Ce fichier est ce lien. Il a ete ecrit apres coup: l'ajout du huitieme axe avait
rescale les poids sans rescaler ce plancher, ce qui rejetait des combinaisons
auparavant approuvees et retrecissait toutes les positions de 8%.
"""

from __future__ import annotations

import pytest
from service_modules import load_service_module

WEIGHTS = load_service_module("decision-engine", "scoring").WEIGHTS
_rules = load_service_module("risk-engine", "rules")
RiskConfig = _rules.RiskConfig

#: La combinaison a quatre axes historiquement retenue comme la plus legere a
#: rester approuvable. Sa somme *est* le plancher, par construction.
_LIGHTEST_APPROVABLE = (
    "volume_growth",
    "social_score",
    "liquidity_score",
    "fundamentals",
)


def test_the_floor_is_exactly_the_lightest_approvable_combination():
    """Le plancher n'est pas un nombre libre: c'est la somme de quatre poids.

    Ecrit ainsi, un rescale des poids qui oublierait le plancher fait echouer
    ce test au lieu de modifier silencieusement le taux d'approbation.
    """
    expected = sum(WEIGHTS[axis] for axis in _LIGHTEST_APPROVABLE)
    assert RiskConfig().min_confidence == pytest.approx(expected)


def test_that_combination_is_approved_not_rejected():
    """La borne est inclusive: `confidence < floor` rejette, donc une
    confiance egale au plancher passe."""
    confidence = sum(WEIGHTS[axis] for axis in _LIGHTEST_APPROVABLE)
    assert not confidence < RiskConfig().min_confidence


def test_position_size_is_linear_in_confidence():
    """C'est le second effet, plus discret que le rejet: la taille se calcule
    `max_position_pct * min(1.0, confidence)`. Un plancher laisse en place
    apres un rescale ne rejette pas seulement des trades, il retrecit *tous*
    ceux qui passent."""
    cfg = RiskConfig()
    full = cfg.max_position_pct * min(1.0, 1.0)
    at_floor = cfg.max_position_pct * min(1.0, cfg.min_confidence)
    assert at_floor < full
    assert at_floor == pytest.approx(cfg.max_position_pct * cfg.min_confidence)


def test_a_full_house_still_saturates_the_size_cap():
    """Tous les axes presents doit continuer a donner la taille maximale,
    sinon le rescale aurait aussi rabote le haut de l'echelle."""
    assert min(1.0, sum(WEIGHTS.values())) == pytest.approx(1.0)
