"""api-gateway entrypoint: REST read API + event persister."""

from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI

from cmi_common import Settings, create_app
from cmi_common.auth import require_principal
from cmi_common.cache import Cache
from cmi_common.db import Database
from cmi_common.kafka import EventConsumer, Topic

from . import events_api, journal_api, read_api, regime_api, routers
from .archiver import EventArchiver
from .health_collector import HealthCollector
from .persister import Persister


async def _startup(app: FastAPI, settings: Settings) -> None:
    db = Database(settings.db)
    # Read-only: api-gateway never writes to Redis, only reads the pipeline's
    # own features:{SYM} / market:regime keys for /market/regime.
    app.state.cache = Cache(settings.redis)
    persister = Persister(db)
    consumer = EventConsumer(
        settings.kafka,
        [
            Topic.PRICE,
            Topic.ANALYSIS,
            Topic.DECISION,
            Topic.RISK_APPROVED,
            Topic.EXECUTION,
            Topic.JOURNAL,
            Topic.DERIVATIVES,
            Topic.FUNDAMENTALS,
            Topic.DEVELOPER,
            Topic.CANDLES,
        ],
        persister.handle,
        group_id="api-gateway-persister",
    )
    await consumer.start()
    app.state.db = db
    app.state.consumer = consumer
    app.state.consumer_task = asyncio.create_task(consumer.run())

    # Account snapshots get their own consumer group. Measured in production:
    # the persister runs a growing backlog on the high-volume topics -- 13910
    # messages behind, gaining ~800 a minute -- so one snapshot a minute sat
    # behind thousands of price and analysis events and never landed, while the
    # balance was already sitting correct in Redis. A trickle of operator-facing
    # state must not queue behind the firehose. Same reasoning as the archiver's
    # separate group.
    account_consumer = EventConsumer(
        settings.kafka,
        [Topic.ACCOUNT_SNAPSHOT],
        persister.handle,
        group_id="api-gateway-account",
    )
    await account_consumer.start()
    app.state.account_consumer = account_consumer
    app.state.account_task = asyncio.create_task(account_consumer.run())

    # Raw broadcast archive, so the Command Center feed survives a reload.
    archiver = EventArchiver(db)
    archive_consumer = EventConsumer(
        settings.kafka,
        [
            Topic.PRICE,
            Topic.VOLUME,
            Topic.DEX,
            Topic.SENTIMENT,
            # Topic.ANALYSIS is absent: AnalysisEvent already lands in `signals`
            # via the persister, so archiving it duplicated ~540k rows a day for
            # nothing. Topic.DECISION stays -- it carries DecisionEvent, which is
            # archived, alongside RiskRejectedEvent, which table_for drops for
            # the same reason (it has pipeline_rejections).
            Topic.DECISION,
            Topic.RISK_APPROVED,
            Topic.EXECUTION,
            # The archiver routes unrecognised types to events_signal, but that
            # only fires for events it actually receives -- subscription is an
            # explicit list, not a wildcard. Without this line the spec's
            # "events_signal holds ... AccountSnapshot" would never hold.
            Topic.ACCOUNT_SNAPSHOT,
        ],
        archiver.handle,
        # Its own group: the archive must not compete with the persister for
        # partitions, and a lagging archive must not delay business persistence.
        # Topic.JOURNAL is deliberately absent -- the journal has its own table
        # and 180-day retention. See archiver._ALREADY_PERSISTED for the rest.
        group_id="api-gateway-archiver",
    )
    await archive_consumer.start()
    app.state.archive_consumer = archive_consumer
    app.state.archive_task = asyncio.create_task(archive_consumer.run())

    # Periodic service-health prober → service_health table (feeds /systems).
    collector = HealthCollector(db)
    app.state.health_collector = collector
    app.state.health_task = asyncio.create_task(collector.run())

    # Bind the DB session dependency now that the engine exists.
    app.dependency_overrides[routers.get_session_dep] = db.session


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    await app.state.consumer.stop()
    await app.state.account_consumer.stop()
    await app.state.archive_consumer.stop()
    app.state.health_collector.stop()
    await asyncio.gather(
        app.state.consumer_task,
        app.state.account_task,
        app.state.archive_task,
        app.state.health_task,
        return_exceptions=True,
    )
    await app.state.db.dispose()
    await app.state.cache.close()


app = create_app("api-gateway", on_startup=_startup, on_shutdown=_shutdown)

# Read-only does not mean public. Next.js proxies /api/gateway/* from the open
# internet, so without this every route below served raw content, portfolio
# holdings, positions and the daily AI spend to anyone who knew the path.
# Applied per-router rather than on the app so /health and /metrics stay open
# for the container healthcheck and Prometheus.
_authed = [Depends(require_principal)]

app.include_router(routers.router, dependencies=_authed)
# Live-mode read API backing the web terminal (market + data explorer).
app.include_router(read_api.router, dependencies=_authed)
# Counterfactual-journal summary (own router: read_api is already ~1000 lines).
app.include_router(journal_api.router, dependencies=_authed)
# Archived broadcast stream, so the Command Center feed survives a reload.
app.include_router(events_api.router, dependencies=_authed)
# Market regime strip: Redis (features/regime) + Postgres (dominance/breadth).
app.include_router(regime_api.router, dependencies=_authed)
