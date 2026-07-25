"""Periodic health prober → the ``service_health`` table.

Persistence-first: every service exposes ``/health`` (via ``cmi_common.app``);
this loop probes them on an interval and upserts the latest status/latency so
``/systems/overview`` reads durable rows rather than doing live fan-out on each
request. Targets are configurable via ``CMI_HEALTH_TARGETS`` (``name=url,...``).
Fully defensive: probe/DB failures are logged, never fatal.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert

from cmi_common.db import Database, ServiceHealth

logger = logging.getLogger(__name__)

DEFAULT_TARGETS: dict[str, str] = {
    "api-gateway": "http://api-gateway:8000/health",
    "websocket-gateway": "http://websocket-gateway:8000/health",
    "control-api": "http://control-api:8000/health",
    "collector-coingecko": "http://collector-coingecko:8000/health",
    "collector-dexscreener": "http://collector-dexscreener:8000/health",
    "collector-social": "http://collector-social:8000/health",
    "collector-news": "http://collector-news:8000/health",
    "sentiment-service": "http://sentiment-service:8000/health",
    "ai-worker-haiku": "http://ai-worker-haiku:8000/health",
    "ai-worker-sonnet": "http://ai-worker-sonnet:8000/health",
    "decision-engine": "http://decision-engine:8000/health",
    "risk-engine": "http://risk-engine:8000/health",
    "trading-engine": "http://trading-engine:8000/health",
}


def resolve_targets() -> dict[str, str]:
    raw = os.getenv("CMI_HEALTH_TARGETS")
    if not raw:
        return DEFAULT_TARGETS
    out: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            name, url = pair.split("=", 1)
            out[name.strip()] = url.strip()
    return out or DEFAULT_TARGETS


class HealthCollector:
    def __init__(self, db: Database, interval: float = 15.0) -> None:
        self._db = db
        self._interval = interval
        self._stop = asyncio.Event()

    async def _upsert(self, name: str, status: str, healthy: bool, latency_ms: float) -> None:
        async with self._db._sessionmaker() as s:  # noqa: SLF001
            now = datetime.now(tz=timezone.utc)
            stmt = insert(ServiceHealth).values(
                service=name, status=status, healthy=healthy, latency_ms=latency_ms, detail={}, checked_at=now
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["service"],
                set_={"status": status, "healthy": healthy, "latency_ms": latency_ms, "checked_at": now},
            )
            await s.execute(stmt)
            await s.commit()

    async def _probe_once(self) -> None:
        import httpx  # imported lazily so a missing dep never breaks startup

        targets = resolve_targets()
        async with httpx.AsyncClient(timeout=3.0) as client:
            async def probe(name: str, url: str) -> tuple[str, str, bool, float]:
                t0 = time.perf_counter()
                try:
                    r = await client.get(url)
                    latency = (time.perf_counter() - t0) * 1000
                    healthy = r.status_code < 400
                    return name, ("healthy" if healthy else "degraded"), healthy, latency
                except Exception:
                    return name, "down", False, (time.perf_counter() - t0) * 1000

            results = await asyncio.gather(*(probe(n, u) for n, u in targets.items()))
        for name, status, healthy, latency in results:
            try:
                await self._upsert(name, status, healthy, round(latency, 1))
            except Exception:
                logger.exception("service_health upsert failed for %s", name)

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._probe_once()
            except Exception:
                logger.exception("health probe cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()
