"""Le cycle du collector GitHub: deux horloges dans une seule boucle.

Le service tourne toutes les 600 s parce que le FeatureStore de
ai-worker-haiku expire a 900 s. Mais l'API GitHub n'a pas besoin d'etre
interrogee a ce rythme. D'ou la dissociation testee ici: publication depuis le
cache a chaque tour, rafraichissement reseau en round-robin borne par un
budget. Le budget borne les telechargements, jamais le reporting.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from service_modules import load_service_module

RepoStats = load_service_module("collector-github", "domain.activity").RepoStats
GitHubCollector = load_service_module(
    "collector-github", "application.collector"
).GitHubCollector
RepoGoneError = load_service_module(
    "collector-github", "infrastructure.github_client"
).RepoGoneError

NOW = datetime(2026, 8, 2, tzinfo=UTC)


class _Producer:
    def __init__(self):
        self.published = []

    async def publish(self, topic, event):
        self.published.append(event)


class _Store:
    """Cache de snapshots en memoire, indexe par (owner, repo)."""

    def __init__(self, seeded=None):
        self.rows = dict(seeded or {})

    async def latest(self, owner, repo):
        return self.rows.get((owner, repo))

    async def save(self, stats):
        self.rows[(stats.owner, stats.repo)] = stats


def _stats(owner="aave", repo="core", **kw):
    base = {
        "stars": 1000,
        "pushed_at": NOW - timedelta(days=1),
        "archived": False,
        "is_fork": False,
        "commits_4w": 40,
        "commits_median_52w": 10.0,
        "pr_merged_4w": 8,
        "pr_merged_52w": 104,
        "stars_prev": 1000,
    }
    base.update(kw)
    return RepoStats(owner=owner, repo=repo, **base)


def _collector(producer, store, *, repo_map=None, fetch=None, budget=0):
    async def _fetch(owner, repo):
        return _stats(owner, repo)

    return GitHubCollector(
        producer=producer,
        store=store,
        fetch_repo=fetch or _fetch,
        repo_map=repo_map or (lambda: {"AAVE": ("aave", [("aave", "core")])}),
        clock=lambda: NOW,
        max_refresh_per_cycle=budget,
    )


def _map(n):
    return {f"S{i}": (f"c{i}", [("o", f"r{i}")]) for i in range(n)}


async def test_publishes_from_cache_every_cycle_even_without_refresh():
    """Le FeatureStore expire a 900 s: republier a chaque cycle, ou l'axe
    disparait 98% du temps."""
    producer = _Producer()
    collector = _collector(producer, _Store({("aave", "core"): _stats()}))
    await collector.poll_once()
    assert len(producer.published) == 1
    await collector.poll_once()
    assert len(producer.published) == 2


async def test_refresh_budget_bounds_fetches_not_reports():
    """Budget a 1 mais 3 depots en cache: 3 evenements, 1 seul appel reseau.

    L'inverse a deja ete paye sur les unlocks DefiLlama, ou un plafond
    applique a l'appartenance a la carte faisait declarer "aucun calendrier
    connu" a 37 tokens sur 40 dont le calendrier etait deja en cache.
    """
    calls = []

    async def fetch(owner, repo):
        calls.append((owner, repo))
        return _stats(owner, repo)

    producer = _Producer()
    store = _Store({("o", f"r{i}"): _stats("o", f"r{i}") for i in range(3)})
    collector = _collector(
        producer, store, repo_map=lambda: _map(3), fetch=fetch, budget=1
    )
    await collector.poll_once()
    assert len(producer.published) == 3
    assert len(calls) == 1


async def test_symbol_without_cached_snapshot_publishes_nothing():
    """Rien de mesure != tout a zero. Pas d'evenement plutot qu'un vide."""
    producer = _Producer()
    await _collector(producer, _Store()).poll_once()
    assert producer.published == []


async def test_refresh_cursor_rotates():
    """Sans rotation, les premiers depots affameraient les suivants."""
    calls = []

    async def fetch(owner, repo):
        calls.append(repo)
        return _stats(owner, repo)

    store = _Store({("o", f"r{i}"): _stats("o", f"r{i}") for i in range(3)})
    collector = _collector(
        _Producer(), store, repo_map=lambda: _map(3), fetch=fetch, budget=1
    )
    for _ in range(3):
        await collector.poll_once()
    assert sorted(calls) == ["r0", "r1", "r2"]


async def test_dead_repo_is_written_off_after_one_failure():
    """Un depot renomme ou passe prive coute un appel, pas un par cycle."""
    calls = []

    async def fetch(owner, repo):
        calls.append(repo)
        raise RepoGoneError(repo)

    collector = _collector(
        _Producer(),
        _Store(),
        repo_map=lambda: {"S": ("c", [("o", "gone")])},
        fetch=fetch,
        budget=5,
    )
    await collector.poll_once()
    await collector.poll_once()
    assert calls == ["gone"]


async def test_one_invalid_token_does_not_silence_the_others():
    """Une exception sur un token ne doit pas couter le cycle aux autres.

    Lecon de l'incident fees_24h_usd: une exception dans une boucle d'emission
    sans garde fait publier zero evenement pour *tous* les tokens du cycle.
    coin_id None fait lever DeveloperEvent, dont coin_id est un str requis.
    """
    producer = _Producer()
    store = _Store({("o", f"r{i}"): _stats("o", f"r{i}") for i in range(3)})
    collector = _collector(
        producer,
        store,
        repo_map=lambda: {
            "S0": (None, [("o", "r0")]),
            "S1": ("c1", [("o", "r1")]),
            "S2": ("c2", [("o", "r2")]),
        },
    )
    await collector.poll_once()
    assert {e.symbol for e in producer.published} == {"S1", "S2"}


async def test_a_broken_aggregate_does_not_silence_the_others():
    """aggregate() est pur mais pas total: un horodatage naif y leve
    TypeError. Hors du try, l'exception sortait de la boucle."""
    producer = _Producer()
    naive = _stats("o", "r0", pushed_at=datetime(2026, 8, 1))  # sans tzinfo
    store = _Store({("o", "r0"): naive, ("o", "r1"): _stats("o", "r1")})
    collector = _collector(
        producer,
        store,
        repo_map=lambda: {"S0": ("c0", [("o", "r0")]), "S1": ("c1", [("o", "r1")])},
    )
    await collector.poll_once()
    assert {e.symbol for e in producer.published} == {"S1"}


