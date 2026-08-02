# tests/test_github_lists.py
from pathlib import Path

from service_modules import load_service_module

_lists = load_service_module("collector-github", "domain.lists")
ListEntry = _lists.ListEntry
parse_awesome_crypto = _lists.parse_awesome_crypto
parse_best_of_crypto = _lists.parse_best_of_crypto

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_best_of_extracts_repos():
    entries = parse_best_of_crypto(_read("best_of_crypto_sample.md")).entries
    by_repo = {(e.owner, e.repo): e for e in entries}
    assert ("bitcoin", "bitcoin") in by_repo
    assert ("ethereum", "go-ethereum") in by_repo


def test_best_of_has_no_homepage():
    """La liste n'en publie pas. None, pas une URL devinee depuis le repo."""
    entries = parse_best_of_crypto(_read("best_of_crypto_sample.md")).entries
    assert all(e.homepage_url is None for e in entries)


def test_best_of_deduplicates_summary_and_body_links():
    """Chaque entree porte deux fois la meme URL; une seule doit sortir."""
    entries = parse_best_of_crypto(_read("best_of_crypto_sample.md")).entries
    assert len(entries) == 2


def test_best_of_deduplicates_a_project_listed_in_two_categories():
    """bitcoin est range dans Cryptocurrencies *et* Smart Contract Platforms.

    Sans le filtre `seen`, le registre porterait deux lignes pour un meme
    depot -- et comme il deduplique ensuite sur github_url, la seconde
    ecraserait la premiere en base plutot que de faire echouer quoi que ce
    soit. Le fixture contient donc deliberement le doublon.
    """
    entries = parse_best_of_crypto(_read("best_of_crypto_sample.md")).entries
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
        e.repo: e
        for e in parse_awesome_crypto(_read("awesome_crypto_sample.md")).entries
    }
    assert entries["tagged-only"].description is None


def test_awesome_extracts_homepage_when_present():
    entries = {
        e.repo: e
        for e in parse_awesome_crypto(_read("awesome_crypto_sample.md")).entries
    }
    assert entries["bitcoin"].homepage_url == "https://bitcoincore.org/en/download"


def test_awesome_homepage_is_none_when_only_github_line():
    """La ligne site est absente pour solana: None, pas l'URL GitHub recopiee."""
    entries = {
        e.repo: e
        for e in parse_awesome_crypto(_read("awesome_crypto_sample.md")).entries
    }
    assert entries["solana"].homepage_url is None
    assert entries["solana"].owner == "solana-labs"


def test_unparseable_entries_are_counted_not_guessed():
    """Un format qui change doit se voir, pas retrecir le registre en silence."""
    result = parse_best_of_crypto(_read("best_of_crypto_sample.md"))
    assert all(isinstance(e, ListEntry) for e in result.entries)
    assert not any(e.name == "broken-entry-without-link" for e in result.entries)
    # Les blocs illisibles sont *comptes*, pas seulement ignores: un README
    # dont le format change doit faire monter ce compteur plutot que faire
    # retrecir le registre sans bruit. Deux ici: le titre sans lien du tout, et
    # celui dont le lien n'est pas dans le <summary>.
    assert result.unparsed == 2


def test_github_url_is_rebuilt_from_owner_and_repo():
    """Le registre deduplique sur github_url: il doit etre canonique."""
    entry = parse_best_of_crypto(_read("best_of_crypto_sample.md")).entries[0]
    assert entry.github_url == "https://github.com/bitcoin/bitcoin"


def test_awesome_counts_no_unparsed_on_a_clean_fixture():
    """Contrepartie du compteur: il ne doit pas monter sur du valide."""
    assert parse_awesome_crypto(_read("awesome_crypto_sample.md")).unparsed == 0


def test_source_list_is_recorded_per_entry():
    """Le registre trace d'ou vient chaque projet; les deux listes ne
    publient pas la meme chose, donc l'origine n'est pas cosmetique."""
    best = parse_best_of_crypto(_read("best_of_crypto_sample.md")).entries
    awesome = parse_awesome_crypto(_read("awesome_crypto_sample.md")).entries
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
    entries = parse_best_of_crypto(markdown).entries
    assert [(e.owner, e.repo) for e in entries] == [("foo", "bar")]


def test_awesome_description_is_captured_not_the_tag_line():
    """La ligne <sub><sup>tags</sup></sub> n'est pas une description."""
    entries = {
        e.repo: e
        for e in parse_awesome_crypto(_read("awesome_crypto_sample.md")).entries
    }
    assert entries["bitcoin"].description == "Bitcoin Core integration/staging tree"


def test_a_body_link_never_becomes_the_projects_repo():
    """C1: la recherche est bornee au <summary>, pas au bloc entier.

    Un titre non lie dont le corps porte un <a href> GitHub produisait sinon
    un depot confiant et faux -- le seul chemin du module capable de fabriquer
    une valeur au lieu d'en perdre une, et sans faire bouger unparsed.
    """
    result = parse_best_of_crypto(_read("best_of_crypto_sample.md"))
    assert not any((e.owner, e.repo) == ("someone", "else") for e in result.entries)


