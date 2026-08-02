"""Un cycle du collector GitHub — deux horloges dans une seule boucle.

Le service tourne toutes les 600 s parce que le ``FeatureStore`` de
``ai-worker-haiku`` expire à 900 s : un axe publié moins souvent que ça est
absent la plupart du temps. Mais l'API GitHub n'a pas besoin d'être interrogée
à ce rythme — l'activité de développement bouge à l'échelle de la semaine.

D'où la dissociation : **publication depuis le cache à chaque cycle**,
**rafraîchissement réseau en round-robin** borné par un budget. Le budget borne
les téléchargements, jamais le reporting. L'inverse a déjà été payé sur les
unlocks DefiLlama, où un plafond appliqué à l'appartenance à la carte faisait
déclarer « aucun calendrier connu » à 37 tokens sur 40 dont le calendrier était
déjà en cache — et comme la renormalisation note mieux un axe absent qu'un axe
mesuré mauvais, le plafond promouvait silencieusement tout ce qu'il sautait.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import ValidationError

from cmi_common.events import DeveloperEvent, Source
from cmi_common.kafka import Topic
from cmi_common.observability import EVENTS_PRODUCED, UNMEASURED

from ..domain.activity import RepoStats
from ..domain.aggregate import aggregate
from ..infrastructure.github_client import RepoGoneError

logger = logging.getLogger(__name__)

SERVICE = "collector-github"

#: ``symbol -> (coin_id, [(owner, repo), …])``
RepoMap = Mapping[str, tuple[str, list[tuple[str, str]]]]


class SnapshotStore(Protocol):
    async def latest(self, owner: str, repo: str) -> RepoStats | None: ...
    async def save(self, stats: RepoStats) -> None: ...


@dataclass(frozen=True, slots=True)
class CycleStats:
    """Ce qu'un cycle a coûté, en comptes séparés.

    Leur somme ne permettrait pas de distinguer « rien n'était éligible » de
    « tout a échoué », deux situations qui appellent des réactions opposées
    d'un opérateur. ``deferred`` est à part pour la même raison : un 202 n'est
    ni un succès ni une erreur, et les confondre empêcherait de distinguer
    « GitHub calcule » de « notre appel casse ».
    """

    published: int = 0
    refreshed: int = 0
    deferred: int = 0
    gone: int = 0
    failed: int = 0
    skipped_no_measurement: int = 0


class GitHubCollector:
    def __init__(
        self,
        *,
        producer,
        store: SnapshotStore,
        fetch_repo: Callable[[str, str], Awaitable[RepoStats]],
        repo_map: Callable[[], RepoMap],
        clock: Callable[[], datetime],
        max_refresh_per_cycle: int = 7,
    ) -> None:
        self._producer = producer
        self._store = store
        self._fetch = fetch_repo
        self._repo_map = repo_map
        self._clock = clock
        self._budget = max_refresh_per_cycle
        #: Curseur tournant sur les dépôts, pour que chacun passe à son tour
        #: plutôt que les premiers affament les suivants.
        self._cursor = 0
        #: Dépôts introuvables, écrits au tableau noir jusqu'au redémarrage :
        #: réessayer chaque cycle dépenserait un appel par cycle pour la même
        #: réponse.
        self._dead: set[tuple[str, str]] = set()

    async def poll_once(self) -> CycleStats:
        mapping = self._repo_map()
        refreshed, deferred, gone, failed = await self._refresh_batch(mapping)
        published, skipped = await self._publish_all(mapping)
        stats = CycleStats(
            published=published,
            refreshed=refreshed,
            deferred=deferred,
            gone=gone,
            failed=failed,
            skipped_no_measurement=skipped,
        )
        logger.info(
            "github cycle: published=%d refreshed=%d deferred=%d gone=%d "
            "failed=%d skipped=%d",
            stats.published,
            stats.refreshed,
            stats.deferred,
            stats.gone,
            stats.failed,
            stats.skipped_no_measurement,
        )
        return stats

    async def _refresh_batch(self, mapping: RepoMap) -> tuple[int, int, int, int]:
        repos = [
            pair
            for _, (_, pairs) in sorted(mapping.items())
            for pair in pairs
            if pair not in self._dead
        ]
        if not repos or self._budget <= 0:
            return 0, 0, 0, 0
        refreshed = deferred = gone = failed = 0
        take = min(self._budget, len(repos))
        for offset in range(take):
            owner, repo = repos[(self._cursor + offset) % len(repos)]
            try:
                stats = await self._fetch(owner, repo)
            except RepoGoneError:
                self._dead.add((owner, repo))
                gone += 1
                continue
            except Exception as exc:
                UNMEASURED.labels(SERVICE, "repo_stats", type(exc).__name__).inc()
                logger.warning("github: %s/%s — %s", owner, repo, exc)
                failed += 1
                continue
            if stats.commits_4w is None:
                # GitHub calcule encore (202). Le dépôt repassera au tour
                # suivant ; on l'enregistre quand même, les autres champs
                # peuvent être présents.
                deferred += 1
            await self._store.save(stats)
            refreshed += 1
        self._cursor = (self._cursor + take) % len(repos)
        return refreshed, deferred, gone, failed

    async def _publish_all(self, mapping: RepoMap) -> tuple[int, int]:
        now = self._clock()
        published = 0
        skipped = 0
        for symbol, (coin_id, pairs) in mapping.items():
            snapshots = [
                snap
                for snap in [await self._store.latest(o, r) for o, r in pairs]
                if snap is not None
            ]
            try:
                activity = aggregate(snapshots, now)
            except Exception as exc:
                # `aggregate` est pur mais pas total : un snapshot partiel ou un
                # horodatage naïf y lèvent. Hors de ce garde, l'exception
                # sortait de la boucle et coûtait le cycle aux ~249 autres
                # tokens — la garde plus bas ne couvrait que la construction de
                # l'événement, pas le calcul qui la précède.
                UNMEASURED.labels(SERVICE, "aggregate", type(exc).__name__).inc()
                logger.error("github: %s — agrégation impossible: %s", symbol, exc)
                continue
            if activity is None:
                # Aucun dépôt lu pour ce symbole. Publier un événement à zéro
                # transformerait « pas encore mesuré » en « aucune activité »,
                # et l'axe pèserait alors contre le token.
                continue
            if activity.push_timestamp_rejected:
                UNMEASURED.labels(SERVICE, "days_since_push", "clock_skew").inc()
            if not activity.has_measurement:
                # Des dépôts existent mais aucune mesure n'a abouti. L'événement
                # serait valide au sens du schéma et n'affirmerait rien. Compté
                # plutôt que simplement sauté : un coin bloqué en 202, un
                # mapping résolu vers du vide et un coin réellement inactif
                # deviendraient sinon le même non-événement.
                UNMEASURED.labels(SERVICE, "developer_event", "no_measurement").inc()
                skipped += 1
                continue
            try:
                event = DeveloperEvent(
                    source=Source.GITHUB,
                    symbol=symbol,
                    coin_id=coin_id,
                    repo_count=activity.repo_count,
                    commit_ratio_4w=activity.commit_ratio_4w,
                    pr_ratio_4w=activity.pr_ratio_4w,
                    days_since_push=activity.days_since_push,
                    star_growth_pct_7d=activity.star_growth_pct_7d,
                    all_repos_archived=activity.all_repos_archived,
                )
            except ValidationError as exc:
                # Un token invalide ne doit pas coûter le cycle aux autres.
                # C'est la leçon de l'incident fees_24h_usd : là-bas, un ge=0
                # sur des frais légitimement négatifs levait dans une boucle
                # d'émission sans garde, et un seul protocole entrant dans
                # l'univers faisait publier *zéro* événement pour tous les
                # tokens du cycle.
                UNMEASURED.labels(SERVICE, "developer_event", "ValidationError").inc()
                logger.error("github: %s — événement invalide: %s", symbol, exc)
                continue
            await self._producer.publish(Topic.DEVELOPER, event)
            EVENTS_PRODUCED.labels(
                SERVICE, Topic.DEVELOPER.value, event.event_type
            ).inc()
            published += 1
        return published, skipped
