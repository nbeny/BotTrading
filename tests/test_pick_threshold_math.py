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
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

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
_counts_ge = _pick_threshold._counts_ge
_bounded_passing = _pick_threshold._bounded_passing
_check_axis_probe_matches_weights = _pick_threshold._check_axis_probe_matches_weights
Scan = _pick_threshold.Scan
AXIS_PROBE = _pick_threshold.AXIS_PROBE
WEIGHTS = _pick_threshold.WEIGHTS
_report = _pick_threshold._report


def _full_scan(total: int, *, regime_seen: int | None = None) -> Scan:
    """Scan avec chaque axe present sur toutes les lignes, pour isoler un seul
    garde a la fois dans les tests qui suivent (sans quoi le garde DEFAUT 1 se
    declencherait sur des axes a presence 0 par defaut)."""
    scan = Scan(total=total, regime_seen=total if regime_seen is None else regime_seen)
    for axis in AXIS_PROBE:
        scan.presence[axis] = total
    return scan


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


# --- DEFAUT 1 : une ligne sur un million ne doit pas passer pour zero -----


def test_une_ligne_sur_un_million_declenche_quand_meme_le_refus(capsys):
    """`pct == 0.0` ratait 1/1 281 511 (7,8e-5 %, arrondi affiche a "0.0%"),
    donc le garde ne se declenchait pas. MIN_PRESENCE_PCT doit refuser meme
    quand le pourcentage affiche a l'ecran est identique a un vrai zero."""
    total = 1_281_511
    scan = _full_scan(total)
    scan.presence["positioning"] = 1  # une seule ligne sur 1.28M
    args = SimpleNamespace(days=7, decisions_per_day=None)

    result = _report(scan, args)
    out = capsys.readouterr().out

    assert result == 1
    assert "REFUS" in out
    assert "positioning" in out
    # Le compte brut doit etre visible a cote du pourcentage : sans lui,
    # "presence=  0.0%" serait indiscernable d'une vraie absence.
    assert "(1 lignes)" in out


def test_un_axe_vraiment_absent_est_toujours_refuse(capsys):
    total = 100
    scan = _full_scan(total)
    scan.presence["positioning"] = 0
    args = SimpleNamespace(days=7, decisions_per_day=None)

    result = _report(scan, args)
    out = capsys.readouterr().out

    assert result == 1
    assert "positioning" in out
    assert "(0 lignes)" in out


def test_un_axe_au_dessus_du_plancher_ne_declenche_pas_le_refus(capsys):
    total = 1000
    scan = _full_scan(total)
    scan.presence["positioning"] = 15  # 1.5% > MIN_PRESENCE_PCT=1.0
    args = SimpleNamespace(days=7, decisions_per_day=None)

    result = _report(scan, args)
    out = capsys.readouterr().out

    assert result == 0
    assert "REFUS" not in out


# --- DEFAUT 2 : le rapport doit aussi modeliser le plancher de confiance ---
# --- du risk-engine (RISK_MIN_CONFIDENCE), pas seulement RISK_MIN_SCORE ----


def test_risk_min_confidence_lit_l_env_avec_le_defaut_du_compose_vps():
    """docker-compose.vps.yml:398 fixe RISK_MIN_CONFIDENCE=0.3795 ; le defaut
    code dans RiskConfig.min_confidence (0.506) est perime. En l'absence de
    la variable d'environnement, le script doit lire celui qui tourne."""
    assert pytest.approx(0.3795) == _pick_threshold._RISK_MIN_CONFIDENCE


def test_colonne_plancher_de_confiance_apparait_dans_les_sections_de_debit(capsys):
    total = 1000
    scan = _full_scan(total)
    scan.score_counts = Counter({90: 10, 80: 10, 70: 10})
    # 70 perd tout a la confiance (typiquement l'axe news retombe sur
    # market_sentiment) ; 90 et 80 en gardent une partie.
    scan.confidence_pass_counts = Counter({90: 4, 80: 2})
    args = SimpleNamespace(days=1, decisions_per_day=100)

    result = _report(scan, args)
    out = capsys.readouterr().out

    assert result == 0
    assert "dont passant le plancher de confiance" in out
    # threshold=0 ici (cible tres large) -> le debit reel couvre tout, dont
    # confidence_pass_counts totalise 4+2=6 lignes.
    assert "dont passant le plancher de confiance (RISK_MIN_CONFIDENCE=" in out


# --- DEFAUT 3 : la table RISK_MIN_SCORE ne doit compter que des lignes qui --
# --- existeront reellement une fois DECISION_THRESHOLD applique ------------


def test_bounded_passing_borne_les_valeurs_sous_le_seuil():
    counts = Counter({100: 5, 90: 10, 80: 20, 70: 20, 60: 20})
    threshold = 85
    # Sous le seuil calibre, la population ne peut pas depasser celle du
    # seuil lui-meme : le decision-engine n'emet rien en dessous.
    assert _bounded_passing(counts, 60, threshold) == _counts_ge(counts, threshold)
    assert _bounded_passing(counts, 70, threshold) == _counts_ge(counts, threshold)
    assert _bounded_passing(counts, 80, threshold) == _counts_ge(counts, threshold)
    # Au seuil ou au-dessus, le bornage ne change rien : max(v, threshold) == v.
    assert _bounded_passing(counts, 85, threshold) == _counts_ge(counts, 85)
    assert _bounded_passing(counts, 95, threshold) == _counts_ge(counts, 95)