def test_blocks_seen_is_the_denominator_unparsed_cannot_replace():
    """C2: unparsed est aveugle a la derive qui compte.

    Si best-of cesse d'emettre <details>, aucun bloc n'est reconnu: entries
    est vide et unparsed vaut zero. Seul le denominateur distingue "la liste a
    retreci" de "notre parseur ne la comprend plus".
    """
    drifted = parse_best_of_crypto("## Une liste sans le moindre bloc details")
    assert drifted.entries == []
    assert drifted.unparsed == 0
    assert drifted.blocks_seen == 0
    real = parse_best_of_crypto(_read("best_of_crypto_sample.md"))
    assert real.blocks_seen == 5


def test_awesome_blocks_seen_survives_a_heading_level_change():
    """Meme aveuglement cote awesome si ### devient ##."""
    drifted = parse_awesome_crypto("## [x](https://github.com/a/b)\ndesc\n")
    assert drifted.entries == []
    assert drifted.blocks_seen == 0


def test_unreadable_head_url_is_counted_in_awesome():
    """M03: l'increment d'unparsed sur une URL non-GitHub n'etait pas teste."""
    result = parse_awesome_crypto("### [x](https://gitlab.com/a/b)\ndesc\n")
    assert result.entries == []
    assert result.blocks_seen == 1
    assert result.unparsed == 1


def test_unreadable_summary_url_is_counted_in_best_of():
    """M02: l'increment symetrique cote best-of."""
    block = (
        '<details><summary><b><a href="https://github.com/">x</a></b>'
        "</summary></details>"
    )
    result = parse_best_of_crypto(block)
    assert result.entries == []
    assert result.blocks_seen == 1
    assert result.unparsed == 1


def test_dedup_is_case_insensitive_because_github_is():
    """I1: Bitcoin/Bitcoin et bitcoin/bitcoin sont un seul depot.

    Deux lignes couteraient deux creneaux du round-robin, lequel est borne par
    le quota et balaie l'univers en 12 h, et gonfleraient repo_count.
    """
    markdown = (
        '<details><summary><a href="https://github.com/Bitcoin/Bitcoin">B</a>'
        "</summary></details>"
        '<details><summary><a href="https://github.com/bitcoin/bitcoin">b</a>'
        "</summary></details>"
    )
    assert len(parse_best_of_crypto(markdown).entries) == 1


def test_awesome_deduplicates_too():
    """I2: l'asymetrie avec best-of laissait la seconde entree ecraser la
    premiere en base, description et homepage comprises."""
    entry = "### [x](https://github.com/a/b)\ndesc\n"
    assert len(parse_awesome_crypto(entry + "\n" + entry).entries) == 1


def test_head_line_residue_is_not_the_description():
    """I3: le corps commence a la ligne suivante, pas a la fin du titre.

    Le suffixe "by [owner](...)" est une forme reelle de ces listes; decoupe a
    head.end(), il devenait la description du projet.
    """
    markdown = (
        "### [bitcoin](https://github.com/bitcoin/bitcoin) "
        "by [bitcoin](https://github.com/bitcoin)\n"
        "Bitcoin Core integration/staging tree\n"
    )
    entry = parse_awesome_crypto(markdown).entries[0]
    assert entry.description == "Bitcoin Core integration/staging tree"


def test_homepage_is_found_when_the_label_is_not_a_url():
    """I4: exiger une URL en libelle rendait le parseur muet, et en silence.

    Les homepages sont la moitie du livrable du registre: une perte totale
    avec un compteur vert est le pire resultat disponible.
    """
    markdown = (
        "### [x](https://github.com/a/b)\ndesc\n"
        "[bitcoincore.org](https://bitcoincore.org)\n"
        "[https://github.com/a/b](https://github.com/a/b)\n"
    )
    assert parse_awesome_crypto(markdown).entries[0].homepage_url == (
        "https://bitcoincore.org"
    )


def test_missing_homepages_are_counted():
    """Contrepartie de I4: si le gabarit change et qu'elles passent toutes a
    None, seul ce compteur le dira."""
    result = parse_awesome_crypto(_read("awesome_crypto_sample.md"))
    assert result.homepage_missing == 2  # solana et tagged-only


def test_reserved_github_paths_are_not_repos():
    """github.com/sponsors/foo n'est pas un depot: le laisser entrer
    produirait un 404 permanent dans la boucle de sondage."""
    markdown = (
        '<details><summary><a href="https://github.com/sponsors/foo">s</a>'
        "</summary></details>"
    )
    result = parse_best_of_crypto(markdown)
    assert result.entries == []
    assert result.unparsed == 1


def test_awesome_name_is_captured():
    """M09: le nom n'etait asserte nulle part pour ce parseur."""
    entry = parse_awesome_crypto("### [Bitcoin Core](https://github.com/a/b)\nd\n")
    assert entry.entries[0].name == "Bitcoin Core"
