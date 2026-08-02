"""Reconstruction d'un RepoStats depuis les deux dernieres lignes de snapshot.

C'est ici que se decide ce que valent stars_prev et stars_prev_at, et un
decalage d'une ligne y produirait une croissance d'etoiles fausse mais
parfaitement plausible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from service_modules import load_service_module

stats_from_rows = load_service_module(
    "collector-github", "infrastructure.store"
).stats_from_rows

NOW = datetime(2026, 8, 2, tzinfo=UTC)
EARLIER = NOW - timedelta(days=7)


def _row(stars, observed_at, **kw):
    base = {
        "stars": stars,
        "forks": 5,
        "observed_at": observed_at,
        "pushed_at": observed_at,
        "archived": False,
        "is_fork": False,
        "commits_4w": 40,
        "commits_median_52w": 10.0,
        "pr_merged_4w": 8,
        "pr_merged_52w": 104,
    }
    base.update(kw)
    return base


def test_previous_stars_come_from_the_older_row():
    """Les lignes arrivent du plus recent au plus ancien."""
    stats = stats_from_rows("o", "r", [_row(1100, NOW), _row(1000, EARLIER)])
    assert stats.stars == 1100
    assert stats.stars_prev == 1000


def test_both_observation_times_are_carried():
    """star_growth_pct divise par stars_at - stars_prev_at: sans les deux
    instants, le delta ne peut pas etre ramene a un taux."""
    stats = stats_from_rows("o", "r", [_row(1100, NOW), _row(1000, EARLIER)])
    assert stats.stars_at == NOW
    assert stats.stars_prev_at == EARLIER


def test_single_row_leaves_previous_none():
    """Un delta demande deux observations; 0 inventerait une stagnation."""
    stats = stats_from_rows("o", "r", [_row(1000, NOW)])
    assert stats.stars_prev is None
    assert stats.stars_prev_at is None


def test_no_rows_returns_none():
    """Aucun snapshot != un snapshot vide."""
    assert stats_from_rows("o", "r", []) is None


def test_measures_come_from_the_current_row_not_the_previous_one():
    """Seules les etoiles regardent en arriere; tout le reste est la lecture
    courante. Melanger les deux daterait les commits d'une semaine."""
    stats = stats_from_rows(
        "o",
        "r",
        [_row(1100, NOW, commits_4w=99), _row(1000, EARLIER, commits_4w=1)],
    )
    assert stats.commits_4w == 99


def test_absent_measures_stay_none():
    """Une ligne partielle (202 au moment du releve) ne devient pas zero."""
    stats = stats_from_rows(
        "o", "r", [_row(1000, NOW, commits_4w=None, commits_median_52w=None)]
    )
    assert stats.commits_4w is None
    assert stats.commits_median_52w is None


def test_identity_is_taken_from_the_arguments_not_the_rows():
    stats = stats_from_rows("owner", "repo", [_row(1000, NOW)])
    assert (stats.owner, stats.repo) == ("owner", "repo")


def test_the_service_entrypoint_imports():
    """Garde-fou de cablage: main.py assemble neuf modules et n'est couvert
    par aucun autre test. Une signature qui derive s'y voit a l'import, pas au
    demarrage du conteneur en production."""
    main = load_service_module("collector-github", "main")
    assert main.app is not None
    assert main.POLL_INTERVAL == 600.0
    assert main.MAX_REFRESH >= 1
