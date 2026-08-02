"""Rattachement ``coin -> dépôts``, colonne vertébrale du signal.

CoinGecko fait autorité : ``/coins/{id}`` publie ``links.repos_url.github``,
c'est-à-dire les dépôts que le projet lui-même déclare. Les awesome-lists ne
servent qu'à proposer des candidats supplémentaires, et un candidat n'est promu
que si son nom résout sans ambiguïté contre le lexicon.

Le filtre est délibérément sévère. Sur ~8 400 entrées cumulées, la grande
majorité sont des bibliothèques sans token (ccxt, web3.py, OpenZeppelin) : une
promotion trop généreuse rattacherait l'activité d'un outil générique à un coin
au hasard, ce qui produirait une mesure — donc un déplacement du score — à
partir d'une coïncidence de nommage.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping

import httpx

from cmi_common.observability import UPSTREAM_REQUESTS

from ..domain.lists import ListEntry

logger = logging.getLogger(__name__)

SERVICE = "collector-github"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"


class CoinGeckoRepos:
    """Lit ``links.repos_url.github`` pour un coin.

    Ce mapping change au rythme des listings, pas des polls : l'appelant le met
    en cache et ne rafraîchit que quelques coins par cycle, ce qui tient dans le
    quota gratuit (10 à 30 appels/minute).
    """

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=COINGECKO_BASE, timeout=timeout, transport=transport
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def repos_for(self, coin_id: str) -> list[tuple[str, str]]:
        """``[(owner, repo), …]``, liste vide si le coin n'en déclare aucun.

        Une liste vide est une réponse — « CoinGecko connaît ce coin et il ne
        publie pas de dépôt » — et non un échec. L'appelant la distingue d'une
        exception, qui elle veut dire « on n'a pas pu demander » : confondre les
        deux effacerait une carte correcte au premier rate-limit.

        Les doublons sont écartés : le même dépôt y apparaît sous plusieurs
        graphies (``.git`` final, sous-chemin ``/tree/main``), et deux lignes
        de carte feraient interroger deux fois le même dépôt.
        """
        response = await self._http.get(
            f"/coins/{coin_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
            },
        )
        if response.status_code >= 400:
            UPSTREAM_REQUESTS.labels(SERVICE, "coingecko", "error").inc()
            response.raise_for_status()
        UPSTREAM_REQUESTS.labels(SERVICE, "coingecko", "ok").inc()
        urls = response.json().get("links", {}).get("repos_url", {}).get("github") or []
        return _dedupe(
            pair for pair in (_split(url) for url in urls) if pair is not None
        )


def _split(url: str) -> tuple[str, str] | None:
    marker = "github.com/"
    if not isinstance(url, str) or marker not in url:
        return None
    parts = url.split(marker, 1)[1].strip("/").split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1].removesuffix(".git")


def _dedupe(pairs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def promote_list_entries(
    entries: Iterable[ListEntry],
    *,
    symbols_by_name: Mapping[str, str],
    homographs: frozenset[str],
) -> list[tuple[str, str, str]]:
    """``[(symbol, owner, repo), …]`` pour les seules entrées non ambiguës.

    Deux garde-fous, dans cet ordre : le nom du projet doit résoudre contre le
    lexicon, et le symbole obtenu ne doit pas figurer parmi les homographes que
    le lexicon signale déjà (ONE, KEEP, FLOW…). Ces derniers demandent une
    corroboration que le seul nom d'un dépôt ne fournit pas.

    Tout ce qui ne résout pas est simplement écarté. L'entrée reste au registre
    avec ``symbol = NULL`` — catalogue, pas mesure — et n'alimente aucun
    agrégat.
    """
    promoted: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        symbol = symbols_by_name.get(entry.name.strip().lower())
        if symbol is None or symbol in homographs:
            continue
        if (entry.owner, entry.repo) in seen:
            continue
        seen.add((entry.owner, entry.repo))
        promoted.append((symbol, entry.owner, entry.repo))
    return promoted
