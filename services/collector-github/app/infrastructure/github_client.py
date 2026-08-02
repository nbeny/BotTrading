"""Accès REST à GitHub, avec les deux quotas et les trois échecs silencieux.

Trois comportements de l'API coûtent cher si on ne les traite pas nommément :

* **202 sur /stats/**\\ *. GitHub calcule ces séries de façon asynchrone et
  répond 202 avec un corps vide en attendant. Rendre ``[]`` ou ``0`` ici
  affirmerait « ce dépôt n'a pas commité de l'année », ce qui est une tout
  autre déclaration que « GitHub n'a pas fini de compter ». On rend ``None`` et
  le dépôt repasse au tour suivant.
* **404.** Dépôt renommé, supprimé ou passé privé. Levé comme ``RepoGoneError`` pour
  que l'appelant l'écrive au tableau noir plutôt que de redemander chaque cycle.
* **Deux seaux de quota distincts.** Le cœur REST accorde 5 000 requêtes/heure,
  l'API ``/search`` 30 par minute. Les mélanger fait passer le service pour dans
  les clous jusqu'au premier 403 de rate-limit secondaire.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from cmi_common.observability import UPSTREAM_REQUESTS

logger = logging.getLogger(__name__)

SERVICE = "collector-github"
API_BASE = "https://api.github.com"
#: Le cœur REST : 5 000 req/h pour un compte authentifié, soit ~83/min. On
#: reste en dessous pour laisser de la marge aux autres consommateurs du token.
CORE_PER_MIN = 80
#: L'API de recherche : 30 req/min, seau séparé.
SEARCH_PER_MIN = 25
#: Fenêtre récente, en semaines, et longueur minimale de série exploitable.
WINDOW_WEEKS = 4
#: Il faut au moins une semaine de baseline en plus de la fenêtre récente pour
#: que la médiane dise quelque chose. En dessous, le dépôt est trop jeune —
#: c'est ce seuil qui, avec celui de ``recent_commits``, fait qu'un dépôt de 4
#: à 7 semaines a un numérateur sans dénominateur.
MIN_WEEKS_FOR_BASELINE = 8


class RepoGoneError(Exception):
    """Le dépôt n'existe plus sous ce nom pour ce token."""


@dataclass(frozen=True, slots=True)
class RepoMeta:
    #: `archived` et `is_fork` sont `bool | None`, pas `bool`. Un `bool` ne
    #: peut pas exprimer "pas lu", et la valeur par defaut serait alors `False`
    #: — c'est-a-dire "ce depot est vivant", affirme a partir d'une reponse
    #: qu'on n'a pas parsee. C'est le champ qui decide de l'entree dans
    #: l'agregat, donc le pire endroit du module pour une valeur inventee.
    stars: int | None
    forks: int | None
    pushed_at: datetime | None
    archived: bool | None
    is_fork: bool | None


class _Limiter:
    """Fenêtre glissante minute, recalée par les en-têtes de l'API."""

    def __init__(self, per_minute: int) -> None:
        self._interval = 60.0 / max(1, per_minute)
        self._next = 0.0
        self._lock = asyncio.Lock()
        #: Compteur d'acquisitions, exposé pour que les tests puissent vérifier
        #: qu'un appel a bien été routé vers *ce* seau. Sans cela, deux seaux
        #: distincts sont indiscernables d'un seul.
        self.acquisitions = 0

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            wait = self._next - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next = loop.time() + self._interval
            self.acquisitions += 1

    def observe(self, response: httpx.Response) -> None:
        """Se recale sur ce que l'API annonce plutôt que sur une constante.

        ``x-ratelimit-remaining`` à zéro veut dire qu'on a mal estimé le coût
        des appels : on attend la réinitialisation annoncée au lieu de marcher
        dans le 403.
        """
        remaining = response.headers.get("x-ratelimit-remaining")
        reset = response.headers.get("x-ratelimit-reset")
        if remaining is None or reset is None:
            return
        try:
            left, reset_at = int(remaining), int(reset)
        except ValueError:
            return
        if left <= 0:
            seconds = max(0.0, reset_at - datetime.now(tz=UTC).timestamp())
            self._next = asyncio.get_running_loop().time() + seconds
            UPSTREAM_REQUESTS.labels(SERVICE, "github", "throttled").inc()


