"""Ratios d'activité par dépôt — pur, synchrone, sans I/O.

Chaque fonction rapporte une mesure ou ``None``. Jamais un zéro de remplacement :
en aval, un axe absent est *exclu* de la renormalisation tandis qu'un axe mesuré
mauvais tire le score vers le bas. Un ``0.0`` fabriqué ici est donc une opinion
négative déguisée en observation.

Le seul zéro légitime est un zéro constaté : un dépôt dont la baseline annuelle
est positive et qui n'a rien produit en quatre semaines s'est réellement arrêté.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: Fenêtre récente, en semaines. La baseline est ramenée à cette même durée.
WINDOW_WEEKS = 4
WEEKS_PER_YEAR = 52


@dataclass(frozen=True, slots=True)
class RepoStats:
    """Ce qu'un cycle a pu lire d'un dépôt. Toute mesure est optionnelle."""

    owner: str
    repo: str
    stars: int | None = None
    forks: int | None = None
    pushed_at: datetime | None = None
    archived: bool = False
    is_fork: bool = False
    #: None tant que GitHub répond 202 sur /stats/commit_activity.
    commits_4w: int | None = None
    commits_median_52w: float | None = None
    pr_merged_4w: int | None = None
    pr_merged_52w: int | None = None
    #: Étoiles au snapshot précédent. None au premier passage.
    stars_prev: int | None = None


def _ratio(recent: int | None, expected: float | None) -> float | None:
    if recent is None or expected is None or expected <= 0:
        # expected <= 0 : le ratio est indéfini. Le rendre infini (ou 1.0 au
        # motif que « tout commit est une accélération ») inventerait une
        # lecture à partir d'une division impossible.
        return None
    return recent / expected


def commit_ratio(stats: RepoStats) -> float | None:
    """Commits des 4 dernières semaines rapportés au rythme habituel du dépôt.

    1.0 = le projet avance à sa vitesse de croisière annuelle. La médiane
    hebdomadaire, et non la moyenne, parce qu'un unique gros merge (import de
    vendor, reformatage) écrase une moyenne et rendrait tout le reste de l'année
    anormalement calme.
    """
    if stats.commits_median_52w is None:
        return None
    return _ratio(stats.commits_4w, stats.commits_median_52w * WINDOW_WEEKS)


def pr_ratio(stats: RepoStats) -> float | None:
    """PR mergées sur 4 semaines rapportées à la moyenne des 52 dernières."""
    if stats.pr_merged_52w is None:
        return None
    weekly = stats.pr_merged_52w / WEEKS_PER_YEAR
    return _ratio(stats.pr_merged_4w, weekly * WINDOW_WEEKS)


def days_since_push(stats: RepoStats, now: datetime) -> int | None:
    """Âge du dernier push, en jours.

    Un ``pushed_at`` dans le futur signale une dérive d'horloge ou une lecture
    API corrompue — pas un dépôt hyperactif. Ramener silencieusement ce cas à 0
    fabriquerait la lecture la plus favorable possible (« poussé il y a 0
    jour ») à partir d'une donnée à laquelle on ne peut pas faire confiance :
    exactement l'anti-motif que ce module évite partout ailleurs. On rapporte
    donc l'absence plutôt qu'une valeur inventée.
    """
    if stats.pushed_at is None:
        return None
    delta_days = (now - stats.pushed_at).days
    if delta_days < 0:
        return None
    return delta_days


def star_growth_pct(stats: RepoStats) -> float | None:
    """Croissance relative des étoiles depuis le snapshot précédent.

    ``None`` au premier passage : un delta demande deux observations, et un 0.0
    y affirmerait une stagnation qu'on n'a pas observée.
    """
    if stats.stars is None or stats.stars_prev is None or stats.stars_prev <= 0:
        return None
    return (stats.stars - stats.stars_prev) / stats.stars_prev
