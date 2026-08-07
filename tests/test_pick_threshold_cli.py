"""Garde-fous du rendu CLI de scripts/pick_threshold.py.

Depuis que threshold_scan.py porte l'analyse, plus rien n'importe le script :
`_print_report`/`_print_axes`/`_print_days` avaient donc perdu toute
couverture, et la traduction aurait pu en silencieusement laisser tomber des
garanties de rendu. Ce fichier charge le script comme
`tests/test_pick_threshold_math.py` le chargeait avant l'extraction (meme
patron `spec_from_file_location`, cf.
git show 7a72ef7:tests/test_pick_threshold_math.py) et verifie sur un
`ThresholdReport` construit a la main -- pas rejoue -- les
trois garanties qui auraient pu se perdre en traduction :
  - le compte brut voyage a cote du pourcentage (DEFAUT 1) ;
  - la ligne du plancher de confiance apparait dans la proposition ;
  - un jour vide est flague dans le rendu par jour.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from service_modules import load_service_module

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pick_threshold.py"

ts = load_service_module("decision-engine", "threshold_scan")


def _load_pick_threshold():
    spec = importlib.util.spec_from_file_location("pick_threshold_script", _SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_pick_threshold = _load_pick_threshold()
_print_report = _pick_threshold._print_report


def _canned_report():
    """Un rapport sans refus, construit a la main -- pas issu d'un rejeu --
    pour isoler uniquement le rendu (`_print_report` et ses sous-fonctions)."""
    return ts.ThresholdReport(
        window={
            "days": 7,
            "min_time": "2026-08-01T00:00:00+00:00",
            "total": 1_281_511,
            "no_evidence": 0,
            "by_day": {"2026-08-01": 0, "2026-08-02": 1_281_511},
        },
        axes=[
            {
                "key": "positioning",
                "weight": 0.09,
                "seen": 1,
                "pct": 0.0,
                "mute": False,
            },
        ],
        refusal=None,
        distribution={
            "scored": 1_281_511,
            "p50": 50,
            "p90": 80,
            "p99": 95,
            "p999": 99,
            "max": 100,
        },
        proposal={
            "threshold": 80,
            "target_per_day": 200,
            "actual_per_day": 190.0,
            "distinct_symbols": 12.0,
            "passing_pct": 75.0,
            "confidence_passing_per_day": 140.0,
            "risk_min_score_effect": [],
        },
        warnings=[
            {
                "code": "EMPTY_DAY",
                "day": "2026-08-01",
                "text": "2026-08-01 : aucune ligne (jour vide)",
            },
        ],
        sonnet={"n": 0, "p50": None, "max": None, "warning": None},
        risk_min_confidence=0.3795,
        min_presence_pct=1.0,
    )


def test_le_compte_brut_est_visible_a_cote_du_pourcentage(capsys) -> None:
    """DEFAUT 1 : "1 ligne sur 1 281 511" s'affiche "0.0%" -- sans le compte
    brut a cote, un axe presque mort serait indiscernable d'un axe sain."""
    _print_report(_canned_report())
    out = capsys.readouterr().out
    assert "(1 lignes)" in out


def test_la_ligne_du_plancher_de_confiance_apparait_dans_la_proposition(
    capsys,
) -> None:
    _print_report(_canned_report())
    out = capsys.readouterr().out
    assert "dont passant le plancher de confiance (RISK_MIN_CONFIDENCE=" in out


def test_un_jour_vide_est_flague_dans_le_rendu_par_jour(capsys) -> None:
    _print_report(_canned_report())
    out = capsys.readouterr().out
    assert "<-- VIDE" in out


def test_parite_cli_service_meme_verdict_sur_meme_scan(capsys) -> None:
    """Parite CLI/service exigee par le spec (§Tests) : le CLI et le rapport
    structure doivent decrire le meme verdict sur le meme `Scan`. Aucun test
    ne le verifiait -- `_canned_report()` a `refusal=None`, si bien que la
    branche de refus de `_print_report` ET son code de sortie documente (1,
    cf. le docstring de `scripts/pick_threshold.py`) n'avaient jamais tourne.

    Construit un `Scan` synthetique avec un axe muet (`positioning` absent de
    la fenetre, les sept autres a 100%), le fait passer par `analyze()` --
    la meme fonction que `threshold_job.py` appelle pour le rapport persiste
    -- puis verifie que `_print_report` sur ce meme rapport rend le meme
    verdict : code de sortie 1, et le detail du refus verbatim dans la
    sortie."""
    scan = ts.Scan(
        total=1_000,
        no_evidence=0,
        regime_seen=1_000,
        presence=Counter(
            {axis: 1_000 for axis in ts.AXIS_PROBE if axis != "positioning"}
        ),
        since=datetime(2026, 8, 1, tzinfo=UTC),
        min_time=datetime(2026, 8, 1, tzinfo=UTC),
        min_time_with_regime=datetime(2026, 8, 1, tzinfo=UTC),
        by_day=Counter({"2026-08-01": 1_000}),
    )
    report = ts.analyze(
        scan, days=1, target_per_day=200, now=datetime(2026, 8, 1, tzinfo=UTC)
    )
    assert report.refusal is not None
    assert report.refusal["code"] == "MUTE_AXES"

    exit_code = _print_report(report)
    out = capsys.readouterr().out

    assert exit_code == 1
    assert report.refusal["detail"] in out