async def test_an_event_asserting_nothing_is_not_published():
    """Des depots existent mais aucune mesure n'a abouti (tous en 202).

    L'evenement serait valide au sens du schema et n'affirmerait rien: le
    FeatureStore n'y trouverait que dev_repo_count.
    """
    producer = _Producer()
    pending = _stats(
        "o",
        "r0",
        commits_4w=None,
        commits_median_52w=None,
        pr_merged_4w=None,
        pr_merged_52w=None,
        pushed_at=None,
    )
    collector = _collector(
        producer, _Store({("o", "r0"): pending}), repo_map=lambda: _map(1)
    )
    stats = await collector.poll_once()
    assert producer.published == []
    assert stats.skipped_no_measurement == 1


async def test_the_all_archived_zero_is_still_published():
    """ "Tout est archive" est une mesure, pas une absence: le seul zero
    legitime de la chaine ne doit pas etre filtre avec les evenements vides."""
    producer = _Producer()
    dead = _stats("o", "r0", archived=True)
    collector = _collector(
        producer, _Store({("o", "r0"): dead}), repo_map=lambda: _map(1)
    )
    await collector.poll_once()
    assert len(producer.published) == 1
    assert producer.published[0].all_repos_archived is True
    assert producer.published[0].repo_count == 0


async def test_pending_stats_are_counted_apart_from_failures():
    """202 n'est ni un succes ni une erreur: les confondre empecherait un
    operateur de distinguer "GitHub calcule" de "notre appel casse"."""

    async def fetch(owner, repo):
        return _stats(owner, repo, commits_4w=None, commits_median_52w=None)

    collector = _collector(
        _Producer(), _Store(), repo_map=lambda: _map(1), fetch=fetch, budget=1
    )
    stats = await collector.poll_once()
    assert stats.deferred == 1
    assert stats.failed == 0
    assert stats.refreshed == 1


async def test_an_unexpected_fetch_error_costs_one_repo_not_the_cycle():
    calls = []

    async def fetch(owner, repo):
        calls.append(repo)
        if repo == "r0":
            raise RuntimeError("boom")
        return _stats(owner, repo)

    store = _Store()
    collector = _collector(
        _Producer(), store, repo_map=lambda: _map(2), fetch=fetch, budget=2
    )
    stats = await collector.poll_once()
    assert sorted(calls) == ["r0", "r1"]
    assert stats.failed == 1
    assert stats.refreshed == 1
