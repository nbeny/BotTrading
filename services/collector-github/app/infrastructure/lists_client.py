"""Téléchargement des deux README, sur un timer lent.

Les listes sont régénérées une fois par semaine par leur générateur ; les relire
plus souvent dépenserait de la bande passante pour un contenu identique.
"""

from __future__ import annotations

import httpx

BEST_OF = "https://raw.githubusercontent.com/lukasmasuch/best-of-crypto/main/README.md"
AWESOME = "https://raw.githubusercontent.com/dylanhogg/awesome-crypto/main/README.md"


async def fetch_readmes(
    *, http_timeout: float = 60.0, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str | None, str | None]:
    """``(best_of, awesome)`` ; ``None`` pour celle qui n'a pas répondu.

    Un échec sur l'une ne doit pas priver le registre de l'autre : ce sont deux
    sources indépendantes. ``None`` dit « pas relue ce cycle », ce qui laisse
    les lignes déjà en base intactes — à distinguer d'une chaîne vide, qui se
    lirait comme « cette liste ne contient plus rien » et viderait le registre.
    """
    results: list[str | None] = []
    async with httpx.AsyncClient(timeout=http_timeout, transport=transport) as http:
        for url in (BEST_OF, AWESOME):
            try:
                response = await http.get(url, follow_redirects=True)
                response.raise_for_status()
                results.append(response.text)
            except httpx.HTTPError:
                results.append(None)
    return results[0], results[1]
