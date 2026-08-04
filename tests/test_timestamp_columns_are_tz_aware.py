"""Aucune colonne temporelle ne doit se declarer sans fuseau.

Quatre modeles declaraient `Mapped[datetime]` nu la ou la colonne est
`timestamptz` en base. SQLAlchemy rendait alors le parametre en
`TIMESTAMP WITHOUT TIME ZONE`, et toute lecture filtrant sur un datetime
*aware* levait asyncpg.DataError a l'encodage -- avant meme d'atteindre la
base. collector-binance-futures a echoue a 100% de ses cycles pendant 28
heures sur ce defaut, en se declarant `healthy`, et l'axe positioning n'a
jamais produit une seule lecture en production.

Le test balaie toutes les colonnes plutot que les quatre connues: la
declaration et la colonne ne sont confrontees nulle part ailleurs dans la
suite, ce qui est exactement pourquoi la divergence a vecu.
"""

from __future__ import annotations

from sqlalchemy import DateTime

from cmi_common.db import Base


def _datetime_columns() -> list[tuple[str, DateTime]]:
    return [
        (f"{table.name}.{column.name}", column.type)
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, DateTime)
    ]


def test_the_sweep_actually_finds_columns() -> None:
    """Sans ceci, une erreur de parcours ferait passer le test suivant a vide."""
    assert len(_datetime_columns()) > 20


def test_every_datetime_column_declares_a_timezone() -> None:
    naive = [name for name, type_ in _datetime_columns() if not type_.timezone]
    assert not naive, (
        f"colonnes temporelles declarees sans fuseau: {naive}. "
        "La base les stocke en timestamptz; SQLAlchemy rendra le parametre en "
        "TIMESTAMP WITHOUT TIME ZONE et toute lecture avec un datetime aware "
        "levera asyncpg.DataError a l'encodage."
    )
