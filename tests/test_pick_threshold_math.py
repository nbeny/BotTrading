"""Tests des deux fonctions pures de scripts/pick_threshold.py.

Le script fait des `sys.path.insert` et charge des modules de service a
l'import (`load_service_module("decision-engine", ...)`). On le charge donc
ici via `importlib.util.spec_from_file_location`, exactement comme
`tests/service_modules.py` charge les packages `app` des services -- ce
chargement execute le module une seule fois (import Python normal), donc les
imports lourds de scoring/features_map ne se reproduisent pas a chaque test.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pick_threshold.py"


def _load_pick_threshold():
    spec = importlib.util.spec_from_file_location("pick_threshold_script", _SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_pick_threshold = _load_pick_threshold()
_threshold_for = _pick_threshold._threshold_for
_percentile = _pick_threshold._percentile


def test_threshold_for_simple_histogram_calcule_a_la_main():
    # 1 jour, cible 2/jour -> budget 2. Cumul depuis 100 :
    # value=100 -> running=1 (<=2), value=90 -> running=3 (>2) -> seuil=91.
    counts = Counter({100: 1, 90: 2, 80: 3, 70: 4})
    assert _threshold_for(counts, target_per_day=2, days=1) == 91


def test_threshold_for_cible_depasse_le_volume_total_tombe_a_zero():
    counts = Counter({100: 1, 90: 2, 80: 3})
    # Budget largement superieur a la somme des comptes : aucun seuil ne
    # dépasse jamais la cible, donc tout passe -> seuil 0.
    assert _threshold_for(counts, target_per_day=1_000, days=1) == 0


def test_threshold_for_meme_le_score_max_depasse_le_budget():
    # Cible tres basse : le score maximal (100) a lui seul depasse deja le
    # budget. Aucun seuil dans [0, 100] ne respecte la cible ; le plus severe
    # possible est 100 (rien de plus haut n'existe sur l'echelle), jamais 101.
    counts = Counter({100: 5, 90: 5})
    result = _threshold_for(counts, target_per_day=0, days=1)
    assert result == 100
    assert 0 <= result <= 100


def test_percentile_histogramme_vide():
    assert _percentile(Counter(), 50) == 0
    assert _percentile(Counter(), 99.9) == 0


def test_percentile_mediane_connue():
    # Quatre valeurs a poids egal : la mediane (50e centile) au sens de cet
    # algorithme (cumul croissant, premier seau qui atteint la moitie du
    # total) est 20 -- pas d'interpolation entre 20 et 30.
    counts = Counter({10: 1, 20: 1, 30: 1, 40: 1})
    assert _percentile(counts, 50) == 20


def test_percentile_p90_et_max():
    counts = Counter({10: 90, 100: 10})
    assert _percentile(counts, 90) == 10
    assert _percentile(counts, 99) == 100
