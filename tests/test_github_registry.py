"""Fusion des deux listes vers le registre de projets.

Les deux se recouvrent partiellement -- Bitcoin Core figure dans les deux -- et
ne publient pas la meme chose: seule awesome-crypto porte une URL de site
officiel. La fusion est donc asymetrique par necessite, et jamais inventive.
"""

from __future__ import annotations

from service_modules import load_service_module

ListEntry = load_service_module("collector-github", "domain.lists").ListEntry
_registry = load_service_module("collector-github", "application.registry")
merge_entries = _registry.merge_entries


def _entry(name, owner, repo, homepage=None, description=None, source="best-of-crypto"):
    return ListEntry(
        name=name,
        owner=owner,
        repo=repo,
        homepage_url=homepage,
        description=description,
        source_list=source,
    )


def _awesome(**kw):
    kw.setdefault("source", "awesome-crypto")
    return _entry(**kw)


def test_same_repo_in_both_lists_is_one_row():
    merged = merge_entries(
        [_entry("bitcoin", "bitcoin", "bitcoin")],
        [_awesome(name="bitcoin", owner="bitcoin", repo="bitcoin")],
    )
    assert len(merged) == 1


def test_homepage_from_awesome_fills_the_gap_best_of_leaves():
    """best-of n'en publie pas; la fusion ne doit pas ecraser celle d'awesome
    sous pretexte que best-of est passe en premier."""
    merged = merge_entries(
        [_entry("bitcoin", "bitcoin", "bitcoin")],
        [
            _awesome(
                name="bitcoin",
                owner="bitcoin",
                repo="bitcoin",
                homepage="https://bitcoincore.org",
            )
        ],
    )
    assert merged[0].homepage_url == "https://bitcoincore.org"


def test_a_present_value_is_never_overwritten_by_an_absent_one():
    """La regle est "la valeur presente gagne sur l'absente", pas "awesome
    gagne": ecrite ainsi, la fusion reste correcte si les listes changent de
    contenu."""
    merged = merge_entries(
        [_entry("x", "o", "r", homepage="https://x.dev", description="desc")],
        [_awesome(name="x", owner="o", repo="r")],
    )
    assert merged[0].homepage_url == "https://x.dev"
    assert merged[0].description == "desc"


def test_absent_homepage_stays_none():
    """Aucune des deux ne la publie: None, pas une URL deduite du depot."""
    merged = merge_entries([_entry("ccxt", "ccxt", "ccxt")], [])
    assert merged[0].homepage_url is None


def test_source_list_records_both_when_present():
    merged = merge_entries(
        [_entry("bitcoin", "bitcoin", "bitcoin")],
        [_awesome(name="bitcoin", owner="bitcoin", repo="bitcoin")],
    )
    assert merged[0].source_list == "best-of-crypto,awesome-crypto"


def test_source_list_is_not_duplicated_on_a_repeated_entry():
    merged = merge_entries(
        [_entry("x", "o", "r"), _entry("x", "o", "r")],
        [],
    )
    assert merged[0].source_list == "best-of-crypto"


def test_entries_unique_to_one_list_survive():
    merged = merge_entries(
        [_entry("a", "o", "a")], [_awesome(name="b", owner="o", repo="b")]
    )
    assert {e.repo for e in merged} == {"a", "b"}


def test_merge_is_case_insensitive_like_github():
    """Bitcoin/Bitcoin et bitcoin/bitcoin sont un seul depot: deux lignes
    couteraient deux creneaux du round-robin et gonfleraient repo_count."""
    merged = merge_entries(
        [_entry("bitcoin", "Bitcoin", "Bitcoin")],
        [_awesome(name="bitcoin", owner="bitcoin", repo="bitcoin")],
    )
    assert len(merged) == 1


def test_merge_preserves_the_first_seen_spelling():
    """La cle est insensible a la casse, mais l'URL stockee garde la graphie
    d'origine: on deduplique sans reecrire ce que la source publie."""
    merged = merge_entries([_entry("x", "MyOrg", "MyRepo")], [])
    assert merged[0].github_url == "https://github.com/MyOrg/MyRepo"
