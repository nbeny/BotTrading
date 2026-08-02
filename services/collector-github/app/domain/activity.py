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

#: Durée sur laquelle `star_growth_pct` normalise son résultat, pour matcher
#: le champ événement `star_growth_pct_7d` et le seuil de scoring qui le lit.
STAR_GROWTH_NORMALISATION_WINDOW = timedelta(days=7)

#: En dessous de cet intervalle entre deux snapshots, extrapoler à 7 jours
#: multiplierait le bruit par un facteur ~168 (7 j / <1 h) : on n'a pas encore
#: assez de recul pour affirmer quoi que ce soit sur un taux. Une heure est le
#: bon ordre de grandeur au vu du cycle round-robin de 12h — largement en
#: dessous du plus petit intervalle réel entre deux relevés, donc sans jamais
#: rejeter une mesure légitime.
MIN_STAR_GROWTH_INTERVAL = timedelta(hours=1)


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
    #: Horodatage de ce snapshot précédent. Sans lui, un delta ne peut pas
    #: être ramené à un taux : la cadence round-robin fait varier l'intervalle
    #: (un repo qui rate son tour sur un 202 ou une erreur saute une fenêtre).
    stars_prev_at: datetime | None = None
    #: Horodatage de *ce* relevé de `stars` — pas l'horloge du cycle qui le
    #: publie. La publication tourne toutes les 600s pour rafraîchir le TTL du
    #: FeatureStore avec la *même* mesure, alors que le relevé lui-même ne
    #: change qu'au tour round-robin (~12h) ; dater l'intervalle sur `now`
    #: ferait dériver la même mesure vers le bas à chaque republication.
    stars_at: datetime | None = None


def _ratio(recent: int | None, expected: float | None) -> float | None:
    # `expected is None` est mort dans les deux appels actuels : les deux
    # appelants calculent sa valeur seulement après avoir écarté l'absence de
    # leur propre baseline. La branche reste quand même : `mypy` ne tourne
    # nulle part dans ce repo (`make lint` agrège `libs` et `services` et
    # échoue sur "Duplicate module named app" avant d'analyser un seul
    # fichier, et il n'y a aucun job de lint en CI), donc ce `if` est la
    # seule vérification qui existe réellement contre une régression d'appel.
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

    ``now`` doit être *aware* (avec fuseau), comme ``pushed_at`` : soustraire
    un naïf d'un aware lève ``TypeError``. C'est le cas de tous les appelants
    de production ; ce n'est simplement pas vérifié ici.
    """
    if stats.pushed_at is None:
        return None
    delta = now - stats.pushed_at
    if delta < -CLOCK_SKEW_TOLERANCE:
        return None
    return max(0, delta.days)


def star_growth_pct(stats: RepoStats) -> float | None:
    """Croissance des étoiles entre deux snapshots, normalisée sur 7 jours.

    ``None`` au premier passage : un delta demande deux observations, et un 0.0
    y affirmerait une stagnation qu'on n'a pas observée.

    L'intervalle court entre les deux *relevés* (``stars_at`` et
    ``stars_prev_at``), jamais jusqu'à l'horloge du cycle qui publie la
    mesure : la publication tourne toutes les 600s pour rafraîchir le TTL du
    FeatureStore avec la même mesure, tandis que le relevé lui-même ne change
    qu'au tour round-robin (~12h, variable — un dépôt qui répond 202 ou en
    erreur saute son tour). Dater sur l'horloge du cycle ferait dériver une
    mesure inchangée vers le bas à chaque republication, jusqu'à diviser sa
    valeur par 2 en un seul cycle de rafraîchissement : le même problème que
    ce module évite ailleurs, mais qui se rejouerait dans le temps plutôt
    qu'entre dépôts.

    Le nom du champ événement (``star_growth_pct_7d``) et le seuil de scoring
    (``0.3 + 0.7·clamp(growth / 0.02, 0, 1)``, calibré pour 2 % sur 7 jours)
    supposent tous deux cette durée. Sans normalisation, un delta mesuré sur
    ~12h serait comparé à un seuil pensé pour 7 jours — environ 14 fois trop
    petit — et pousserait systématiquement ce sous-signal vers le bas de sa
    bande plutôt que de l'exclure : pire qu'une absence.
    """
    if stats.stars is None or stats.stars_prev is None or stats.stars_prev <= 0:
        return None
    if stats.stars_prev_at is None or stats.stars_at is None:
        return None
    interval = stats.stars_at - stats.stars_prev_at
    if interval < MIN_STAR_GROWTH_INTERVAL:
        # Couvre aussi bien l'intervalle trop court (peu de recul, bruit
        # amplifié ~168x en extrapolant à 7 jours) que l'intervalle nul ou
        # négatif (horodatage incohérent) : les deux rendent le taux indéfini.
        return None
    raw_growth = (stats.stars - stats.stars_prev) / stats.stars_prev
    return raw_growth * (STAR_GROWTH_NORMALISATION_WINDOW / interval)
