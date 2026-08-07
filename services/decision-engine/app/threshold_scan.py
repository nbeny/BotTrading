"""Analyse pure du rejeu du journal de decision -- calibration de DECISION_THRESHOLD.

Extrait de ``scripts/pick_threshold.py`` : ce module porte le scan (I/O, via
``scan_window``) et toute la logique de decision (``analyze``, pur -- aucun I/O,
aucun ``print``). Le CLI (``scripts/pick_threshold.py``) et le service periodique
(``threshold_job.py``) en sont deux faces qui appellent les deux memes fonctions.

Le seuil est une **vanne de debit**, pas un reglage de finesse. Mesure en
production, le volume de lignes de journal varie d'un facteur ~15 selon le
jour (671 955 le 2026-07-31, 0 le lendemain) -- ~276 000 par jour est le
compte d'UNE SEULE journee (03 aout), pas une moyenne qui aurait un sens, et
ce rapport imprime desormais la repartition jour par jour pour que ca ne se
reproduise pas. Contre MAX_ORDERS_PER_HOUR=10 en aval, meme le 99,9e
percentile laisserait passer un ordre de grandeur comparable de decisions par
jour. L'operateur choisit donc un debit sur une fenetre dont il doit juger la
representativite, et ce module rend le seuil qui le produit -- l'inverse ne
veut rien dire.

Ce qu'il ne faut PAS faire, et qui a ete propose deux fois: un ratio SQL sur
`decision_journal.score / confidence`. Ces colonnes ne sont pas la sortie de ce
modele -- ai-worker-sonnet les ecrit depuis analysis.*, c'est-a-dire le scoreur
a quatre facteurs de haiku, dont la confiance est une affine plancherisee a
0.25 sans rapport avec le poids present. Mesure sur 30k echantillons,
l'identite est violee de plus d'un point sur 24% des lignes, au pire de +33,8.
Les deux erreurs poussent le seuil trop haut, ce qui restaure le blocage que ce
travail supprime.

Le rapport de presence par axe sort AVANT tout nombre, et cette analyse refuse
de proposer un seuil si un axe est sous MIN_PRESENCE_PCT -- pas seulement
strictement a zero, cf. la constante plus bas. C'est son garde-fou central: le
2026-08-04, positioning etait a 0 sur 1 281 511 lignes, et une calibration
lancee ce jour-la aurait rendu un nombre parfaitement plausible et faux.
"""

from __future__ import annotations

import math
import os
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from cmi_common.db import DecisionJournal

from .features_map import features_from
from .scoring import WEIGHTS, score

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
            "AXIS_PROBE (decision-engine/app/threshold_scan.py) et WEIGHTS "
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
#:
#: 1.0 est pose, pas mesure -- et surtout, un plancher UNIFORME sur les huit
#: axes suppose a tort qu'ils devraient tous avoir la meme couverture. Un
#: token sans depot GitHub n'a legitimement aucune lecture developer_activity;
#: un token sans contrat perpetuel n'a legitimement aucune lecture
#: positioning. Le seul chiffre mesure a ce jour est fundamentals a 3,6% de
#: presence -- et c'est un axe qui fonctionne. Ce garde peut donc refuser sur
#: un axe parfaitement sain: c'est pourquoi le message de refus (plus bas)
#: enonce les deux lectures possibles au lieu de n'en offrir qu'une. Ce
#: plancher uniforme est un garde-fou PROVISOIRE, en attendant des planchers
#: PAR AXE mesures une fois que les huit axes auront tourne ensemble -- ce qui
#: n'est jamais arrive a ce jour, puisque c'est precisement ce que cette
#: branche rend possible.
MIN_PRESENCE_PCT = 1.0

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


async def scan_window(session: Any, days: int) -> Scan:
    """Rejoue la fenetre des `days` derniers jours sur une session ouverte par
    l'appelant.

    Le script et le job gerent chacun leur propre session (et leur propre
    engine) ; cette fonction ne fait qu'un `stream` dessus, en aggregats
    bornes (cf. `Scan`).
    """
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
            if scan.min_time_with_regime is None or time_ < scan.min_time_with_regime:
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
    return scan


