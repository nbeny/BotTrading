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

import asyncio
import logging
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

import httpx

from cmi_common.observability import UPSTREAM_REQUESTS

from ..domain.lists import ListEntry

logger = logging.getLogger(__name__)

SERVICE = "collector-github"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
#: L'offre gratuite plafonne autour de 10-30 appels/minute et repond 429
#: au-dela. En production le collector n'appelle qu'un coin par cycle de
#: 600 s, donc la limite ne mord jamais — mais le harnais de verification,
#: lui, balaie l'univers d'un trait et s'est fait couper au 8e coin. Un
#: client qui ne connait pas son propre quota reporte le probleme sur
#: chacun de ses appelants.
MIN_INTERVAL_SECONDS = 6.5


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
        min_interval: float = MIN_INTERVAL_SECONDS,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=COINGECKO_BASE, timeout=timeout, transport=transport
        )
        self._min_interval = min_interval
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    async def _throttle(self) -> None:
        """Espace les appels, sous verrou pour que deux coroutines ne
        puissent pas passer ensemble."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            wait = self._next - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next = loop.time() + self._min_interval

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
        await self._throttle()
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


#: Chemins reserves de github.com : ce ne sont pas des depots, et les laisser
#: entrer produirait des 404 permanents dans la boucle de sondage plutot que
#: des absences comptees.
_RESERVED = frozenset(
    {"sponsors", "orgs", "topics", "collections", "features", "settings", "apps"}
)
#: Hotes acceptes. `gist.github.com` en est exclu : un gist a bien la forme
#: `user/hash` mais n'est pas un depot, et la paire produite pourrait entrer en
#: collision avec un vrai depot de cet utilisateur.
_HOSTS = frozenset({"github.com", "www.github.com"})


def _split(url: str) -> tuple[str, str] | None:
    """``(owner, repo)`` d'une URL GitHub, ou ``None`` si ce n'en est pas une.

    Analyse par ``urlsplit`` plutot que par decoupage de chaine. Le decoupage
    laissait passer plusieurs formes reelles de ``links.repos_url.github`` :
    ``https://GitHub.com/...`` etait rejete alors qu'il est valide (le marqueur
    etait sensible a la casse), ``...?tab=readme`` et ``...#readme`` entraient
    dans le nom du depot, et ``github.com/topics/solana`` produisait la paire
    ``("topics", "solana")`` — un depot fantome interroge indefiniment.
    """
    if not isinstance(url, str):
        return None
    parts = urlsplit(url.strip())
    if parts.netloc.lower() not in _HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1].removesuffix(".git")
    if owner.casefold() in _RESERVED or not repo:
        return None
    return owner, repo


def _dedupe(pairs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Deduplique sans tenir compte de la casse, en gardant la graphie d'origine.

    GitHub resout ``owner/repo`` sans distinguer la casse, donc
    ``Uniswap/v3-core`` et ``uniswap/v3-core`` sont un seul depot. Les compter
    deux fois couterait deux creneaux du round-robin, gonflerait ``repo_count``
    dans l'agregat, et compterait deux fois la base d'etoiles de ce depot dans
    la ponderation. ``ListEntry.dedup_key`` prend deja cette position pour la
    meme raison ; ce module s'y aligne.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for pair in pairs:
        key = (pair[0].casefold(), pair[1].casefold())
        if key not in seen:
            seen.add(key)
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
        if entry.dedup_key in seen:
            continue
        seen.add(entry.dedup_key)
        promoted.append((symbol, entry.owner, entry.repo))
    return promoted