def test_risk_min_score_sous_le_seuil_n_affiche_plus_un_nombre_sans_referent(capsys):
    """Reproduit l'exemple de la revue : seuil calibre a 86 (le plus proche de
    85 sur cet histogramme), RISK_MIN_SCORE=70 (le defaut actuel) est sous le
    seuil -- son compte doit etre identique a celui du seuil, pas celui de la
    population brute >=70."""
    total = 10_000
    scan = _full_scan(total)
    scan.score_counts = Counter(
        {100: 5, 95: 5, 90: 10, 85: 10, 80: 20, 75: 20, 70: 20, 60: 20}
    )
    args = SimpleNamespace(days=1, decisions_per_day=25)

    result = _report(scan, args)
    out = capsys.readouterr().out

    assert result == 0
    assert "DECISION_THRESHOLD propose : 86" in out
    lines = {ln.strip() for ln in out.splitlines() if "RISK_MIN_SCORE=" in ln}
    line_70 = next(ln for ln in lines if "RISK_MIN_SCORE= 70" in ln)
    line_86 = next(ln for ln in lines if "RISK_MIN_SCORE= 86" in ln)
    # Seul le compte de lignes doit coincider (le marqueur "<-- defaut
    # actuel" ne s'affiche que sur la ligne 70, ce qui est attendu).
    assert "20 lignes" in line_70
    assert "20 lignes" in line_86


# --- DEFAUT 4 : un regime partiellement couvert doit etre refuse, pas -----
# --- seulement un regime totalement absent ---------------------------------


def test_regime_partiellement_couvert_est_refuse(capsys):
    """Deploiement mardi, fenetre lancee jeudi avec --days 7 : le regime est
    present sur une partie de la fenetre (regime_seen > 0) mais les jours
    anterieurs au deploiement n'en ont pas -- le vieux garde tout-ou-rien
    (`regime_seen == 0`) laissait passer ce cas."""
    total = 100
    scan = _full_scan(total)
    scan.min_time = datetime(2026, 7, 27, tzinfo=UTC)
    scan.min_time_with_regime = datetime(2026, 7, 29, tzinfo=UTC)  # +2 jours
    args = SimpleNamespace(days=7, decisions_per_day=None)

    result = _report(scan, args)
    out = capsys.readouterr().out

    assert result == 1
    assert "REFUS" in out
    assert "DEBUT" in out
    assert "--days" in out


def test_regime_couvrant_toute_la_fenetre_ne_declenche_pas_le_refus(capsys):
    total = 100
    scan = _full_scan(total)
    t0 = datetime(2026, 7, 27, tzinfo=UTC)
    scan.min_time = t0
    scan.min_time_with_regime = t0  # regime present depuis le tout debut
    args = SimpleNamespace(days=7, decisions_per_day=None)

    result = _report(scan, args)
    out = capsys.readouterr().out

    assert result == 0
    assert "REFUS" not in out


# --- DEFAUT 5 : AXIS_PROBE est une 4e copie de la liste d'axes, non couverte


def test_axis_probe_et_weights_reellement_alignes_a_l_import():
    """Verifie l'invariant reellement utilise par le script (pas une copie du
    test) : si ce test echoue, c'est que quelqu'un a ajoute/retire un axe d'un
    cote sans l'autre, et l'import du script aurait deja leve."""
    _check_axis_probe_matches_weights(AXIS_PROBE, WEIGHTS)
    assert set(AXIS_PROBE) == set(WEIGHTS)


def test_axis_probe_divergent_de_weights_leve_bruyamment():
    probe = {"volume_growth": "volume_spike_ratio"}
    weights = {"volume_growth": 0.1875, "positioning": 0.09}
    with pytest.raises(RuntimeError, match="AXIS_PROBE"):
        _check_axis_probe_matches_weights(probe, weights)


# --- DEFAUT 6 : la fenetre n'est pas homogene, et le rapport doit le montrer


def test_lignes_par_jour_signale_jour_vide_et_ecart_fort(capsys):
    total = 60_348 + 0 + 45_423
    scan = _full_scan(total)
    since = datetime(2026, 7, 27, tzinfo=UTC)
    scan.since = since
    scan.min_time = since
    scan.min_time_with_regime = since
    scan.by_day["2026-07-27"] = 60_348
    # 2026-07-28 volontairement absent du Counter : jour entierement vide.
    scan.by_day["2026-07-29"] = 45_423
    args = SimpleNamespace(days=2, decisions_per_day=None)

    # Fige "aujourd'hui" implicitement via since + args.days : le rapport
    # parcourt since.date() .. date du jour, donc on ne fixe ici que le debut
    # et on verifie juste la presence des marqueurs, pas la borne de fin.
    result = _report(scan, args)
    out = capsys.readouterr().out

    assert result == 0
    assert "lignes par jour :" in out
    assert "2026-07-28 : 0  <-- VIDE" in out
    assert "2026-07-27" in out and "2026-07-29" in out