@dataclass
class ThresholdReport:
    """Ce que le CLI imprime et ce que le service persiste, une seule fois.

    `refusal` porte le verdict ET son texte : c'est la partie qui a de la
    valeur. Un refus reduit a un booleen priverait l'operateur de ce qui
    distingue une collecte cassee d'un axe legitimement rare.
    """

    window: dict[str, Any]
    axes: list[dict[str, Any]]
    refusal: dict[str, Any] | None
    distribution: dict[str, Any]
    proposal: dict[str, Any] | None
    warnings: list[str]
    sonnet: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "axes": self.axes,
            "refusal": self.refusal,
            "distribution": self.distribution,
            "proposal": self.proposal,
            "warnings": self.warnings,
            "sonnet": self.sonnet,
        }


def analyze(
    scan: Scan,
    *,
    days: int,
    target_per_day: int | None = None,
    now: datetime | None = None,
) -> ThresholdReport:
    """Reprend l'enchainement de decision de l'ancien `_report`, dans le meme ordre.

    Presence par axe (triee par poids decroissant, `mute` si sous
    MIN_PRESENCE_PCT) -> refus MUTE_AXES -> refus NO_REGIME -> refus
    REGIME_GAP (avec `suggested_days`) -> distribution -> proposition si une
    cible est donnee. Pur : aucun I/O, aucun `print` -- c'est la responsabilite
    du CLI et, plus tard, du job.
    """
    now = now or datetime.now(tz=UTC)
    warnings: list[str] = []

    # 1. Presence par axe, triee par poids decroissant, marqueur sur tout axe
    # sous MIN_PRESENCE_PCT.
    ordered_axes = sorted(AXIS_PROBE, key=lambda a: WEIGHTS[a], reverse=True)
    axes: list[dict[str, Any]] = []
    mute_axes: list[str] = []
    for axis in ordered_axes:
        seen = scan.presence[axis]
        pct = 100.0 * seen / scan.total if scan.total else 0.0
        mute = pct < MIN_PRESENCE_PCT
        if mute:
            mute_axes.append(axis)
        # Le compte brut voyage a cote du pourcentage : a 5 decimales pres,
        # "1 ligne sur 1 281 511" s'affiche "0.0%" (`:5.1f`) et un operateur
        # qui ne lirait que le pourcentage n'y verrait aucune anomalie --
        # cf. MIN_PRESENCE_PCT ci-dessus.
        axes.append(
            {
                "key": axis,
                "weight": WEIGHTS[axis],
                "seen": seen,
                "pct": pct,
                "mute": mute,
            }
        )

    window: dict[str, Any] = {
        "days": days,
        "min_time": scan.min_time.isoformat() if scan.min_time else None,
        "total": scan.total,
        "no_evidence": scan.no_evidence,
        "by_day": dict(scan.by_day),
    }
    distribution: dict[str, Any] = {}
    proposal: dict[str, Any] | None = None
    sonnet: dict[str, Any] = {}

    def _refuse(refusal: dict[str, Any]) -> ThresholdReport:
        # Capture par nom, pas par valeur : appelee immediatement apres avoir
        # pose `refusal`, avant que `distribution`/`sonnet` ne soient
        # remplies -- exactement ce que les anciens retours anticipes de
        # `_report` emettaient.
        return ThresholdReport(
            window, axes, refusal, distribution, proposal, warnings, sonnet
        )

    # 2. Refus si un axe est sous MIN_PRESENCE_PCT.
    if mute_axes:
        refusal: dict[str, Any] = {
            "code": "MUTE_AXES",
            "title": (
                ", ".join(mute_axes)
                + f" sous {MIN_PRESENCE_PCT:.1f}% de presence sur la fenetre."
            ),
            "detail": (
                "Un axe absent est EXCLU du denominateur de renormalisation, pas note "
                "zero -- donc un seuil calibre ici vaudrait pour un modele ampute de "
                "ce poids, et deviendrait faux des que l'axe reparle, sans erreur ni "
                "test rouge.\n\n"
                "Ce refus a deux lectures possibles, et le pourcentage seul ne "
                "tranche pas entre elles :\n"
                "  1. la collecte est cassee -- un collecteur echoue en silence "
                "(ex. collector-binance-futures le 2026-08-04) ; verifie ses logs et "
                "son /health, qui repond 503 apres plusieurs echecs consecutifs.\n"
                "  2. l'axe est legitimement rare -- il ne s'applique qu'a un "
                "sous-ensemble de tokens (positioning: pas de contrat perpetuel ; "
                "developer_activity: pas de depot public), et sa couverture reelle "
                "est simplement basse. Repere mesure : fundamentals tourne a 3,6% de "
                "presence et fonctionne.\n"
                "Ce qui distingue les deux : rapporte le compte brut affiche "
                "ci-dessus au nombre de tokens de la fenetre auxquels l'axe peut "
                "s'appliquer (pas au total des lignes, qui inclut les tokens hors "
                "champ). Si tu conclus a la seconde lecture, ajuste "
                "MIN_PRESENCE_PCT en connaissance de cause -- ne contourne pas ce "
                "garde. MIN_PRESENCE_PCT reste un plancher provisoire, uniforme sur "
                "les huit axes en l'absence de planchers mesures par axe (cf. le "
                "commentaire de la constante)."
            ),
        }
        return _refuse(refusal)

    # 3. Refus si aucune lecture de regime, ou si la fenetre couvre des jours
    # anterieurs au deploiement qui le journalise -- un regime PARTIELLEMENT
    # couvert desarme ce garde tout autant qu'un regime absent (DEFAUT 4) :
    # rejouer les jours anterieurs au deploiement leur retire silencieusement
    # news_score pour tout symbole sans sentiment propre.
    if scan.regime_seen == 0:
        refusal = {
            "code": "NO_REGIME",
            "title": "market_sentiment absent de toutes les lignes de la fenetre.",
            "detail": (
                "Cela signifie que la fenetre precede le deploiement qui journalise "
                "market_sentiment, alors que le moteur avait la valeur en memoire au "
                "moment de decider. Rejouer ces lignes retirerait l'axe news_score au "
                "tiers des symboles sans sentiment propre. (Une absence PARTIELLE est "
                "normale : mesure sur 14 jours, 38 ecarts d'alimentation sur 399 "
                "depassent le TTL d'une heure -- ce n'est pas ce garde-la.)"
            ),
        }
        return _refuse(refusal)

    if scan.min_time is not None and scan.min_time_with_regime is not None:
        gap = scan.min_time_with_regime - scan.min_time
        if gap > timedelta(hours=_REGIME_GAP_WARN_HOURS):
            suggested_days = max(
                1,
                math.ceil((now - scan.min_time_with_regime) / timedelta(days=1)),
            )
            refusal = {
                "code": "REGIME_GAP",
                "title": (
                    "le regime (market_sentiment) n'est journalise que depuis "
                    f"{scan.min_time_with_regime.isoformat()}, alors que la fenetre "
                    f"demandee commence a {scan.min_time.isoformat()} -- un ecart de "
                    f"{gap.total_seconds() / 3600:.1f}h, bien au-dela du pas de "
                    "collecte normal."
                ),
                "detail": (
                    "Le bord fautif est le DEBUT de la fenetre (--days trop grand pour "
                    "ce deploiement) : les lignes anterieures au premier "
                    "market_sentiment seraient rejouees sans regime, exactement le "
                    "defaut que ce garde existe pour attraper. Relance avec "
                    f"--days {suggested_days} pour ne couvrir que la periode ou le "
                    "regime est journalise."
                ),
                "suggested_days": suggested_days,
            }
            return _refuse(refusal)

    # 4. Lignes ecartees par _MIN_PRESENT_WEIGHT, lignes scorees.
    scored = sum(scan.score_counts.values())

    # 5. Volume par jour. Une cible "decisions par jour" moyennee sur une
    # fenetre non homogene n'a pas le sens qu'on lui prete -- mesure en prod,
    # le volume journalier varie d'un facteur 15 et deux jours sont a zero.
    # Avertissement, pas un refus : c'est a l'operateur de juger si sa fenetre
    # est representative.
    if scan.since is not None and scan.min_time is not None:
        start_day = scan.since.date()
        end_day = now.date()
        n_days = (end_day - start_day).days
        days_covered = [
            (start_day + timedelta(days=i)).isoformat() for i in range(n_days + 1)
        ]
    else:
        days_covered = sorted(scan.by_day)
    counts_per_day = [scan.by_day[d] for d in days_covered]
    median_day = statistics.median(counts_per_day) if counts_per_day else 0.0
    by_day_filled: dict[str, int] = {}
    for d in days_covered:
        n = scan.by_day[d]
        by_day_filled[d] = n
        if n == 0:
            warnings.append(f"{d} : aucune ligne (jour vide)")
        elif median_day and abs(n - median_day) / median_day > _DAY_VOLUME_WARN_RATIO:
            warnings.append(
                f"{d} : {n} lignes, ecart fort a la mediane de la fenetre "
                f"({median_day:.0f})"
            )
    window["by_day"] = by_day_filled

    # 6. Distribution.
    p50 = _percentile(scan.score_counts, 50)
    p90 = _percentile(scan.score_counts, 90)
    p99 = _percentile(scan.score_counts, 99)
    p999 = _percentile(scan.score_counts, 99.9)
    max_score = max(scan.score_counts) if scan.score_counts else 0
    distribution = {
        "scored": scored,
        "p50": p50,
        "p90": p90,
        "p99": p99,
        "p999": p999,
        "max": max_score,
    }

    # 10. Population Sonnet (numerotation conservee depuis l'ancien `_report` :
    # calculee ici mais rendue independamment de la presence d'une cible). La
    # mise en garde voyage dans `sonnet`, pas dans `warnings` : c'est une
    # caracteristique permanente de cette population (un score de LLM, pas de
    # l'echelle a huit axes), pas une anomalie de cette fenetre precise.
    if scan.sonnet_scores:
        sonnet_sorted = sorted(scan.sonnet_scores)
        sonnet = {
            "n": len(sonnet_sorted),
            "p50": sonnet_sorted[len(sonnet_sorted) // 2],
            "max": sonnet_sorted[-1],
            "warning": (
                "ce score sort d'un LLM, pas de l'echelle a huit axes ci-dessus. "
                "Un plancher calibre sur la premiere population est arbitraire "
                "pour celle-ci -- c'est deja ce qui bloquait en juillet. Signale, "
                "pas resolu."
            ),
        }
    else:
        sonnet = {"n": 0, "p50": None, "max": None, "warning": None}

    # 7. Rapport seul si pas de cible.
    if target_per_day is None:
        return ThresholdReport(
            window, axes, None, distribution, proposal, warnings, sonnet
        )

    # 8. Seuil, debit reel, symboles distincts, part passant le plancher de
    # confiance du risk-engine (RISK_MIN_CONFIDENCE, applique AVANT
    # min_score -- cf. services/risk-engine/app/rules.py::evaluate). Sans
    # cette colonne, le debit annonce ici surestime ce qui atteint reellement
    # le risk-engine : ~34% des lignes perdent le poids de news_score des que
    # l'axe ne tient qu'au market_sentiment (cf. scoring.py::_confidence),
    # exactement la discordance score/plancher qui bloquait le pipeline en
    # juillet.
    threshold = _threshold_for(scan.score_counts, target_per_day, days)
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

    # 9. Effet de RISK_MIN_SCORE a quelques valeurs autour du seuil, avec la
    # meme colonne de confiance. Borne via _bounded_passing : sous le seuil
    # calibre, une valeur de RISK_MIN_SCORE ne peut jamais voir plus de lignes
    # que le seuil lui-meme, puisque le decision-engine n'emet rien en dessous.
    risk_min_score_effect = []
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
        risk_min_score_effect.append(
            {
                "value": v,
                "lines": passing,
                "pct": pct,
                "confidence_passing": conf_passing,
                "is_default": v == _DEFAULT_RISK_MIN_SCORE,
            }
        )

    proposal = {
        "threshold": threshold,
        "target_per_day": target_per_day,
        "actual_per_day": real_per_day,
        "distinct_symbols": distinct_per_day,
        "passing_pct": confidence_pct,
        "confidence_passing_per_day": confidence_passing / days,
        "risk_min_score_effect": risk_min_score_effect,
    }

    return ThresholdReport(window, axes, None, distribution, proposal, warnings, sonnet)
