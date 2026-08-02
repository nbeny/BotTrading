"""Peuplement du registre de projets depuis les deux awesome-lists.

Les deux listes se recouvrent partiellement — Bitcoin Core figure dans les deux —
et ne publient pas la même chose : seule ``awesome-crypto`` porte une URL de site
officiel. La fusion est donc asymétrique par nécessité, et jamais inventive : un
projet que ni l'une ni l'autre ne documente garde ``homepage_url = None`` plutôt
qu'une URL déduite du dépôt.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import replace

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from cmi_common.db.models import CryptoProjectRegistry
from cmi_common.db.session import Database

from ..domain.lists import ListEntry

logger = logging.getLogger(__name__)


def merge_entries(
    best_of: Iterable[ListEntry], awesome: Iterable[ListEntry]
) -> list[ListEntry]:
    """Fusionne les deux listes sur l'identité du dépôt, casse ignorée.

    L'URL de site de la seconde liste complète la première, qui n'en publie
    aucune. La règle est écrite « la valeur présente gagne sur l'absente »
    plutôt que « awesome gagne », pour que la fusion reste correcte si l'une
    des listes change de contenu.

    La clé ignore la casse parce que GitHub résout ``owner/repo`` sans la
    distinguer, mais la graphie d'origine est conservée : on déduplique sans
    réécrire ce que la source publie.
    """
    merged: dict[tuple[str, str], ListEntry] = {}
    for entry in [*best_of, *awesome]:
        key = entry.dedup_key
        current = merged.get(key)
        if current is None:
            merged[key] = entry
            continue
        sources = current.source_list.split(",")
        if entry.source_list not in sources:
            sources.append(entry.source_list)
        merged[key] = replace(
            current,
            homepage_url=current.homepage_url or entry.homepage_url,
            description=current.description or entry.description,
            source_list=",".join(sources),
        )
    return list(merged.values())


async def persist(db: Database, entries: Sequence[ListEntry]) -> int:
    """Upsert sur ``github_url``; ``last_seen_at`` est rafraîchi à chaque passage.

    ``first_seen_at`` n'est **jamais** écrasé : c'est la seule trace de la date
    d'entrée d'un projet dans les listes, et la seule chose qui rendrait un jour
    exploitable le signal « nouvellement listé » que ce chantier écarte pour
    l'instant.
    """
    if not entries:
        return 0
    written = 0
    async with db.sessionmaker() as session:
        for entry in entries:
            statement = insert(CryptoProjectRegistry).values(
                github_url=entry.github_url,
                name=entry.name,
                homepage_url=entry.homepage_url,
                description=entry.description,
                source_list=entry.source_list,
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["github_url"],
                    set_={
                        "name": statement.excluded.name,
                        "homepage_url": statement.excluded.homepage_url,
                        "description": statement.excluded.description,
                        "source_list": statement.excluded.source_list,
                        "last_seen_at": func.now(),
                    },
                )
            )
            written += 1
        await session.commit()
    return written


async def resolved_symbols(db: Database) -> dict[str, str]:
    """``github_url -> symbol`` pour les seules lignes dont le ticker est résolu.

    Les lignes à ``symbol NULL`` sont volontairement absentes : elles restent au
    catalogue mais ne doivent alimenter aucun agrégat.
    """
    async with db.sessionmaker() as session:
        rows = (
            await session.execute(
                select(
                    CryptoProjectRegistry.github_url, CryptoProjectRegistry.symbol
                ).where(CryptoProjectRegistry.symbol.is_not(None))
            )
        ).all()
    return dict(rows)
