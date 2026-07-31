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
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from cmi_common.db import Database, ServiceHealth
from cmi_common.observability.metrics import (
    EVENTS_CONSUMED_METRIC,
    EVENTS_PRODUCED_METRIC,
)

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


# ── Prometheus text parsing (pure, unit-tested) ───────────────────────────────
def parse_prometheus(text: str) -> dict[str, list[tuple[dict[str, str], float]]]:
    """Parse Prometheus exposition text into {metric: [(labels, value), ...]}."""
    out: dict[str, list[tuple[dict[str, str], float]]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if "{" in line and "}" in line:
                name = line[: line.index("{")].strip()
                labels_str = line[line.index("{") + 1 : line.rindex("}")]
                rest = line[line.rindex("}") + 1 :].strip()
                labels = {}
                for part in labels_str.split(","):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        labels[k.strip()] = v.strip().strip('"')
            else:
                bits = line.rsplit(" ", 1)
                if len(bits) != 2:
                    continue
                name, rest, labels = bits[0].strip(), bits[1].strip(), {}
            value = float(rest.split()[0])
        except (ValueError, IndexError):
            continue
        out.setdefault(name, []).append((labels, value))
    return out


def metric_sum(parsed: dict[str, list[tuple[dict[str, str], float]]], name: str) -> float:
    return sum(v for _, v in parsed.get(name, []))


def compute_detail(
    parsed: dict[str, list[tuple[dict[str, str], float]]],
    prev: dict[str, float] | None,
    now_ts: float,
) -> tuple[dict, dict[str, float]]:
    """Derive {mem_mb, cpu_pct, throughput_per_min} + the next sample state.

    cpu% and throughput are rates, so they need the previous cumulative sample.
    """
    mem = metric_sum(parsed, "process_resident_memory_bytes")
    cpu_s = metric_sum(parsed, "process_cpu_seconds_total")
    events = metric_sum(parsed, EVENTS_CONSUMED_METRIC) + metric_sum(
        parsed, EVENTS_PRODUCED_METRIC
    )
    detail: dict = {}
    if mem:
        detail["mem_mb"] = round(mem / 1e6)
    if prev:
        dt = max(1e-3, now_ts - prev["ts"])
        detail["cpu_pct"] = round(max(0.0, (cpu_s - prev["cpu"]) / dt * 100), 1)
        detail["throughput_per_min"] = round(max(0.0, (events - prev["events"]) / dt * 60))
    return detail, {"ts": now_ts, "cpu": cpu_s, "events": events}


class HealthCollector:
    def __init__(self, db: Database, interval: float = 15.0) -> None:
        self._db = db
        self._interval = interval
        self._stop = asyncio.Event()
        self._samples: dict[str, dict[str, float]] = {}  # per-service last cumulative sample

    async def _upsert(self, name: str, status: str, healthy: bool, latency_ms: float, detail: dict) -> None:
        async with self._db._sessionmaker() as s:
            now = datetime.now(tz=UTC)
            stmt = insert(ServiceHealth).values(
                service=name, status=status, healthy=healthy, latency_ms=latency_ms, detail=detail, checked_at=now
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["service"],
                set_={"status": status, "healthy": healthy, "latency_ms": latency_ms, "detail": detail, "checked_at": now},
            )
            await s.execute(stmt)
            await s.commit()

    async def _probe_once(self) -> None:
        import httpx  # imported lazily so a missing dep never breaks startup

        targets = resolve_targets()
        async with httpx.AsyncClient(timeout=3.0) as client:
            async def probe(name: str, url: str) -> tuple[str, str, bool, float, dict]:
                t0 = time.perf_counter()
                try:
                    r = await client.get(url)
                    latency = (time.perf_counter() - t0) * 1000
                    healthy = r.status_code < 400
                    status = "healthy" if healthy else "degraded"
                except Exception:
                    return name, "down", False, (time.perf_counter() - t0) * 1000, {}
                # best-effort /metrics scrape → real cpu/mem/throughput
                detail: dict = {}
                try:
                    metrics_url = url.rsplit("/health", 1)[0] + "/metrics"
                    mr = await client.get(metrics_url)
                    if mr.status_code < 400:
                        parsed = parse_prometheus(mr.text)
                        detail, self._samples[name] = compute_detail(
                            parsed, self._samples.get(name), time.time()
                        )
                except Exception:
                    pass
                return name, status, healthy, latency, detail

            results = await asyncio.gather(*(probe(n, u) for n, u in targets.items()))
        for name, status, healthy, latency, detail in results:
            try:
                await self._upsert(name, status, healthy, round(latency, 1), detail)
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
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()
