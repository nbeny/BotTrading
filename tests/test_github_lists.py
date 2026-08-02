# tests/test_github_lists.py
from pathlib import Path

from service_modules import load_service_module

_lists = load_service_module("collector-github", "domain.lists")
ListEntry = _lists.ListEntry
parse_awesome_crypto = _lists.parse_awesome_crypto
parse_best_of_crypto = _lists.parse_best_of_crypto
parse_awesome_crypto_full = _lists.parse_awesome_crypto_full
parse_best_of_crypto_full = _lists.parse_best_of_crypto_full

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_best_of_extracts_repos():
    entries = parse_best_of_crypto(_read("best_of_crypto_sample.md"))
    by_repo = {(e.owner, e.repo): e for e in entries}
    assert ("bitcoin", "bitcoin") in by_repo
    assert ("ethereum", "go-ethereum") in by_repo


def test_best_of_has_no_homepage():
    """La liste n'en publie pas. None, pas une URL devinee depuis le repo."""
    entries = parse_best_of_crypto(_read("best_of_crypto_sample.md"))
    assert all(e.homepage_url is None for e in entries)


def test_best_of_deduplicates_summary_and_body_links():
    """Chaque entree porte deux fois la meme URL; une seule doit sortir."""
    entries = parse_best_of_crypto(_read("best_of_crypto_sample.md"))
    assert len(entries) == 2


def test_best_of_deduplicates_a_project_listed_in_two_categories():
    """bitcoin est range dans Cryptocurrencies *et* Smart Contract Platforms.

    Sans le filtre `seen`, le registre porterait deux lignes pour un meme
    depot -- et comme il deduplique ensuite sur github_url, la seconde
    ecraserait la premiere en base plutot que de faire echouer quoi que ce
    soit. Le fixture contient donc deliberement le doublon.
    """
    entries = parse_best_of_crypto(_read("best_of_crypto_sample.md"))
    bitcoins = [e for e in entries if (e.owner, e.repo) == ("bitcoin", "bitcoin")]
    assert len(bitcoins) == 1


def test_a_project_without_a_description_does_not_inherit_its_metrics_line():
    """`tagged-only` n'a aucune description: seulement des liens et des tags.

    Sans la borne sur le premier lien, il heritait de "5 stars per week over
    10 weeks" -- une ligne de compteurs presentee comme la description
    editoriale du projet dans le registre. Valeur inventee, plausible, et dans
    un champ que personne n'aurait recoupe.
    """
    entries = {
        e.repo: e for e in parse_awesome_crypto(_read("awesome_crypto_sample.md"))
    }
    assert entries["tagged-only"].description is None


def test_awesome_extracts_homepage_when_present():
    entries = {
        e.repo: e for e in parse_awesome_crypto(_read("awesome_crypto_sample.md"))
    }
    assert entries["bitcoin"].homepage_url == "https://bitcoincore.org/en/download"


def test_awesome_homepage_is_none_when_only_github_line():
    """La ligne site est absente pour solana: None, pas l'URL GitHub recopiee."""
    entries = {
        e.repo: e for e in parse_awesome_crypto(_read("awesome_crypto_sample.md"))
    }
    assert entries["solana"].homepage_url is None
    assert entries["solana"].owner == "solana-labs"


def test_unparseable_entries_are_counted_not_guessed():
    """Un format qui change doit se voir, pas retrecir le registre en silence."""
    result = parse_best_of_crypto_full(_read("best_of_crypto_sample.md"))
    assert all(isinstance(e, ListEntry) for e in result.entries)
    assert not any(e.name == "broken-entry-without-link" for e in result.entries)
    # Le bloc sans lien est *compte*, pas seulement ignore: un README dont le
    # format change doit faire monter ce compteur plutot que faire retrecir le
    # registre sans bruit. Sans cette assertion, remplacer `unparsed += 1` par
    # `pass` passerait inapercu.
    assert result.unparsed == 1


def test_github_url_is_rebuilt_from_owner_and_repo():
    """Le registre deduplique sur github_url: il doit etre canonique."""
    entry = parse_best_of_crypto(_read("best_of_crypto_sample.md"))[0]
    assert entry.github_url == "https://github.com/bitcoin/bitcoin"


def test_awesome_counts_no_unparsed_on_a_clean_fixture():
    """Contrepartie du compteur: il ne doit pas monter sur du valide."""
    assert parse_awesome_crypto_full(_read("awesome_crypto_sample.md")).unparsed == 0


def test_source_list_is_recorded_per_entry():
    """Le registre trace d'ou vient chaque projet; les deux listes ne
    publient pas la meme chose, donc l'origine n'est pas cosmetique."""
    best = parse_best_of_crypto(_read("best_of_crypto_sample.md"))
    awesome = parse_awesome_crypto(_read("awesome_crypto_sample.md"))
    assert {e.source_list for e in best} == {"best-of-crypto"}
    assert {e.source_list for e in awesome} == {"awesome-crypto"}


def test_repo_url_does_not_capture_subpaths():
    """Les blocs best-of contiennent aussi des liens /blob/ et /issues/ ;
    une regex trop laxiste en ferait des depots fantomes."""
    markdown = (
        "<details><summary><b>"
        '<a href="https://github.com/foo/bar/blob/main/README.md">bar</a>'
        "</b></summary></details>"
    )
    entries = parse_best_of_crypto(markdown)
    assert [(e.owner, e.repo) for e in entries] == [("foo", "bar")]


def test_awesome_description_is_captured_not_the_tag_line():
    """La ligne <sub><sup>tags</sup></sub> n'est pas une description."""
    entries = {
        e.repo: e for e in parse_awesome_crypto(_read("awesome_crypto_sample.md"))
    }
    assert entries["bitcoin"].description == "Bitcoin Core integration/staging tree"
