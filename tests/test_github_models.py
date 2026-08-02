"""Les trois tables du chantier GitHub: ce qui doit pouvoir etre NULL.

Chaque colonne de mesure y est nullable a dessein. Une colonne NOT NULL
DEFAULT 0 transformerait "pas encore lu" en "mesure a zero" au niveau du
schema, c'est-a-dire a l'endroit le plus difficile a rattraper ensuite.
"""

from __future__ import annotations

import re
from pathlib import Path

from cmi_common.db.models import (
    Base,
    CoinRepoMap,
    CryptoProjectRegistry,
    GithubRepoSnapshot,
)

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations/alembic/versions/0017_github_activity.py"
)

#: Toute mesure lue chez GitHub. Aucune n'est garantie: /stats repond 202
#: pendant son calcul, et les deux appels /search peuvent echouer separement.
_MEASURES = (
    "stars",
    "forks",
    "commits_4w",
    "commits_median_52w",
    "pr_merged_4w",
    "pr_merged_52w",
    "pushed_at",
)


def test_tables_are_registered():
    names = set(Base.metadata.tables)
    assert {
        "crypto_project_registry",
        "coin_repo_map",
        "github_repo_snapshot",
    } <= names


def test_registry_symbol_is_nullable():
    """Un projet sans ticker resolu reste au catalogue, symbol NULL.

    NULL veut dire "pas de rattachement connu", ce qui n'affirme rien sur
    l'activite du projet -- il ne doit alimenter aucun agregat.
    """
    assert CryptoProjectRegistry.__table__.c.symbol.nullable is True


def test_registry_homepage_is_nullable():
    """best-of-crypto ne publie pas d'URL de site: NULL, pas chaine vide.

    Une chaine vide se lirait comme "le projet n'a pas de site", alors que
    l'information est simplement absente de cette liste-la.
    """
    assert CryptoProjectRegistry.__table__.c.homepage_url.nullable is True


def test_registry_github_url_is_unique():
    """Cle de deduplication du registre: les deux listes citent les memes
    depots, et sans contrainte le second passage doublerait les lignes."""
    col = CryptoProjectRegistry.__table__.c.github_url
    assert col.unique is True
    assert col.nullable is False


def test_snapshot_measures_are_all_nullable_without_zero_defaults():
    """Aucune mesure ne vaut 0 par defaut, ni cote Python ni cote serveur."""
    cols = GithubRepoSnapshot.__table__.c
    for name in _MEASURES:
        assert cols[name].nullable is True, name
        assert cols[name].default is None, name
        assert cols[name].server_default is None, name


def test_snapshot_flags_default_to_false_not_null():
    """archived/is_fork sont des faits booleens, pas des mesures: un depot
    dont on a lu la fiche est archive ou ne l'est pas."""
    cols = GithubRepoSnapshot.__table__.c
    assert cols["archived"].nullable is False
    assert cols["is_fork"].nullable is False


def test_repo_map_is_unique_per_coin_and_repo():
    pk = {c.name for c in CoinRepoMap.__table__.primary_key}
    assert pk == {"coin_id", "owner", "repo"}


def test_repo_map_records_its_origin():
    """CoinGecko fait autorite, une promotion depuis une awesome-list non.
    Sans cette colonne, un doute futur sur la qualite de l'axe ne pourrait
    pas se trancher en filtrant."""
    col = CoinRepoMap.__table__.c.origin
    assert col.nullable is False


def test_snapshot_is_indexed_for_the_two_latest_rows():
    """`stats_from_rows` lit les deux dernieres lignes d'un depot a chaque
    cycle de publication, pour tous les symboles: sans index composite
    (owner, repo, observed_at) c'est un scan par symbole et par cycle."""
    indexed = {
        tuple(c.name for c in index.columns)
        for index in GithubRepoSnapshot.__table__.indexes
    }
    assert ("owner", "repo", "observed_at") in indexed


def _migration_tables() -> dict[str, set[str]]:
    """Colonnes creees par la migration 0017, lues dans son source.

    Lecture textuelle plutot qu'execution: alembic exigerait une base, et ce
    test doit tourner hors ligne comme le reste de la suite.
    """
    src = _MIGRATION.read_text(encoding="utf-8")
    return {
        m.group(1): set(re.findall(r'sa\.Column\(\s*"(\w+)"', m.group(2)))
        for m in re.finditer(
            r'op\.create_table\(\s*"(\w+)",(.*?)\n    \)\n', src, re.DOTALL
        )
    }


def test_migration_columns_match_the_models():
    """Modeles et migration derivent silencieusement sinon.

    Une colonne ajoutee au modele mais pas a la migration ne casse rien en
    test -- SQLAlchemy la connait, la base ne l'a pas -- et echoue seulement
    au premier acces en production.
    """
    tables = _migration_tables()
    assert set(tables) == {
        "crypto_project_registry",
        "coin_repo_map",
        "github_repo_snapshot",
    }
    for name, columns in tables.items():
        assert columns == {c.name for c in Base.metadata.tables[name].columns}, name


def test_migration_chains_onto_the_previous_head():
    src = _MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision = "0017"$', src, re.MULTILINE)
    assert re.search(r'^down_revision = "0016"$', src, re.MULTILINE)


def test_migration_downgrade_drops_every_table_it_creates():
    """Une migration qui ne se defait pas entierement laisse la base dans un
    etat que la suivante ne peut plus supposer."""
    src = _MIGRATION.read_text(encoding="utf-8")
    dropped = set(re.findall(r'op\.drop_table\("(\w+)"\)', src))
    assert dropped == set(_migration_tables())
