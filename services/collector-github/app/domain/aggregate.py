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
    #: La plus petite valeur (le push le plus récent) parmi les dépôts dont
    #: l'horodatage a été cru — jamais calculée sur un horodatage agrégé.
    #: Un dépôt à l'horloge décalée est écarté individuellement, pas laissé
    #: contaminer les autres : sans ça, un seul dépôt mal daté écraserait la
    #: fraîcheur mesurée de tous les dépôts sains du même coin, jetant une
    #: donnée correcte pour éviter d'en publier une fausse alors qu'écarter
    #: le seul dépôt fautif suffit.
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


def _sum_or_none(values: Sequence[int | float | None]) -> int | float | None:
    """Somme, ou None si *aucune* valeur n'a été mesurée.

    Les None individuels (dépôt en 202) sont ignorés plutôt que de contaminer
    tout l'agrégat : deux dépôts mesurés sur trois valent mieux qu'aucune
    lecture. Mais zéro dépôt mesuré ne vaut pas 0.

    Sert aussi bien des comptages entiers (``commits_4w``) que des médianes
    hebdomadaires (``commits_median_52w``) : dans les deux cas la somme des
    valeurs par dépôt est la meilleure estimation de l'ensemble, et la seule
    différence entre les deux usages était une annotation de type que rien
    ne fait respecter — `mypy` ne tourne nulle part dans ce dépôt.
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

    # `merged` ne porte ni `stars`/`stars_prev` ni `pushed_at` : les deux se
    # calculent par dépôt plus bas, jamais sur une somme ou un agrégat
    # d'horodatages. Les y remettre serait trompeur — un lecteur qui voit des
    # champs étoiles ou date sur `merged` en conclurait raisonnablement que la
    # croissance d'étoiles ou la fraîcheur en découlent, ce qui est
    # exactement l'erreur que les deux blocs ci-dessous existent pour éviter.
    merged = RepoStats(
        owner=live[0].owner,
        repo=f"<{len(live)} repos>",
        commits_4w=_sum_or_none([r.commits_4w for r in live]),
        commits_median_52w=_sum_or_none([r.commits_median_52w for r in live]),
        pr_merged_4w=_sum_or_none([r.pr_merged_4w for r in live]),
        pr_merged_52w=_sum_or_none([r.pr_merged_52w for r in live]),
    )

    # Fraîcheur : par dépôt, pas sur un `max(pushed_at)` agrégé. Le `max`
    # ferait gagner le dépôt le plus mal daté avant même de savoir si son
    # horodatage est crédible — un seul dépôt à l'horloge décalée écraserait
    # alors la fraîcheur mesurée de tous les dépôts sains du même coin.
    # `days_since_push` filtre déjà la gigue d'horloge au niveau d'un dépôt ;
    # on ne fait ici que garder le plus petit nombre de jours parmi les
    # dépôts dont l'horodatage a été cru, et ignorer les autres plutôt que de
    # laisser l'un d'eux invalider le lot.
    per_repo_freshness = [days_since_push(r, now) for r in live]
    believed = [f for f in per_repo_freshness if f is not None]
    freshness = min(believed) if believed else None
    # Un rejet individuel reste rapporté même quand un autre dépôt sauve la
    # fraîcheur du coin : c'est un signal d'observabilité pour l'appelant
    # (qui seul a le droit d'incrémenter un compteur), pas une condition sur
    # le résultat final.
    push_timestamp_rejected = any(
        r.pushed_at is not None and f is None
        for r, f in zip(live, per_repo_freshness, strict=True)
    )

    return CoinActivity(
        repo_count=len(live),
        commit_ratio_4w=commit_ratio(merged),
        pr_ratio_4w=pr_ratio(merged),
        days_since_push=freshness,
        # Plus de `now` : l'intervalle court d'un snapshot à l'autre, pas
        # jusqu'à l'horloge du cycle. Voir la note dans `_weighted_star_growth`
        # sur l'agrégation par dépôt plutôt que sur une somme d'étoiles.
        star_growth_pct_7d=_weighted_star_growth(live),
        all_repos_archived=False,
        push_timestamp_rejected=push_timestamp_rejected,
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
