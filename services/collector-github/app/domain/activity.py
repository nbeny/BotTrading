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
from datetime import datetime, timedelta

#: Fenêtre récente, en semaines. La baseline est ramenée à cette même durée.
WINDOW_WEEKS = 4
WEEKS_PER_YEAR = 52

#: Tolérance de dérive d'horloge, pas une règle métier : un décalage NTP de
#: quelques secondes à quelques minutes entre notre horloge et celle de GitHub
#: est la norme, pas une anomalie, et il touche précisément les dépôts qui
#: viennent de pousser — les plus actifs, ceux que ce signal existe pour
#: repérer. Au-delà de cette fenêtre, l'avance n'est plus de la gigue mais un
#: horodatage auquel on ne peut plus faire confiance.
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)


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

    Un ``pushed_at`` dans le futur au-delà de ``CLOCK_SKEW_TOLERANCE`` signale
    une dérive d'horloge ou une lecture API corrompue — pas un dépôt
    hyperactif. Ramener silencieusement ce cas à 0 fabriquerait la lecture la
    plus favorable possible (« poussé il y a 0 jour ») à partir d'une donnée à
    laquelle on ne peut pas faire confiance : exactement l'anti-motif que ce
    module évite partout ailleurs. On rapporte donc l'absence plutôt qu'une
    valeur inventée — mais seulement une fois la tolérance de gigue d'horloge
    dépassée ; en-deçà, une avance de quelques secondes ou minutes est un
    dépôt qui vient tout juste d'être poussé, et vaut 0.
    """
    if stats.pushed_at is None:
        return None
    delta = now - stats.pushed_at
    if delta < -CLOCK_SKEW_TOLERANCE:
        return None
    return max(0, delta.days)


def star_growth_pct(stats: RepoStats) -> float | None:
    """Croissance relative des étoiles depuis le snapshot précédent.

    ``None`` au premier passage : un delta demande deux observations, et un 0.0
    y affirmerait une stagnation qu'on n'a pas observée.
    """
    if stats.stars is None or stats.stars_prev is None or stats.stars_prev <= 0:
        return None
    return (stats.stars - stats.stars_prev) / stats.stars_prev
