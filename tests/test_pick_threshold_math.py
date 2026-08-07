"""Tests des fonctions pures de decision-engine/app/threshold_scan.py.

Historiquement ces fonctions vivaient dans `scripts/pick_threshold.py` et ce
fichier les testait via `_report(scan, args)` + `capsys` (le script imprimait
directement). Le refactor qui a extrait l'analyse en module pur
(`threshold_scan.py::analyze`) les a deplacees ; ce fichier suit le meme
mouvement et teste desormais la structure retournee par `analyze()` plutot que
du texte imprime -- `scripts/pick_threshold.py` n'est plus qu'un formateur
au-dessus des memes fonctions, verifie separement par
`tests/test_threshold_scan.py`.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import pytest
from service_modules import load_service_module

ts = load_service_module("decision-engine", "threshold_scan")

_threshold_for = ts._threshold_for
_percentile = ts._percentile
_counts_ge = ts._counts_ge
_bounded_passing = ts._bounded_passing
_check_axis_probe_matches_weights = ts._check_axis_probe_matches_weights
Scan = ts.Scan
AXIS_PROBE = ts.AXIS_PROBE
WEIGHTS = ts.WEIGHTS
analyze = ts.analyze


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


def test_une_ligne_sur_un_million_declenche_quand_meme_le_refus():
    """`pct == 0.0` ratait 1/1 281 511 (7,8e-5 %, arrondi affiche a "0.0%"),
    donc le garde ne se declenchait pas. MIN_PRESENCE_PCT doit refuser meme
    quand le pourcentage affiche a l'ecran est identique a un vrai zero."""
    total = 1_281_511
    scan = _full_scan(total)
    scan.presence["positioning"] = 1  # une seule ligne sur 1.28M

    report = analyze(scan, days=7, target_per_day=None)

    assert report.refusal is not None
    assert report.refusal["code"] == "MUTE_AXES"
    assert "positioning" in report.refusal["title"]
    # Le compte brut doit etre visible a cote du pourcentage : sans lui,
    # "presence=  0.0%" serait indiscernable d'une vraie absence.
    positioning = next(a for a in report.axes if a["key"] == "positioning")
    assert positioning["seen"] == 1
    assert positioning["mute"] is True


def test_un_axe_vraiment_absent_est_toujours_refuse():
    total = 100
    scan = _full_scan(total)
    scan.presence["positioning"] = 0

    report = analyze(scan, days=7, target_per_day=None)

    assert report.refusal is not None
    assert "positioning" in report.refusal["title"]
    positioning = next(a for a in report.axes if a["key"] == "positioning")
    assert positioning["seen"] == 0


def test_un_axe_au_dessus_du_plancher_ne_declenche_pas_le_refus():
    total = 1000
    scan = _full_scan(total)
    scan.presence["positioning"] = 15  # 1.5% > MIN_PRESENCE_PCT=1.0
    scan.min_time = datetime(2026, 7, 27, tzinfo=UTC)
    scan.min_time_with_regime = scan.min_time

    report = analyze(scan, days=7, target_per_day=None)

    assert report.refusal is None


# --- DEFAUT 2 : le rapport doit aussi modeliser le plancher de confiance ---
# --- du risk-engine (RISK_MIN_CONFIDENCE), pas seulement RISK_MIN_SCORE ----


def test_risk_min_confidence_lit_l_env_avec_le_defaut_du_compose_vps():
    """docker-compose.vps.yml:398 fixe RISK_MIN_CONFIDENCE=0.3795 ; le defaut
    code dans RiskConfig.min_confidence (0.506) est perime. En l'absence de
    la variable d'environnement, le module doit lire celui qui tourne."""
    assert pytest.approx(0.3795) == ts._RISK_MIN_CONFIDENCE


def test_colonne_plancher_de_confiance_apparait_dans_la_proposition():
    total = 1000
    scan = _full_scan(total)
    scan.score_counts = Counter({90: 10, 80: 10, 70: 10})
    # 70 perd tout a la confiance (typiquement l'axe news retombe sur
    # market_sentiment) ; 90 et 80 en gardent une partie.
    scan.confidence_pass_counts = Counter({90: 4, 80: 2})
    scan.min_time = datetime(2026, 7, 27, tzinfo=UTC)
    scan.min_time_with_regime = scan.min_time

    report = analyze(scan, days=1, target_per_day=100)

    assert report.refusal is None
    assert report.proposal is not None
    # threshold=0 ici (cible tres large) -> le debit reel couvre tout, dont
    # confidence_pass_counts totalise 4+2=6 lignes.
    assert report.proposal["confidence_passing_per_day"] == 6.0


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


