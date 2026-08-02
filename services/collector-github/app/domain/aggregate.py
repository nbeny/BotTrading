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
    #: Fraîcheur en jours, la plus petite valeur crue parmi les dépôts vivants.
    days_since_push: int | None
    star_growth_pct_7d: float | None
    all_repos_archived: bool
    #: Au moins un dépôt avait un horodatage, mais il a été rejeté (horloge
    #: décalée). Signal d'observabilité pour l'appelant, qui seul a le droit
    #: d'incrémenter un compteur — voir le calcul dans `aggregate()`.
    push_timestamp_rejected: bool = False

    @property
    def has_measurement(self) -> bool:
        """Vrai si au moins une des quatre mesures a été effectivement lue.

        ``all_repos_archived=True`` doit rester une mesure ici, pas une
        absence : ses zéros (``commit_ratio_4w=0.0``, ``pr_ratio_4w=0.0``)
        sont un constat — « on a regardé, tout est mort » — au même titre
        qu'une croissance ou une fraîcheur positive. Un prédicat fondé sur la
        vérité booléenne des champs (``0.0`` est *falsy*) laisserait tomber
        ce seul zéro légitime de toute la chaîne ; d'où la clause explicite
        plutôt qu'un simple ``any(... is not None ...)`` sur les quatre
        champs, qui suppose sans le garantir que la branche « tout archivé »
        continuera à les poser à ``0.0``.
        """
        return (
            self.all_repos_archived
            or self.commit_ratio_4w is not None
            or self.pr_ratio_4w is not None
            or self.days_since_push is not None
            or self.star_growth_pct_7d is not None
        )


def _is_live(stats: RepoStats) -> bool:
    """Un dépôt archivé ou forké n'est pas une mesure d'activité à zéro.

    Il est hors sujet : un miroir n'a pas vocation à commiter et un dépôt
    archivé annonce lui-même qu'il ne bougera plus. Les compter à zéro
    diluerait l'activité réelle du projet proportionnellement au nombre de
    miroirs qu'il traîne.
    """
    # `is True` et non une simple veracite : `archived` peut valoir None quand
    # la fiche du depot n'a pas ete lue, et un `not None` vaudrait True — donc
    # « vivant », affirme depuis une absence de mesure. Un depot dont on ignore
    # le statut est exclu, pas presume vivant.
    return stats.archived is not True and stats.is_fork is not True


def _paired_sums(
    pairs: Sequence[tuple[int | float, int | float]],
) -> tuple[int | float | None, int | float | None]:
    """Somme le numérateur et le dénominateur sur le *même* sous-ensemble.

    Ne jamais sommer les deux côtés d'un ratio indépendamment : Task 5 exige
    ``len(weeks) >= 4`` pour ``commits_4w`` mais ``>= 8`` pour
    ``commits_median_52w`` à partir du même appel API, donc un dépôt jeune de
    4 à 7 semaines a des commits mesurés sans médiane. Sommer chaque champ
    sur son propre sous-ensemble de dépôts présents ferait alors comparer les
    commits récents de ce dépôt à la baseline d'un *autre* dépôt — une valeur
    qui n'a jamais été mesurée sur aucun repo. Côté PR c'est pire :
    ``pr_merged_4w`` et ``pr_merged_52w`` viennent de deux appels
    ``/search/issues`` indépendants sur le seau de recherche, fragile aux
    limites de débit, donc l'un des deux peut manquer sans l'autre.

    Un dépôt qui n'a que la moitié de la paire sort donc de l'agrégat pour ce
    ratio entier, pas seulement du côté qui manque : on ne peut pas dire si
    ses commits sont rapides ou lents sans sa propre baseline. Même position
    que ``_ratio`` sur ``expected <= 0``.
    """
    if not pairs:
        return None, None
    return sum(n for n, _ in pairs), sum(d for _, d in pairs)


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
    #
    # Numérateur et dénominateur de chaque ratio viennent du même
    # sous-ensemble de dépôts (voir `_paired_sums`) : les sommer
    # indépendamment ferait comparer les commits récents d'un dépôt jeune,
    # sans médiane annuelle encore disponible, à la baseline d'un tout autre
    # dépôt.
    commit_pairs = [
        (r.commits_4w, r.commits_median_52w)
        for r in live
        if r.commits_4w is not None and r.commits_median_52w is not None
    ]
    pr_pairs = [
        (r.pr_merged_4w, r.pr_merged_52w)
        for r in live
        if r.pr_merged_4w is not None and r.pr_merged_52w is not None
    ]
    commits_4w, commits_median_52w = _paired_sums(commit_pairs)
    pr_merged_4w, pr_merged_52w = _paired_sums(pr_pairs)
    merged = RepoStats(
        owner=live[0].owner,
        repo=f"<{len(live)} repos>",
        commits_4w=commits_4w,
        commits_median_52w=commits_median_52w,
        pr_merged_4w=pr_merged_4w,
        pr_merged_52w=pr_merged_52w,
    )

    # Fraîcheur : par dépôt, pas sur un `max(pushed_at)` agrégé. Le `max`
    # ferait gagner le dépôt le plus mal daté avant même de savoir si son
    # horodatage est crédible — un seul dépôt à l'horloge décalée écraserait
    # alors la fraîcheur mesurée de tous les dépôts sains du même coin, jetant
    # une donnée correcte pour éviter d'en publier une fausse, alors
    # qu'écarter le seul dépôt fautif suffit. `days_since_push` filtre déjà
    # la gigue d'horloge au niveau d'un dépôt ; on ne garde ici que le plus
    # petit nombre de jours parmi les dépôts dont l'horodatage a été cru.
    #
    # Dépôt et fraîcheur calculés dans la même compréhension pour que le
    # pairage ne puisse pas se décaler — deux listes construites séparément
    # puis recombinées par `zip` sont un ordre implicite qu'un futur tri ou
    # filtre casserait silencieusement.
    per_repo_freshness = [(r, days_since_push(r, now)) for r in live]
    believed = [f for _, f in per_repo_freshness if f is not None]
    freshness = min(believed) if believed else None
    # Un rejet individuel reste rapporté même quand un autre dépôt sauve la
    # fraîcheur du coin : c'est un signal d'observabilité pour l'appelant
    # (qui seul a le droit d'incrémenter un compteur), pas une condition sur
    # le résultat final.
    push_timestamp_rejected = any(
        r.pushed_at is not None and f is None for r, f in per_repo_freshness
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

    Prend ``live`` (dépôts vivants), jamais la liste brute des dépôts du
    coin : un fork aux relevés d'étoiles exploitables mais à la base
    massive dominerait sinon la moyenne pondérée d'un projet dont il n'est
    qu'un miroir.
    """
    # Le filtre `stars_prev > 0` est mort au sens strict : `star_growth_pct`
    # rejette déjà `stars_prev is None` ou `<= 0` et rend `None` dans les
    # deux cas, donc `rate` serait de toute façon absent sans lui. Il reste
    # écrit pour une raison locale : c'est la preuve que `total` plus bas est
    # strictement positif, sans laquelle rien n'empêcherait un poids nul ou
    # négatif de s'infiltrer dans la moyenne pondérée. Même choix que la
    # branche `expected is None` de `_ratio`, elle aussi morte mais gardée
    # pour la même raison — `mypy` ne tourne nulle part dans ce dépôt, donc
    # ce genre de garde reste la seule protection qui s'exécute réellement.
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
