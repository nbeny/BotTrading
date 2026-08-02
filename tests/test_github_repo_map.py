"""Rattachement coin -> depots: CoinGecko fait foi, les listes sont un appoint.

C'est ce module qui decide si l'axe mesure quelque chose ou l'invente. Sur les
~8 400 entrees cumulees des deux awesome-lists, la grande majorite sont des
bibliotheques sans token (ccxt, web3.py, OpenZeppelin): une promotion trop
genereuse rattacherait l'activite d'un outil generique a un coin au hasard.
"""

from __future__ import annotations

import httpx
from service_modules import load_service_module

ListEntry = load_service_module("collector-github", "domain.lists").ListEntry
_map = load_service_module("collector-github", "infrastructure.repo_map")
CoinGeckoRepos = _map.CoinGeckoRepos
promote_list_entries = _map.promote_list_entries


def _client(payload, *, status=200):
    def handler(request):
        return httpx.Response(status, json=payload)

    return CoinGeckoRepos(transport=httpx.MockTransport(handler))


async def test_reads_github_repos_from_links():
    repos = await _client(
        {"links": {"repos_url": {"github": ["https://github.com/aave/aave-v3-core"]}}}
    ).repos_for("aave")
    assert repos == [("aave", "aave-v3-core")]


async def test_absent_links_return_empty_not_none():
    """CoinGecko connait le coin mais ne liste aucun depot: c'est une reponse.

    L'appelant la distingue d'une exception, qui elle veut dire "on n'a pas pu
    demander" -- et qui ne doit pas faire ecrire une carte vide en base.
    """
    assert await _client({"links": {}}).repos_for("some-coin") == []


async def test_null_github_list_is_not_a_crash():
    """CoinGecko renvoie parfois `"github": null` plutot qu'une liste vide."""
    assert (
        await _client({"links": {"repos_url": {"github": None}}}).repos_for("x") == []
    )


async def test_non_github_urls_are_ignored():
    repos = await _client(
        {
            "links": {
                "repos_url": {"github": ["https://gitlab.com/x/y"], "bitbucket": []}
            }
        }
    ).repos_for("x")
    assert repos == []


async def test_subpaths_and_git_suffix_are_normalised():
    """Deux graphies du meme depot doivent donner une seule paire, sinon la
    carte porte deux lignes et le meme depot est interroge deux fois."""
    repos = await _client(
        {
            "links": {
                "repos_url": {
                    "github": [
                        "https://github.com/aave/aave-v3-core.git",
                        "https://github.com/aave/aave-v3-core/tree/main",
                    ]
                }
            }
        }
    ).repos_for("aave")
    assert repos == [("aave", "aave-v3-core")]


async def test_http_error_raises_rather_than_reporting_no_repos():
    """Un 429 n'est pas "ce coin n'a pas de depot": le confondre effacerait
    une carte correcte au premier rate-limit."""
    import pytest

    with pytest.raises(httpx.HTTPStatusError):
        await _client({}, status=429).repos_for("aave")


def _entry(name, owner, repo):
    return ListEntry(name=name, owner=owner, repo=repo, source_list="l")


def test_promotion_requires_unambiguous_lexicon_match():
    promoted = promote_list_entries(
        [_entry("aave", "aave", "aave-v3-core"), _entry("ccxt", "ccxt", "ccxt")],
        symbols_by_name={"aave": "AAVE"},
        homographs=frozenset(),
    )
    assert promoted == [("AAVE", "aave", "aave-v3-core")]


def test_homographs_are_never_promoted():
    """KEEP, FLOW, ONE: le lexicon les signale comme ambigus et exige une
    corroboration que le seul nom d'un depot ne fournit pas."""
    promoted = promote_list_entries(
        [_entry("keep", "keep-network", "keep-core")],
        symbols_by_name={"keep": "KEEP"},
        homographs=frozenset({"KEEP"}),
    )
    assert promoted == []


def test_promotion_is_case_insensitive_on_the_project_name():
    """Les listes ecrivent "Aave", "aave" et "AAVE" selon les entrees."""
    promoted = promote_list_entries(
        [_entry("Aave", "aave", "aave-v3-core")],
        symbols_by_name={"aave": "AAVE"},
        homographs=frozenset(),
    )
    assert promoted == [("AAVE", "aave", "aave-v3-core")]


def test_unmatched_entries_are_dropped_not_guessed():
    """Un projet dont le nom ne resout pas reste hors de la carte. Il demeure
    au registre avec symbol NULL, mais n'alimente aucun agregat."""
    promoted = promote_list_entries(
        [_entry("some-random-sdk", "acme", "sdk")],
        symbols_by_name={"aave": "AAVE"},
        homographs=frozenset(),
    )
    assert promoted == []


def test_promotion_deduplicates_the_same_repo_from_both_lists():
    """bitcoin figure dans les deux listes: une seule entree de carte."""
    promoted = promote_list_entries(
        [
            _entry("bitcoin", "bitcoin", "bitcoin"),
            _entry("bitcoin", "bitcoin", "bitcoin"),
        ],
        symbols_by_name={"bitcoin": "BTC"},
        homographs=frozenset(),
    )
    assert promoted == [("BTC", "bitcoin", "bitcoin")]


async def test_real_coingecko_url_shapes_are_parsed_not_split():
    """I7: le decoupage de chaine laissait passer plusieurs formes reelles.

    L'hote en capitales etait rejete alors qu'il est valide, les suffixes de
    requete et d'ancre entraient dans le nom du depot, et les chemins reserves
    produisaient des depots fantomes interroges indefiniment.
    """
    cases = {
        "https://GitHub.com/aave/aave-v3-core": ("aave", "aave-v3-core"),
        "https://github.com/aave/aave-v3-core?tab=readme": ("aave", "aave-v3-core"),
        "https://github.com/aave/aave-v3-core#readme": ("aave", "aave-v3-core"),
        "https://github.com/aave/aave-v3-core/tree/main": ("aave", "aave-v3-core"),
        "  https://github.com/aave/aave-v3-core  ": ("aave", "aave-v3-core"),
    }
    for url, expected in cases.items():
        got = await _client({"links": {"repos_url": {"github": [url]}}}).repos_for(
            "aave"
        )
        assert got == [expected], url


async def test_reserved_paths_and_gists_are_not_repos():
    """Un 404 permanent dans la boucle de sondage, pas une absence comptee."""
    for url in (
        "https://github.com/topics/solana",
        "https://github.com/orgs/aave/repositories",
        "https://github.com/sponsors/aave",
        "https://gist.github.com/vbuterin/deadbeef",
        "https://github.com/bitcoin",
    ):
        got = await _client({"links": {"repos_url": {"github": [url]}}}).repos_for("x")
        assert got == [], url


async def test_dedup_ignores_case_like_github_does():
    """I8: deux graphies du meme depot coutaient deux creneaux du round-robin
    et gonflaient repo_count."""
    got = await _client(
        {
            "links": {
                "repos_url": {
                    "github": [
                        "https://github.com/Uniswap/v3-core",
                        "https://github.com/uniswap/v3-core",
                    ]
                }
            }
        }
    ).repos_for("uniswap")
    assert got == [("Uniswap", "v3-core")]


def test_promotion_dedup_ignores_case_too():
    promoted = promote_list_entries(
        [
            _entry("bitcoin", "Bitcoin", "Bitcoin"),
            _entry("bitcoin", "bitcoin", "bitcoin"),
        ],
        symbols_by_name={"bitcoin": "BTC"},
        homographs=frozenset(),
    )
    assert len(promoted) == 1