def test_risk_min_score_sous_le_seuil_n_affiche_plus_un_nombre_sans_referent():
    """Reproduit l'exemple de la revue : seuil calibre a 86 (le plus proche de
    85 sur cet histogramme), RISK_MIN_SCORE=70 (le defaut actuel) est sous le
    seuil -- son compte doit etre identique a celui du seuil, pas celui de la
    population brute >=70."""
    total = 10_000
    scan = _full_scan(total)
    scan.score_counts = Counter(
        {100: 5, 95: 5, 90: 10, 85: 10, 80: 20, 75: 20, 70: 20, 60: 20}
    )
    scan.min_time = datetime(2026, 7, 27, tzinfo=UTC)
    scan.min_time_with_regime = scan.min_time

    report = analyze(scan, days=1, target_per_day=25)

    assert report.refusal is None
    assert report.proposal["threshold"] == 86
    rows = {row["value"]: row for row in report.proposal["risk_min_score_effect"]}
    # Seul le compte de lignes doit coincider (le marqueur "defaut actuel"
    # ne s'applique qu'a la ligne 70, ce qui est attendu).
    assert rows[70]["lines"] == 20
    assert rows[86]["lines"] == 20
    assert rows[70]["is_default"] is True
    assert rows[86]["is_default"] is False


# --- DEFAUT 4 : un regime partiellement couvert doit etre refuse, pas -----
# --- seulement un regime totalement absent ---------------------------------


def test_regime_partiellement_couvert_est_refuse():
    """Deploiement mardi, fenetre lancee jeudi avec --days 7 : le regime est
    present sur une partie de la fenetre (regime_seen > 0) mais les jours
    anterieurs au deploiement n'en ont pas -- le vieux garde tout-ou-rien
    (`regime_seen == 0`) laissait passer ce cas."""
    total = 100
    scan = _full_scan(total)
    scan.min_time = datetime(2026, 7, 27, tzinfo=UTC)
    scan.min_time_with_regime = datetime(2026, 7, 29, tzinfo=UTC)  # +2 jours

    report = analyze(
        scan, days=7, target_per_day=None, now=datetime(2026, 8, 3, tzinfo=UTC)
    )

    assert report.refusal is not None
    assert report.refusal["code"] == "REGIME_GAP"
    assert "DEBUT" in report.refusal["detail"]
    assert "--days" in report.refusal["detail"]


def test_regime_couvrant_toute_la_fenetre_ne_declenche_pas_le_refus():
    total = 100
    scan = _full_scan(total)
    t0 = datetime(2026, 7, 27, tzinfo=UTC)
    scan.min_time = t0
    scan.min_time_with_regime = t0  # regime present depuis le tout debut

    report = analyze(
        scan, days=7, target_per_day=None, now=datetime(2026, 8, 3, tzinfo=UTC)
    )

    assert report.refusal is None


# --- DEFAUT 5 : AXIS_PROBE est une 4e copie de la liste d'axes, non couverte


def test_axis_probe_et_weights_reellement_alignes_a_l_import():
    """Verifie l'invariant reellement utilise par le module (pas une copie du
    test) : si ce test echoue, c'est que quelqu'un a ajoute/retire un axe d'un
    cote sans l'autre, et l'import du module aurait deja leve."""
    _check_axis_probe_matches_weights(AXIS_PROBE, WEIGHTS)
    assert set(AXIS_PROBE) == set(WEIGHTS)


def test_axis_probe_divergent_de_weights_leve_bruyamment():
    probe = {"volume_growth": "volume_spike_ratio"}
    weights = {"volume_growth": 0.1875, "positioning": 0.09}
    with pytest.raises(RuntimeError, match="AXIS_PROBE"):
        _check_axis_probe_matches_weights(probe, weights)


# --- DEFAUT 6 : la fenetre n'est pas homogene, et le rapport doit le montrer


def test_lignes_par_jour_signale_jour_vide_et_ecart_fort():
    total = 60_348 + 0 + 45_423
    scan = _full_scan(total)
    since = datetime(2026, 7, 27, tzinfo=UTC)
    scan.since = since
    scan.min_time = since
    scan.min_time_with_regime = since
    scan.by_day["2026-07-27"] = 60_348
    # 2026-07-28 volontairement absent du Counter : jour entierement vide.
    scan.by_day["2026-07-29"] = 45_423

    # Fige "aujourd'hui" a la fin de la fenetre demandee (2 jours depuis
    # `since`) : l'analyse parcourt since.date() .. now.date().
    report = analyze(
        scan, days=2, target_per_day=None, now=datetime(2026, 7, 29, tzinfo=UTC)
    )

    assert report.refusal is None
    assert report.window["by_day"]["2026-07-28"] == 0
    assert "2026-07-27" in report.window["by_day"]
    assert "2026-07-29" in report.window["by_day"]
    assert any(
        w["code"] == "EMPTY_DAY" and w["day"] == "2026-07-28" for w in report.warnings
    )
