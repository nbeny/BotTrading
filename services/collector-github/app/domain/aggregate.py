"""Agrégation des dépôts d'un même coin. Pur, sans I/O.

Un coin a N dépôts : un client de référence, des bibliothèques, parfois un site.
L'agrégat somme les comptages puis recalcule les ratios sur la somme, plutôt que
de moyenner des ratios — une moyenne de ratios donne le même poids à un dépôt de
documentation qu'au client principal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .activity import (
    RepoStats,
    commit_ratio,
    days_since_push,
    pr_ratio,
    star_growth_pct,
)


@dataclass(frozen=True, slots=True)
class CoinActivity:
    repo_count: int
    commit_ratio_4w: float | None
    pr_ratio_4w: float | None
    #: `max(...)` sur les dépôts vivants : un seul horodatage dérivé dans le
    #: futur (dépôt mal daté, horloge tierce décalée) fait donc gagner ce
    #: dépôt et écrase la fraîcheur de tout le coin, y compris celle de dépôts
    #: réellement actifs. C'est le sens conservateur — on préfère sous-estimer
    #: la fraîcheur d'un coin actif plutôt que la sur-estimer pour un coin mort
    #: — et il est délibéré, pas un oubli.
    days_since_push: int | None
    star_growth_pct_7d: float | None
    all_repos_archived: bool
    #: Un `pushed_at` existait mais a été rejeté (horloge décalée). N'entre pas
    #: dans l'événement : c'est un signal d'observabilité pour l'appelant, qui
    #: seul a le droit d'incrémenter un compteur.
    push_timestamp_rejected: bool = False


def _is_live(stats: RepoStats) -> bool:
    """Un dépôt archivé ou forké n'est pas une mesure d'activité à zéro.

    Il est hors sujet : un miroir n'a pas vocation à commiter et un dépôt
    archivé annonce lui-même qu'il ne bougera plus. Les compter à zéro
    diluerait l'activité réelle du projet proportionnellement au nombre de
    miroirs qu'il traîne.
    """
    return not stats.archived and not stats.is_fork


def _sum_or_none(values: Sequence[int | None]) -> int | None:
    """Somme, ou None si *aucune* valeur n'a été mesurée.

    Les None individuels (dépôt en 202) sont ignorés plutôt que de contaminer
    tout l'agrégat : deux dépôts mesurés sur trois valent mieux qu'aucune
    lecture. Mais zéro dépôt mesuré ne vaut pas 0.
    """
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def aggregate(repos: Sequence[RepoStats], now: datetime) -> CoinActivity | None:
    """Agrège les dépôts d'un coin, ou ``None`` si aucun dépôt n'est connu.

    La distinction est load-bearing : « aucun dépôt connu » veut dire que le
    mapping n'a rien trouvé, tandis que « tous les dépôts archivés » est un
    constat de mort mesuré. Le premier doit laisser l'axe absent, le second
    doit le noter à zéro.
    """
    if not repos:
        return None

    live = [r for r in repos if _is_live(r)]
    if not live:
        return CoinActivity(
            repo_count=0,
            commit_ratio_4w=0.0,
            pr_ratio_4w=0.0,
            days_since_push=None,
            star_growth_pct_7d=None,
            all_repos_archived=True,
        )

    # ATTENTION — décision à prendre ici, pas ailleurs. `star_growth_pct` divise
    # par `stars_at - stars_prev_at`, et chaque dépôt a *ses* deux instants : le
    # round-robin ne les rafraîchit pas ensemble. Sommer les étoiles puis
    # diviser par un intervalle inventé pour l'ensemble redonnerait exactement
    # le défaut que la tâche 2 a corrigé.
    #
    # Calcul du taux **par dépôt**, puis moyenne pondérée par `stars_prev`.
    # C'est identique à la somme quand les intervalles coïncident, et correct
    # quand ils divergent. On ne prend pas `min`/`max` des instants : la
    # fenêtre la plus étroite surestime le taux, donc biaise à la hausse — le
    # mauvais sens.
    merged = RepoStats(
        owner=live[0].owner,
        repo=f"<{len(live)} repos>",
        stars=_sum_or_none([r.stars for r in live]),
        pushed_at=max((r.pushed_at for r in live if r.pushed_at), default=None),
        commits_4w=_sum_or_none([r.commits_4w for r in live]),
        commits_median_52w=_sum_median([r.commits_median_52w for r in live]),
        pr_merged_4w=_sum_or_none([r.pr_merged_4w for r in live]),
        pr_merged_52w=_sum_or_none([r.pr_merged_52w for r in live]),
        stars_prev=_sum_or_none([r.stars_prev for r in live]),
    )
    freshness = days_since_push(merged, now)
    return CoinActivity(
        repo_count=len(live),
        commit_ratio_4w=commit_ratio(merged),
        pr_ratio_4w=pr_ratio(merged),
        days_since_push=freshness,
        # Plus de `now` : l'intervalle court d'un snapshot à l'autre, pas
        # jusqu'à l'horloge du cycle. Voir la note ci-dessus sur l'agrégation.
        star_growth_pct_7d=_weighted_star_growth(live),
        all_repos_archived=False,
        # Rapporté, pas compté : ce module reste pur, et c'est l'appelant qui
        # incrémente la métrique. L'horodatage existait mais n'a pas été cru —
        # horloge décalée au-delà de CLOCK_SKEW_TOLERANCE. Sans cette
        # remontée la perte serait invisible : freshness pèse 0.25 de l'axe,
        # et une dérive d'horloge l'annule pour *tous* les dépôts récemment
        # poussés d'un coup, l'axe se renormalisant en silence sur 0.75.
        push_timestamp_rejected=merged.pushed_at is not None and freshness is None,
    )


def _weighted_star_growth(live: Sequence[RepoStats]) -> float | None:
    """Taux de croissance d'étoiles du coin, pondéré par la base de chaque dépôt.

    Par dépôt puis moyenné, jamais sommé : chaque dépôt porte ses propres
    ``stars_at``/``stars_prev_at``, et le round-robin ne les rafraîchit pas
    ensemble. Diviser une somme d'étoiles par un intervalle commun inventé
    rejouerait le défaut corrigé en tâche 2, où l'intervalle réel et
    l'intervalle supposé divergeaient.

    Pondéré par ``stars_prev`` parce qu'un dépôt de documentation à 30 étoiles
    ne doit pas peser autant que le client principal à 40 000.

    Une lecture partielle (certains dépôts sans taux utilisable) ne se
    transforme jamais en lecture complète : seuls les dépôts dont le taux est
    effectivement calculable entrent dans la moyenne, et si aucun ne l'est le
    résultat reste ``None`` plutôt qu'un 0.0 fabriqué.
    """
    rates = [
        (star_growth_pct(r), r.stars_prev)
        for r in live
        if r.stars_prev is not None and r.stars_prev > 0
    ]
    usable = [(rate, base) for rate, base in rates if rate is not None]
    if not usable:
        return None
    total = sum(base for _, base in usable)
    return sum(rate * base for rate, base in usable) / total


def _sum_median(values: Sequence[float | None]) -> float | None:
    """Somme des médianes hebdomadaires.

    Ce n'est pas la médiane de la somme, et c'est volontaire : on ne dispose
    que des médianes par dépôt, et leur somme est la meilleure estimation du
    rythme de croisière de l'ensemble sans redemander les 52 séries.
    """
    present = [v for v in values if v is not None]
    return sum(present) if present else None
