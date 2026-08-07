#!/usr/bin/env python
"""Choisit DECISION_THRESHOLD par rejeu du journal de decision.

Ce script est desormais un pur formateur texte : toute l'analyse (le scan et
la logique de decision -- presence par axe, refus, distribution, proposition
de seuil) vit dans ``services/decision-engine/app/threshold_scan.py``, seul
service autorise a importer ``scoring.py``. Le meme module alimente aussi le
service periodique (``threshold_job.py``) qui persiste le rapport pour le
terminal web. Ce script ouvre sa propre connexion, appelle ``scan_window``
puis ``analyze``, et imprime le resultat -- l'interface CLI (``--days``,
``--decisions-per-day``) et le code de sortie (1 si refus, 0 sinon) ne
changent pas.

Usage :
    python scripts/pick_threshold.py --days 7 --decisions-per-day 200
    python scripts/pick_threshold.py --days 7            # rapport seul
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

# Ce script touche deux services, et chacun embarque un package nomme `app`.
# Un double `sys.path.insert` ne marche pas : le premier `import app` fige
# `sys.modules["app"]` sur le premier service charge, et l'import suivant
# chercherait `app.scoring` dedans. On reutilise donc le chargeur des tests,
# qui enregistre chaque service sous un alias distinct.
_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "tests"))
sys.path.insert(0, os.path.join(_ROOT, "libs", "cmi_common"))

from service_modules import load_service_module  # noqa: E402

from cmi_common.config import get_settings  # noqa: E402
from cmi_common.db import Database  # noqa: E402

ts = load_service_module("decision-engine", "threshold_scan")


def _print_axes(axes: list[dict[str, Any]]) -> None:
    print("presence par axe (part des lignes ou l'axe a ete lu) :")
    for axis in axes:
        # Le compte brut est imprime a cote du pourcentage : a 5 decimales
        # pres, "1 ligne sur 1 281 511" s'affiche "0.0%" (`:5.1f`) et un
        # operateur qui ne lirait que le pourcentage n'y verrait aucune
        # anomalie -- cf. threshold_scan.py::MIN_PRESENCE_PCT.
        marker = "  <-- MUET" if axis["mute"] else ""
        print(
            f"  {axis['key']:20s} poids={axis['weight']:.4f}  "
            f"presence={axis['pct']:5.1f}% ({axis['seen']} lignes){marker}"
        )


def _print_days(by_day: dict[str, int], warnings: list[str]) -> None:
    print("\nlignes par jour :")
    for day, n in by_day.items():
        flag = ""
        day_warning = next((w for w in warnings if w.startswith(f"{day} :")), None)
        if day_warning is not None:
            flag = (
                "  <-- VIDE"
                if "vide" in day_warning
                else "  <-- ecart fort a la mediane"
            )
        print(f"  {day} : {n}{flag}")


def _print_report(report: Any) -> int:
    # 1. Presence par axe.
    _print_axes(report.axes)

    # 2/3. Refus (MUTE_AXES, NO_REGIME ou REGIME_GAP) : le detail voyage mot
    # pour mot avec le rapport, c'est la partie qui a de la valeur.
    if report.refusal is not None:
        print(f"\nREFUS : {report.refusal['title']}")
        print(report.refusal["detail"])
        return 1

    # 4. Lignes ecartees, lignes scorees.
    print(
        f"\nlignes totales        : {report.window['total']}\n"
        f"ecartees (preuve insuffisante) : {report.window['no_evidence']}\n"
        f"scorees               : {report.distribution['scored']}"
    )

    # 5. Volume par jour.
    _print_days(report.window["by_day"], report.warnings)

    # 6. Distribution.
    d = report.distribution
    print(
        "\ndistribution des scores (opportunity_score, 0-100) :\n"
        f"  p50={d['p50']}  p90={d['p90']}  p99={d['p99']}  p99.9={d['p999']}  "
        f"max={d['max']}"
    )

    # 7. Rapport seul si pas de cible.
    if report.proposal is None:
        print("\n--decisions-per-day non fourni : rapport seul, aucun seuil propose.")
        return 0

    # 8. Seuil, debit reel, symboles distincts, part passant le plancher de
    # confiance du risk-engine.
    p = report.proposal
    print(
        f"\nDECISION_THRESHOLD propose : {p['threshold']}\n"
        f"  debit reel        : {p['actual_per_day']:.1f} decisions/jour "
        f"(cible {p['target_per_day']})\n"
        f"  dont passant le plancher de confiance (RISK_MIN_CONFIDENCE="
        f"{ts._RISK_MIN_CONFIDENCE}) : {p['confidence_passing_per_day']:.1f}/jour "
        f"({p['passing_pct']:.1f}% du debit reel)\n"
        f"  symboles distincts : {p['distinct_symbols']:.1f}/jour\n"
        "  Les deux premiers different parce qu'un meme symbole peut franchir "
        "le seuil plusieurs fois par heure : le second est le nombre "
        "d'opportunites, le premier le nombre d'evenements a absorber en aval."
    )

    # 9. Effet de RISK_MIN_SCORE a quelques valeurs autour du seuil.
    print("\neffet de RISK_MIN_SCORE (garde aval, services/risk-engine) :")
    for row in p["risk_min_score_effect"]:
        note = "  <-- defaut actuel" if row["is_default"] else ""
        print(
            f"  RISK_MIN_SCORE={row['value']:3d} : {row['lines']} lignes "
            f"({row['pct']:.1f}% des scorees), dont {row['confidence_passing']} "
            f"passant le plancher de confiance{note}"
        )

    # 10. Population Sonnet.
    s = report.sonnet
    if s["n"]:
        print(
            f"\npopulation Sonnet (sonnet_score) : n={s['n']} "
            f"p50={s['p50']} max={s['max']}"
        )
        if s["warning"]:
            print(f"  MISE EN GARDE : {s['warning']}")
    else:
        print("\npopulation Sonnet : aucune ligne avec sonnet_score sur la fenetre.")

    return 0


async def _run(args: argparse.Namespace) -> int:
    db = Database(get_settings().db)
    async with db.sessionmaker() as session:
        scan = await ts.scan_window(session, args.days)
    await db.engine.dispose()
    if scan.total == 0:
        print("aucune ligne sur la fenetre demandee", file=sys.stderr)
        return 1
    report = ts.analyze(scan, days=args.days, target_per_day=args.decisions_per_day)
    return _print_report(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="taille de la fenetre")
    parser.add_argument(
        "--decisions-per-day",
        type=int,
        default=None,
        help="debit cible ; omis, le script n'imprime que le rapport",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