class GitHubClient:
    def __init__(
        self,
        *,
        token: str,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.core_limiter = _Limiter(CORE_PER_MIN)
        self.search_limiter = _Limiter(SEARCH_PER_MIN)
        self._http = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=timeout,
            transport=transport,
            # GitHub repond 301 sur un depot renomme ou transfere, ce qui est
            # courant sur une liste rafraichie lentement. Sans suivi, le 301
            # passe le filtre `>= 400`, le corps n'est jamais parse, et le
            # depot fantome entre dans l'agregat sans jamais etre mis au
            # tableau noir puisque ce n'est pas un 404.
            follow_redirects=True,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, limiter: _Limiter, **params: str) -> httpx.Response:
        await limiter.acquire()
        response = await self._http.get(path, params=params or None)
        limiter.observe(response)
        if response.status_code == 404:
            UPSTREAM_REQUESTS.labels(SERVICE, "github", "gone").inc()
            raise RepoGoneError(path)
        if response.status_code >= 400:
            # Un 500 ou un 403 doit remonter : le confondre avec une mesure
            # absente ferait lire « ce dépôt n'a rien » là où l'API n'a pas
            # répondu. 202 n'est pas une erreur et ne passe pas ici.
            UPSTREAM_REQUESTS.labels(SERVICE, "github", "error").inc()
            response.raise_for_status()
        UPSTREAM_REQUESTS.labels(SERVICE, "github", "ok").inc()
        return response

    async def repo(self, owner: str, repo: str) -> RepoMeta:
        data = (await self._get(f"/repos/{owner}/{repo}", self.core_limiter)).json()
        pushed = data.get("pushed_at")
        return RepoMeta(
            stars=data.get("stargazers_count"),
            forks=data.get("forks_count"),
            pushed_at=(
                datetime.fromisoformat(pushed.replace("Z", "+00:00"))
                if pushed
                else None
            ),
            archived=_as_bool(data.get("archived")),
            is_fork=_as_bool(data.get("fork")),
        )

    async def commit_activity(self, owner: str, repo: str) -> list[int] | None:
        """52 totaux hebdomadaires, ou ``None`` si GitHub calcule encore.

        Une liste vide est traitée comme ``None`` et non comme « 52 semaines à
        zéro » : l'API la renvoie aussi bien pour un dépôt réellement vide que
        pendant le calcul, et les deux ne se distinguent pas à la lecture.
        Rendre ``None`` coûte un cycle ; rendre ``0`` fabriquerait une année de
        silence.
        """
        response = await self._get(
            f"/repos/{owner}/{repo}/stats/commit_activity", self.core_limiter
        )
        if response.status_code == 202:
            return None
        weeks = response.json()
        if not isinstance(weeks, list) or not weeks:
            return None
        # Pas de defaut a zero : une semaine sans `total` est une lecture
        # incomplete, pas une semaine sans commit. Avec un defaut, une charge
        # partiellement absente donnait une mediane reelle et un numerateur
        # deprime — un ratio faux, dans le sens "moins actif".
        if any(not isinstance(week, dict) or "total" not in week for week in weeks):
            return None
        return [int(week["total"]) for week in weeks]

    async def merged_pr_count(
        self, owner: str, repo: str, *, since_days: int
    ) -> int | None:
        since = (datetime.now(tz=UTC) - timedelta(days=since_days)).date().isoformat()
        response = await self._get(
            "/search/issues",
            self.search_limiter,
            q=f"repo:{owner}/{repo} is:pr is:merged merged:>{since}",
            per_page="1",
        )
        payload = response.json()
        if payload.get("incomplete_results"):
            # GitHub leve ce drapeau quand la requete a expire et que le
            # decompte est partiel. La requete a 364 jours sur un gros depot
            # est precisement ou il se declenche. Rendre le nombre tel quel
            # serait pire qu'une absence : le drapeau peut toucher le
            # numerateur ou le denominateur du ratio independamment, donc il
            # deplace le resultat dans les deux directions.
            return None
        total = payload.get("total_count")
        return int(total) if total is not None else None


def _as_bool(value: object) -> bool | None:
    """`None` quand le champ est absent, sinon le booleen.

    `bool(None)` vaudrait `False`, ce qui affirmerait "pas archive" a partir
    d'une absence de lecture.
    """
    return None if value is None else bool(value)


def weekly_median(weeks: list[int] | None) -> float | None:
    """Médiane hebdomadaire sur l'année, hors les 4 dernières semaines.

    Les 4 dernières sont exclues parce qu'elles constituent la fenêtre
    *récente* : les inclure dans leur propre baseline amortit exactement le
    mouvement qu'on cherche à détecter.

    La médiane, et non la moyenne : un unique gros merge (import de vendor,
    reformatage) écrase une moyenne et rendrait tout le reste de l'année
    anormalement calme.
    """
    if weeks is None or len(weeks) < MIN_WEEKS_FOR_BASELINE:
        return None
    return float(statistics.median(weeks[:-WINDOW_WEEKS]))


def recent_commits(weeks: list[int] | None) -> int | None:
    if weeks is None or len(weeks) < WINDOW_WEEKS:
        return None
    return sum(weeks[-WINDOW_WEEKS:])
