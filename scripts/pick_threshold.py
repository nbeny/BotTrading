#!/usr/bin/env python
"""Choisit DECISION_THRESHOLD par rejeu du journal de decision.

Le seuil est une **vanne de debit**, pas un reglage de finesse. Mesure en
production, le volume de lignes de journal varie d'un facteur ~15 selon le
jour (671 955 le 2026-07-31, 0 le lendemain) -- ~276 000 par jour est le
compte d'UNE SEULE journee (03 aout), pas une moyenne qui aurait un sens, et
ce script imprime desormais la repartition jour par jour pour que ca ne se
reproduise pas. Contre MAX_ORDERS_PER_HOUR=10 en aval, meme le 99,9e
percentile laisserait passer un ordre de grandeur comparable de decisions par
jour. L'operateur choisit donc un debit sur une fenetre dont il doit juger la
representativite, et le script rend le seuil qui le produit -- l'inverse ne
veut rien dire.

Ce qu'il ne faut PAS faire, et qui a ete propose deux fois: un ratio SQL sur
`decision_journal.score / confidence`. Ces colonnes ne sont pas la sortie de ce
modele -- ai-worker-sonnet les ecrit depuis analysis.*, c'est-a-dire le scoreur
a quatre facteurs de haiku, dont la confiance est une affine plancherisee a
0.25 sans rapport avec le poids present. Mesure sur 30k echantillons,
l'identite est violee de plus d'un point sur 24% des lignes, au pire de +33,8.
Les deux erreurs poussent le seuil trop haut, ce qui restaure le blocage que ce
travail supprime.

Le rapport de presence par axe sort AVANT tout nombre, et le script refuse de
proposer un seuil si un axe est sous MIN_PRESENCE_PCT -- pas seulement
strictement a zero, cf. la constante plus bas. C'est son garde-fou central: le
2026-08-04, positioning etait a 0 sur 1 281 511 lignes, et une calibration
lancee ce jour-la aurait rendu un nombre parfaitement plausible et faux.

Usage :
    python scripts/pick_threshold.py --days 7 --decisions-per-day 200
    python scripts/pick_threshold.py --days 7            # rapport seul
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

# Ce script touche deux services, et chacun embarque un package nomme `app`.
# Un double `sys.path.insert` ne marche pas : le premier `import app` fige
# `sys.modules["app"]` sur le premier service charge, et l'import suivant
# chercherait `app.scoring` dedans. On reutilise donc le chargeur des tests,
# qui enregistre chaque service sous un alias distinct.
_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "tests"))
sys.path.insert(0, os.path.join(_ROOT, "libs", "cmi_common"))

from service_modules import load_service_module  # noqa: E402
from sqlalchemy import select  # noqa: E402

from cmi_common.config import get_settings  # noqa: E402
from cmi_common.db import Database, DecisionJournal  # noqa: E402

_scoring = load_service_module("decision-engine", "scoring")
features_from = load_service_module("decision-engine", "features_map").features_from
score = _scoring.score
WEIGHTS = _scoring.WEIGHTS

#: Cle du dict de features qui atteste qu'un axe a ete *lu* pour cette ligne.
#: Une seule cle par axe suffit: la presence se mesure a la source du signal,
#: pas au fait que l'axe ait ete score, qui peut dependre d'un XOR interne.
AXIS_PROBE = {
    "volume_growth": "volume_spike_ratio",
    "social_score": "social_growth",
    "news_score": "sentiment_score",
    "market_trend": "price_change_pct_24h",
    "liquidity_score": "volume_24h_usd",
    "positioning": "funding_rate_8h",
    "fundamentals": "tvl_change_pct_7d",
    "developer_activity": "commit_ratio_4w",
}


def _check_axis_probe_matches_weights(
    probe: dict[str, str], weights: dict[str, float]
) -> None:
    """Echoue bruyamment, a l'import, si AXIS_PROBE et WEIGHTS divergent.

    `tests/test_axis_parity.py` verrouille deja trois copies independantes de
    la liste des axes (`scoring.py::WEIGHTS`, `dossier.py::AXIS_KEYS`,
    `dossier.ts::SCORE_AXES`) precisement parce qu'un axe oublie devient
    invisible sans jamais lever d'erreur ni faire echouer un test. AXIS_PROBE
    ci-dessus est une QUATRIEME copie, hors de ce test-la: un neuvieme axe
    ajoute a WEIGHTS mais pas ici disparaitrait du rapport de presence, et le
    garde du DEFAUT 1 (axe sous MIN_PRESENCE_PCT) ne pourrait alors plus le
    declarer muet -- silencieusement, puisqu'un axe absent d'AXIS_PROBE n'est
    tout simplement jamais compte.
    """
    probe_axes = set(probe)
    weight_axes = set(weights)
    if probe_axes != weight_axes:
        missing_from_probe = sorted(weight_axes - probe_axes)
        extra_in_probe = sorted(probe_axes - weight_axes)
        raise RuntimeError(
            "AXIS_PROBE (scripts/pick_threshold.py) et WEIGHTS "
            "(decision-engine/app/scoring.py) ont diverge : "
            f"axes dans WEIGHTS absents d'AXIS_PROBE={missing_from_probe}, "
            f"axes dans AXIS_PROBE absents de WEIGHTS={extra_in_probe}. "
            "Un axe absent d'AXIS_PROBE sort silencieusement du rapport de "
            "presence -- corrige AXIS_PROBE avant toute calibration."
        )


_check_axis_probe_matches_weights(AXIS_PROBE, WEIGHTS)

#: Valeur par defaut du garde en aval (services/risk-engine/app/main.py), pour
#: situer le seuil calibre par rapport a ce qui tourne aujourd'hui.
_DEFAULT_RISK_MIN_SCORE = 70

#: Plancher de confiance reellement en vigueur en production. Le defaut code
#: dans RiskConfig.min_confidence (services/risk-engine/app/rules.py) est
#: 0.506, mais docker-compose.vps.yml:398 le surcharge a 0.3795 -- c'est ce
#: dernier qui tourne, donc c'est celui-ci qui doit filtrer ce rapport, pas le
#: defaut perime du code. Meme mecanique de lecture que le service lui-meme.
_RISK_MIN_CONFIDENCE = float(os.getenv("RISK_MIN_CONFIDENCE", "0.3795"))

#: Un axe present sur une seule ligne sur 1 281 511 (7,8e-5 %) n'est pas un
#: axe qui parle : c'est un redemarrage de collecteur qui a survecu un cycle
#: avant de re-echouer. `pct == 0.0` ratait ce cas -- 1/1 281 511 s'arrondit a
#: "0.0%" a l'affichage (`:5.1f`) mais n'est pas *egal* a 0.0 -- si bien que le
#: garde ne se declenchait pas alors que l'operateur lisait "0.0%" et concluait
#: a tort que l'axe avait ete valide. Le seuil est un pourcentage, pas une
#: egalite stricte, precisement pour attraper ce cas.
MIN_PRESENCE_PCT = 1.0


@dataclass
class Scan:
    """Tout ce que le rejeu retient, et rien de plus.

    La fenetre ne tient pas en memoire: 1 414 216 lignes sur 7 jours, 368 Mo de
    JSONB compresse, contre 1 996 Mo disponibles sur le VPS. Le parcours est
    donc un flux, et l'on n'accumule que des agregats bornes.

    `best_by_symbol_day` merite un mot: compter les symboles distincts par seuil
    en gardant un ensemble par seuil couterait jusqu'a cent insertions par
    ligne, soit ~141 millions. Retenir le meilleur score de chaque couple
    (symbole, jour) donne la meme reponse pour tout seuil, en ~10 000 entrees.
    """

    total: int = 0
    no_evidence: int = 0
    #: Lignes portant une lecture de regime. Zero signifie que la fenetre
    #: precede le deploiement qui la journalise, pas qu'il n'y en avait pas.
    regime_seen: int = 0
    presence: Counter = field(default_factory=Counter)
    score_counts: Counter = field(default_factory=Counter)
    #: Sous-ensemble de score_counts dont la ligne franchit aussi
    #: RISK_MIN_CONFIDENCE -- le risk-engine applique ce plancher *avant*
    #: min_score (services/risk-engine/app/rules.py::evaluate), donc une ligne
    #: qui echoue ici n'atteint jamais le test sur le score.
    confidence_pass_counts: Counter = field(default_factory=Counter)
    best_by_symbol_day: dict = field(default_factory=dict)
    sonnet_scores: list = field(default_factory=list)
    #: Bornes temporelles de la fenetre effectivement lue, pour detecter un
    #: regime partiellement journalise (DEFAUT 4) et batir la liste des jours
    #: (DEFAUT 6). `since` est la borne demandee (`--days`), `min_time` la
    #: plus ancienne ligne reellement vue.
    since: datetime | None = None
    min_time: datetime | None = None
    #: Plus ancienne ligne portant `market_sentiment`. None tant que
    #: `regime_seen == 0`.
    min_time_with_regime: datetime | None = None
    #: Lignes par jour (cle = date ISO). Borne par construction: au plus
    #: quelques dizaines d'entrees, une par jour de la fenetre `--days`.
    by_day: Counter = field(default_factory=Counter)


def _threshold_for(counts: Counter, target_per_day: int, days: int) -> int:
    """Plus petit seuil entier dont le debit ne depasse pas la cible.

    Quand meme le score maximal a lui seul depasse le budget (cible tres
    basse), ``value + 1`` vaudrait 101 -- hors de l'echelle 0-100. Le seuil le
    plus severe possible est 100 : il n'existe aucun seuil qui respecte la
    cible, donc on rend le plus proche, pas un nombre hors echelle.
    """
    budget = target_per_day * days
    running = 0
    for value in range(100, -1, -1):
        running += counts[value]
        if running > budget:
            return min(100, value + 1)
    return 0


def _percentile(counts: Counter, pct: float) -> int:
    """Percentile lu sur un histogramme, sans materialiser la serie."""
    total = sum(counts.values())
    if total == 0:
        return 0
    target = total * pct / 100.0
    running = 0
    for value in range(0, 101):
        running += counts[value]
        if running >= target:
            return value
    return 100


def _counts_ge(counts: Counter, threshold: int) -> int:
    """Nombre de valeurs enregistrees >= threshold, threshold clampe a [0, 100]."""
    lo = max(0, min(100, threshold))
    return sum(counts[v] for v in range(lo, 101))


def _bounded_passing(counts: Counter, v: int, threshold: int) -> int:
    """Lignes >= v qui existeront reellement une fois DECISION_THRESHOLD applique.

    Le decision-engine n'emet que ce qui franchit DECISION_THRESHOLD (le seuil
    calibre ici) -- une ligne en dessous n'existe pour aucun garde en aval,
    quelle que soit la valeur de RISK_MIN_SCORE testee. Sans ce plancher,
    `_counts_ge(counts, v)` pour un `v` sous le seuil compte une population qui
    ne sera jamais soumise au risk-engine : avec un seuil calibre a 85, la
    ligne "RISK_MIN_SCORE=70" afficherait un nombre sans referent.
    """
    return _counts_ge(counts, max(v, threshold))


#: "Quelques heures" au sens du DEFAUT 4 : le pas d'echantillonnage naturel
#: (cycles de collecte, TTL FeatureStore ~900s) se compte en minutes, jamais
#: en heures -- un ecart plus grand entre le debut de la fenetre et la
#: premiere ligne portant market_sentiment ne peut venir que d'un deploiement
#: survenu APRES le debut de la fenetre demandee, pas d'une gigue normale.
_REGIME_GAP_WARN_HOURS = 6.0

#: Un jour dont le volume s'ecarte de plus de moitie de la mediane de la
#: fenetre est signale -- pas refuse, l'operateur juge. Mesure en prod, le
#: facteur va jusqu'a 15 (671 955 vs 0), tres au-dessus de ce seuil.
_DAY_VOLUME_WARN_RATIO = 0.5


async def _scan(days: int) -> Scan:
    db = Database(get_settings().db)
    since = datetime.now(tz=UTC) - timedelta(days=days)
    stmt = (
        select(
            DecisionJournal.time,
            DecisionJournal.symbol,
            DecisionJournal.features,
            DecisionJournal.sonnet_score,
        )
        .where(DecisionJournal.time > since)
        .execution_options(yield_per=5_000)
    )
    scan = Scan(since=since)
    async with db.sessionmaker() as session:
        result = await session.stream(stmt)
        async for time_, symbol, raw, sonnet in result:
            scan.total += 1
            raw = raw or {}
            if scan.min_time is None or time_ < scan.min_time:
                scan.min_time = time_
            scan.by_day[time_.date().isoformat()] += 1
            for axis, probe in AXIS_PROBE.items():
                if probe in raw:
                    scan.presence[axis] += 1
            if "market_sentiment" in raw:
                scan.regime_seen += 1
                if (
                    scan.min_time_with_regime is None
                    or time_ < scan.min_time_with_regime
                ):
                    scan.min_time_with_regime = time_
            if sonnet is not None:
                scan.sonnet_scores.append(sonnet)
            outcome = score(features_from(raw, now=time_))
            if outcome.confidence == 0.0:
                # _MIN_PRESENT_WEIGHT a refuse de renormaliser: trop peu de
                # preuve. Ces lignes n'auraient produit aucune decision, quel
                # que soit le seuil; les compter tirerait la distribution vers
                # le bas et donnerait un seuil trop permissif.
                scan.no_evidence += 1
                continue
            value = outcome.opportunity_score
            scan.score_counts[value] += 1
            if outcome.confidence >= _RISK_MIN_CONFIDENCE:
                scan.confidence_pass_counts[value] += 1
            key = (symbol, time_.date().isoformat())
            if value > scan.best_by_symbol_day.get(key, -1):
                scan.best_by_symbol_day[key] = value
    await db.engine.dispose()
    return scan


def _report(scan: Scan, args: argparse.Namespace) -> int:
    days = args.days

    # 1. Presence par axe, triee par poids decroissant, marqueur sur tout axe
    # sous MIN_PRESENCE_PCT.
    print("presence par axe (part des lignes ou l'axe a ete lu) :")
    ordered_axes = sorted(AXIS_PROBE, key=lambda a: WEIGHTS[a], reverse=True)
    mute_axes = []
    for axis in ordered_axes:
        seen = scan.presence[axis]
        pct = 100.0 * seen / scan.total if scan.total else 0.0
        marker = ""
        if pct < MIN_PRESENCE_PCT:
            marker = "  <-- MUET"
            mute_axes.append(axis)
        # Le compte brut est imprime a cote du pourcentage : a 5 decimales
        # pres, "1 ligne sur 1 281 511" s'affiche "0.0%" (`:5.1f`) et un
        # operateur qui ne lirait que le pourcentage n'y verrait aucune
        # anomalie -- cf. MIN_PRESENCE_PCT ci-dessus.
        print(
            f"  {axis:20s} poids={WEIGHTS[axis]:.4f}  "
            f"presence={pct:5.1f}% ({seen} lignes){marker}"
        )

    # 2. Refus si un axe est sous MIN_PRESENCE_PCT.
    if mute_axes:
        print(
            "\nREFUS : "
            + ", ".join(mute_axes)
            + f" sous {MIN_PRESENCE_PCT:.1f}% de presence sur la fenetre."
        )
        print(
            "Un axe absent est EXCLU du denominateur de renormalisation, pas note "
            "zero -- donc un seuil calibre ici vaudrait pour un modele ampute de "
            "ce poids, et deviendrait faux des que l'axe reparle, sans erreur ni "
            "test rouge. Corrige la source (collecteur, health check) avant de "
            "recalibrer."
        )
        return 1

    # 3. Refus si aucune lecture de regime, ou si la fenetre couvre des jours
    # anterieurs au deploiement qui le journalise -- un regime PARTIELLEMENT
    # couvert desarme ce garde tout autant qu'un regime absent (DEFAUT 4) :
    # rejouer les jours anterieurs au deploiement leur retire silencieusement
    # news_score pour tout symbole sans sentiment propre.
    if scan.regime_seen == 0:
        print("\nREFUS : market_sentiment absent de toutes les lignes de la fenetre.")
        print(
            "Cela signifie que la fenetre precede le deploiement qui journalise "
            "market_sentiment, alors que le moteur avait la valeur en memoire au "
            "moment de decider. Rejouer ces lignes retirerait l'axe news_score au "
            "tiers des symboles sans sentiment propre. (Une absence PARTIELLE est "
            "normale : mesure sur 14 jours, 38 ecarts d'alimentation sur 399 "
            "depassent le TTL d'une heure -- ce n'est pas ce garde-la.)"
        )
        return 1

    if scan.min_time is not None and scan.min_time_with_regime is not None:
        gap = scan.min_time_with_regime - scan.min_time
        if gap > timedelta(hours=_REGIME_GAP_WARN_HOURS):
            suggested_days = max(
                1,
                math.ceil(
                    (datetime.now(tz=UTC) - scan.min_time_with_regime)
                    / timedelta(days=1)
                ),
            )
            print(
                "\nREFUS : le regime (market_sentiment) n'est journalise que depuis "
                f"{scan.min_time_with_regime.isoformat()}, alors que la fenetre "
                f"demandee commence a {scan.min_time.isoformat()} -- un ecart de "
                f"{gap.total_seconds() / 3600:.1f}h, bien au-dela du pas de collecte "
                "normal."
            )
            print(
                "Le bord fautif est le DEBUT de la fenetre (--days trop grand pour "
                "ce deploiement) : les lignes anterieures au premier "
                "market_sentiment seraient rejouees sans regime, exactement le "
                "defaut que ce garde existe pour attraper. Relance avec "
                f"--days {suggested_days} pour ne couvrir que la periode ou le "
                "regime est journalise."
            )
            return 1

    # 4. Lignes ecartees par _MIN_PRESENT_WEIGHT, lignes scorees.
    scored = sum(scan.score_counts.values())
    print(
        f"\nlignes totales        : {scan.total}\n"
        f"ecartees (preuve insuffisante) : {scan.no_evidence}\n"
        f"scorees               : {scored}"
    )

    # 5. Volume par jour. Une cible "decisions par jour" moyennee sur une
    # fenetre non homogene n'a pas le sens qu'on lui prete -- mesure en prod,
    # le volume journalier varie d'un facteur 15 et deux jours sont a zero.
    # Avertissement, pas un refus : c'est a l'operateur de juger si sa fenetre
    # est representative.
    print("\nlignes par jour :")
    days_covered: list[str] = []
    if scan.since is not None and scan.min_time is not None:
        start_day = scan.since.date()
        end_day = datetime.now(tz=UTC).date()
        n_days = (end_day - start_day).days
        days_covered = [
            (start_day + timedelta(days=i)).isoformat() for i in range(n_days + 1)
        ]
    else:
        days_covered = sorted(scan.by_day)
    counts_per_day = [scan.by_day[d] for d in days_covered]
    median_day = statistics.median(counts_per_day) if counts_per_day else 0.0
    for d in days_covered:
        n = scan.by_day[d]
        flag = ""
        if n == 0:
            flag = "  <-- VIDE"
        elif median_day and abs(n - median_day) / median_day > _DAY_VOLUME_WARN_RATIO:
            flag = "  <-- ecart fort a la mediane"
        print(f"  {d} : {n}{flag}")

    # 6. Distribution.
    p50 = _percentile(scan.score_counts, 50)
    p90 = _percentile(scan.score_counts, 90)
    p99 = _percentile(scan.score_counts, 99)
    p999 = _percentile(scan.score_counts, 99.9)
    max_score = max(scan.score_counts) if scan.score_counts else 0
    print(
        "\ndistribution des scores (opportunity_score, 0-100) :\n"
        f"  p50={p50}  p90={p90}  p99={p99}  p99.9={p999}  max={max_score}"
    )

    # 7. Rapport seul si pas de cible.
    if args.decisions_per_day is None:
        print("\n--decisions-per-day non fourni : rapport seul, aucun seuil propose.")
        return 0

    # 8. Seuil, debit reel, symboles distincts, part passant le plancher de
    # confiance du risk-engine (RISK_MIN_CONFIDENCE, applique AVANT
    # min_score -- cf. services/risk-engine/app/rules.py::evaluate). Sans
    # cette colonne, le debit annonce ici surestime ce qui atteint reellement
    # le risk-engine : ~34% des lignes perdent le poids de news_score des que
    # l'axe ne tient qu'au market_sentiment (cf. scoring.py::_confidence),
    # exactement la discordance score/plancher qui bloquait le pipeline en
    # juillet.
    threshold = _threshold_for(scan.score_counts, args.decisions_per_day, days)
    real_decisions = _counts_ge(scan.score_counts, threshold)
    real_per_day = real_decisions / days
    confidence_passing = _counts_ge(scan.confidence_pass_counts, threshold)
    confidence_pct = (
        100.0 * confidence_passing / real_decisions if real_decisions else 0.0
    )
    distinct_symbol_days = sum(
        1 for v in scan.best_by_symbol_day.values() if v >= threshold
    )
    distinct_per_day = distinct_symbol_days / days
    print(
        f"\nDECISION_THRESHOLD propose : {threshold}\n"
        f"  debit reel        : {real_per_day:.1f} decisions/jour "
        f"(cible {args.decisions_per_day})\n"
        f"  dont passant le plancher de confiance (RISK_MIN_CONFIDENCE="
        f"{_RISK_MIN_CONFIDENCE}) : {confidence_passing / days:.1f}/jour "
        f"({confidence_pct:.1f}% du debit reel)\n"
        f"  symboles distincts : {distinct_per_day:.1f}/jour\n"
        "  Les deux premiers different parce qu'un meme symbole peut franchir "
        "le seuil plusieurs fois par heure : le second est le nombre "
        "d'opportunites, le premier le nombre d'evenements a absorber en aval."
    )

    # 9. Effet de RISK_MIN_SCORE a quelques valeurs autour du seuil, avec la
    # meme colonne de confiance. Bornee via _bounded_passing : sous le seuil
    # calibre, une valeur de RISK_MIN_SCORE ne peut jamais voir plus de lignes
    # que le seuil lui-meme, puisque le decision-engine n'emet rien en dessous.
    print("\neffet de RISK_MIN_SCORE (garde aval, services/risk-engine) :")
    around = sorted(
        {
            max(0, threshold - 10),
            max(0, threshold - 5),
            threshold,
            min(100, threshold + 5),
            min(100, threshold + 10),
            _DEFAULT_RISK_MIN_SCORE,
        }
    )
    for v in around:
        passing = _bounded_passing(scan.score_counts, v, threshold)
        pct = 100.0 * passing / scored if scored else 0.0
        conf_passing = _bounded_passing(scan.confidence_pass_counts, v, threshold)
        note = "  <-- defaut actuel" if v == _DEFAULT_RISK_MIN_SCORE else ""
        print(
            f"  RISK_MIN_SCORE={v:3d} : {passing} lignes ({pct:.1f}% des scorees), "
            f"dont {conf_passing} passant le plancher de confiance{note}"
        )

    # 10. Population Sonnet.
    if scan.sonnet_scores:
        sonnet_sorted = sorted(scan.sonnet_scores)
        sonnet_p50 = sonnet_sorted[len(sonnet_sorted) // 2]
        sonnet_max = sonnet_sorted[-1]
        print(
            f"\npopulation Sonnet (sonnet_score) : n={len(sonnet_sorted)} "
            f"p50={sonnet_p50} max={sonnet_max}\n"
            "  MISE EN GARDE : ce score sort d'un LLM, pas de l'echelle a huit "
            "axes ci-dessus. Un plancher calibre sur la premiere population est "
            "arbitraire pour celle-ci -- c'est deja ce qui bloquait en juillet. "
            "Signale, pas resolu."
        )
    else:
        print("\npopulation Sonnet : aucune ligne avec sonnet_score sur la fenetre.")

    return 0


async def _run(args: argparse.Namespace) -> int:
    scan = await _scan(args.days)
    if scan.total == 0:
        print("aucune ligne sur la fenetre demandee", file=sys.stderr)
        return 1
    return _report(scan, args)


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
