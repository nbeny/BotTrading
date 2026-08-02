"""Le client GitHub: les trois echecs silencieux de l'API, sans reseau."""

from __future__ import annotations

import statistics

import httpx
import pytest
from service_modules import load_service_module

client_mod = load_service_module("collector-github", "infrastructure.github_client")
GitHubClient = client_mod.GitHubClient
RepoGoneError = client_mod.RepoGoneError
recent_commits = client_mod.recent_commits
weekly_median = client_mod.weekly_median


def _client(handler):
    return GitHubClient(token="t", transport=httpx.MockTransport(handler))


async def test_commit_activity_returns_none_while_github_computes():
    """202 = statistiques en cours de calcul. None, jamais une liste vide.

    Rendre [] ou 0 affirmerait "ce depot n'a pas commite de l'annee", ce qui
    est une tout autre declaration que "GitHub n'a pas fini de compter".
    """
    result = await _client(lambda r: httpx.Response(202, content=b"")).commit_activity(
        "a", "b"
    )
    assert result is None


async def test_commit_activity_returns_weekly_totals():
    weeks = [{"total": n, "week": 0, "days": []} for n in range(52)]
    result = await _client(lambda r: httpx.Response(200, json=weeks)).commit_activity(
        "a", "b"
    )
    assert result is not None
    assert len(result) == 52
    assert result[-1] == 51


async def test_empty_list_is_none_not_zero_activity():
    """GitHub renvoie [] pour un depot vide *et* parfois pendant le calcul:
    les deux sont indiscernables a la lecture, donc on ne tranche pas."""
    result = await _client(lambda r: httpx.Response(200, json=[])).commit_activity(
        "a", "b"
    )
    assert result is None
    assert result != 0


async def test_404_raises_repo_gone():
    handler = lambda r: httpx.Response(404, json={"message": "Not Found"})  # noqa: E731
    with pytest.raises(RepoGoneError):
        await _client(handler).repo("a", "b")


async def test_repo_reads_metadata():
    payload = {
        "stargazers_count": 100,
        "forks_count": 5,
        "pushed_at": "2026-08-01T00:00:00Z",
        "archived": False,
        "fork": False,
    }
    meta = await _client(lambda r: httpx.Response(200, json=payload)).repo("a", "b")
    assert meta.stars == 100
    assert meta.forks == 5
    assert meta.archived is False
    assert meta.is_fork is False
    assert meta.pushed_at is not None
    assert meta.pushed_at.year == 2026
    assert meta.pushed_at.tzinfo is not None


async def test_repo_absent_counts_stay_none():
    """Un champ absent de la reponse reste None, pas 0: le scoring exclut un
    axe absent mais penalise un axe mesure a zero."""
    meta = await _client(lambda r: httpx.Response(200, json={})).repo("a", "b")
    assert meta.stars is None
    assert meta.forks is None
    assert meta.pushed_at is None


async def test_merged_pr_count_uses_search():
    seen: dict[str, str] = {}

    def handler(request):
        # Le parametre decode, pas l'URL brute: httpx percent-encode les ':'
        # de la syntaxe de recherche GitHub.
        seen["q"] = request.url.params["q"]
        seen["path"] = request.url.path
        return httpx.Response(200, json={"total_count": 42})

    count = await _client(handler).merged_pr_count("a", "b", since_days=28)
    assert count == 42
    assert seen["path"] == "/search/issues"
    assert "is:pr" in seen["q"]
    assert "is:merged" in seen["q"]
    assert "repo:a/b" in seen["q"]


async def test_merged_pr_window_is_the_requested_one():
    """since_days doit reellement borner la recherche: une fenetre figee
    ferait comparer 4 semaines de PR a une baseline de 4 semaines aussi,
    donc un ratio de 1.0 pour tout le monde."""
    seen: dict[str, str] = {}

    def handler(request):
        seen.setdefault("queries", "")
        seen["queries"] += request.url.params["q"] + "\n"
        return httpx.Response(200, json={"total_count": 1})

    client = _client(handler)
    await client.merged_pr_count("a", "b", since_days=28)
    await client.merged_pr_count("a", "b", since_days=364)
    first, second = seen["queries"].strip().split("\n")
    assert first != second


async def test_merged_pr_count_is_none_without_total():
    """Reponse sans total_count: on n'a pas mesure, on ne dit pas zero."""
    count = await _client(lambda r: httpx.Response(200, json={})).merged_pr_count(
        "a", "b", since_days=28
    )
    assert count is None
    assert count != 0


async def test_search_and_core_quotas_are_tracked_separately():
    """L'API /search a son propre seau de 30/min, distinct des 5000/h du
    coeur REST. Les melanger fait passer le service pour dans les clous
    jusqu'au premier 403 de rate-limit secondaire."""
    client = _client(lambda r: httpx.Response(200, json={"total_count": 1}))
    assert client.search_limiter is not client.core_limiter


async def test_search_uses_the_search_bucket_and_repo_uses_the_core_one():
    """Le seau separe ne sert a rien si les appels n'y sont pas routes."""
    client = _client(lambda r: httpx.Response(200, json={"total_count": 1}))
    before_core = client.core_limiter.acquisitions
    before_search = client.search_limiter.acquisitions
    await client.merged_pr_count("a", "b", since_days=28)
    assert client.search_limiter.acquisitions == before_search + 1
    assert client.core_limiter.acquisitions == before_core


async def test_server_error_raises_rather_than_reporting_absence():
    """Un 500 n'est pas une mesure absente: il doit remonter, sinon
    l'appelant le confondrait avec un depot sans activite."""
    handler = lambda r: httpx.Response(500, json={})  # noqa: E731
    with pytest.raises(httpx.HTTPStatusError):
        await _client(handler).repo("a", "b")


def test_weekly_median_excludes_the_recent_window():
    """Les 4 dernieres semaines sont la fenetre *recente*: les inclure dans
    leur propre baseline amortit exactement le mouvement qu'on cherche.

    La serie est courte et tranchee a dessein. Avec [10]*48 + [100]*4 la
    mediane vaut 10 qu'on exclue ou non la fenetre recente -- la mediane est
    trop robuste pour que ce jeu discrimine, et le test passait alors meme
    que l'exclusion etait supprimee.
    """
    weeks = [1, 1, 1, 1, 100, 100, 100, 100]
    assert weekly_median(weeks) == 1.0  # mediane des 4 premieres seulement
    # Sans l'exclusion, la mediane des 8 vaudrait 50.5: le sursaut recent
    # servirait de reference a lui-meme et le ratio retomberait vers 1.
    assert statistics.median(weeks) == 50.5


def test_weekly_median_is_none_on_a_young_repo():
    """Moins de 8 semaines d'historique: pas de baseline annuelle.

    C'est ce seuil qui, combine a celui de recent_commits (4 semaines), fait
    qu'un depot de 4 a 7 semaines a un numerateur sans denominateur.
    """
    assert weekly_median([1] * 7) is None
    assert weekly_median(None) is None


def test_recent_commits_sums_the_last_four_weeks():
    assert recent_commits([1] * 48 + [10, 20, 30, 40]) == 100


def test_recent_commits_is_none_on_too_short_a_series():
    assert recent_commits([1, 2, 3]) is None
    assert recent_commits(None) is None
