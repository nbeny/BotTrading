"""collector-github service entrypoint (FastAPI + background pollers)."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

from fastapi import FastAPI
from sqlalchemy import select

from cmi_common import Settings, create_app
from cmi_common.db.models import Token
from cmi_common.db.session import Database
from cmi_common.kafka import EventProducer
from cmi_common.observability import UNMEASURED
from cmi_common.runner import run_periodic

from .application.collector import GitHubCollector
from .application.registry import merge_entries, persist
from .domain.activity import RepoStats
from .domain.lists import parse_awesome_crypto, parse_best_of_crypto
from .infrastructure.github_client import (
    GitHubClient,
    recent_commits,
    weekly_median,
)
from .infrastructure.lists_client import fetch_readmes
from .infrastructure.repo_map import CoinGeckoRepos
from .infrastructure.store import PostgresSnapshotStore

logger = logging.getLogger(__name__)

SERVICE = "collector-github"
#: Cadence de *publication*. Calée sur le TTL de 900 s du FeatureStore, pas sur
#: le rythme de l'API GitHub — voir application/collector.py.
POLL_INTERVAL = float(os.getenv("GITHUB_POLL_INTERVAL", "600"))
#: Plancher à 1 : zéro figerait le curseur et le round-robin ne tournerait
#: jamais — une mauvaise configuration qui dégrade en silence.
MAX_REFRESH = max(1, int(os.getenv("GITHUB_MAX_REFRESH_PER_CYCLE", "7")))
UNIVERSE_SIZE = int(os.getenv("GITHUB_UNIVERSE_SIZE", "250"))
LISTS_REFRESH_SECONDS = float(os.getenv("GITHUB_LISTS_REFRESH_HOURS", "168")) * 3600
TOKEN = os.getenv("GITHUB_TOKEN", "")
PR_WINDOW_DAYS = 28
PR_BASELINE_DAYS = 364
#: Un coin par cycle : la carte est quasi immuable et le quota gratuit de
#: CoinGecko est étroit. Le remplissage initial s'étale donc sur plusieurs
#: heures plutôt que de saturer l'API au démarrage.
COINS_PER_CYCLE = 1


async def _startup(app: FastAPI, settings: Settings) -> None:
    if not TOKEN:
        # Pas de repli sur l'API anonyme (60 req/h) : un quota anonyme épuisé
        # produirait des mesures partielles indiscernables de mesures
        # complètes. Un axe absent est correctement traité en aval ; un axe à
        # moitié mesuré ne l'est pas.
        logger.error("collector-github: GITHUB_TOKEN absent — le service reste inactif")
        return

    db = Database(settings.db)
    producer = EventProducer(settings.kafka)
    await producer.start()
    client = GitHubClient(token=TOKEN)
    gecko = CoinGeckoRepos()
    store = PostgresSnapshotStore(db)
    mapping: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    #: Symboles deja interroges, avec ou sans resultat. Distinct de `mapping`,
    #: qui ne retient que les succes.
    attempted: set[str] = set()

    async def fetch_repo(owner: str, repo: str) -> RepoStats:
        meta = await client.repo(owner, repo)
        weeks = await client.commit_activity(owner, repo)
        return RepoStats(
            owner=owner,
            repo=repo,
            stars=meta.stars,
            forks=meta.forks,
            pushed_at=meta.pushed_at,
            archived=meta.archived,
            is_fork=meta.is_fork,
            commits_4w=recent_commits(weeks),
            commits_median_52w=weekly_median(weeks),
            pr_merged_4w=await client.merged_pr_count(
                owner, repo, since_days=PR_WINDOW_DAYS
            ),
            pr_merged_52w=await client.merged_pr_count(
                owner, repo, since_days=PR_BASELINE_DAYS
            ),
        )

    collector = GitHubCollector(
        producer=producer,
        store=store,
        fetch_repo=fetch_repo,
        repo_map=lambda: mapping,
        # Un seul `now` par cycle, échantillonné ici. Le relire par dépôt ferait
        # glisser la fenêtre de CLOCK_SKEW_TOLERANCE au fil d'un cycle long, donc
        # rétrécirait la tolérance pour les derniers dépôts traités.
        clock=lambda: datetime.now(tz=UTC),
        max_refresh_per_cycle=MAX_REFRESH,
    )

    async def refresh_mapping() -> None:
        """Complète la carte, quelques coins par cycle."""
        async with db.sessionmaker() as session:
            rows = (
                await session.execute(
                    select(Token.coin_id, Token.symbol)
                    .order_by(Token.id)
                    .limit(UNIVERSE_SIZE)
                )
            ).all()
        budget = COINS_PER_CYCLE
        for coin_id, symbol in rows:
            if budget <= 0:
                return
            if not coin_id or not symbol or symbol in attempted:
                continue
            # `attempted`, pas `mapping` : la plupart des coins d'un univers de
            # 250 ne declarent aucun depot, et ne les marquer qu'en cas de
            # succes figeait le curseur sur le premier d'entre eux. Il etait
            # alors re-interroge a chaque cycle, indefiniment, et la carte
            # cessait de grandir apres une poignee de coins — l'axe restait
            # inerte en production sans qu'aucun log ne le dise, une liste vide
            # n'etant pas une exception.
            attempted.add(symbol)
            budget -= 1
            try:
                pairs = await gecko.repos_for(coin_id)
            except Exception as exc:
                UNMEASURED.labels(SERVICE, "coin_repos", type(exc).__name__).inc()
                logger.warning("coingecko: %s - %s", coin_id, exc)
                # Retire de `attempted` : un echec transitoire ne doit pas
                # ecarter le coin definitivement, contrairement a une reponse
                # vide qui, elle, est une reponse.
                attempted.discard(symbol)
                continue
            if pairs:
                mapping[symbol] = (coin_id, pairs)
            else:
                logger.info("coingecko: %s ne declare aucun depot", coin_id)

    async def refresh_lists() -> None:
        """Relit les deux README et met le registre à jour."""
        best_of_md, awesome_md = await fetch_readmes()
        best = parse_best_of_crypto(best_of_md) if best_of_md else None
        awesome = parse_awesome_crypto(awesome_md) if awesome_md else None
        for name, result in (("best-of-crypto", best), ("awesome-crypto", awesome)):
            if result is None:
                UNMEASURED.labels(SERVICE, "readme", name).inc()
                continue
            # Le ratio, pas le compte : un README dont le gabarit change fait
            # tomber blocks_seen à zéro sans faire monter unparsed.
            logger.info(
                "github lists: %s — %d/%d entrees lues, %d sans site",
                name,
                len(result.entries),
                result.blocks_seen,
                result.homepage_missing,
            )
            if result.blocks_seen == 0:
                UNMEASURED.labels(SERVICE, "readme", f"{name}:no_blocks").inc()
        written = await persist(
            db,
            merge_entries(
                best.entries if best else [], awesome.entries if awesome else []
            ),
        )
        logger.info("github: registre mis a jour, %d projets", written)

    async def cycle() -> None:
        await refresh_mapping()
        await collector.poll_once()

    app.state.db = db
    app.state.producer = producer
    app.state.client = client
    app.state.gecko = gecko
    app.state.poller = asyncio.create_task(
        run_periodic(cycle, POLL_INTERVAL, name="github-poll")
    )
    app.state.lists_poller = asyncio.create_task(
        run_periodic(refresh_lists, LISTS_REFRESH_SECONDS, name="github-lists")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    if not hasattr(app.state, "poller"):
        return
    for task in (app.state.poller, app.state.lists_poller):
        task.cancel()
    await asyncio.gather(
        app.state.poller, app.state.lists_poller, return_exceptions=True
    )
    await app.state.client.close()
    await app.state.gecko.close()
    await app.state.producer.stop()
    await app.state.db.dispose()


app = create_app("collector-github", on_startup=_startup, on_shutdown=_shutdown)
