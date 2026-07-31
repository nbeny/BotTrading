# services/trading-engine/app/reconcile.py
"""Background reconciliation: detect closed positions and free exposure.

Source of truth is Kraken. The engine only manages positions it opened (tracked
in Redis set ``trading:positions``). A tracked position that Kraken no longer
reports has closed. A Kraken position we do not track is logged, never touched.
"""
from __future__ import annotations

import asyncio
import logging

from cmi_common.events.decision import Direction
from cmi_common.events.execution import ExecutionEvent, ExecutionKind
from cmi_common.kafka import Topic
from cmi_common.observability import EVENTS_PRODUCED

from .engine import SERVICE

logger = logging.getLogger(__name__)

POSITIONS_SET = "trading:positions"
EXPOSURE_KEY = "risk:exposure"


class Reconciler:
    def __init__(self, cache, producer, kraken) -> None:
        self._cache = cache
        self._producer = producer
        self._kraken = kraken
        self._stopped = asyncio.Event()

    async def run(self, interval_s: int) -> None:
        while not self._stopped.is_set():
            try:
                await self.sweep()
            except Exception:
                logger.exception("reconcile sweep failed")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval_s)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stopped.set()

    async def sweep(self) -> None:
        open_resp = await self._kraken.get_open_positions()
        open_pairs = {p["symbol"] for p in open_resp.get("openPositions", [])}

        tracked = await self._cache.client.smembers(POSITIONS_SET)
        for event_id in tracked:
            pos = await self._cache.get_json(f"trading:position:{event_id}")
            if not pos:
                await self._cache.client.srem(POSITIONS_SET, event_id)
                continue
            if pos["pair"] in open_pairs:
                continue  # still open
            await self._on_closed(event_id, pos)

        # Surface (but never act on) positions we did not open.
        tracked_pairs = set()
        for event_id in tracked:
            pos = await self._cache.get_json(f"trading:position:{event_id}")
            if pos:
                tracked_pairs.add(pos["pair"])
        for pair in open_pairs - tracked_pairs:
            logger.warning("untracked Kraken position %s — leaving untouched", pair)

    async def _on_closed(self, event_id: str, pos: dict) -> None:
        exposure = float(await self._cache.get_json(EXPOSURE_KEY) or 0.0)
        freed = max(0.0, exposure - float(pos.get("position_size_pct", 0.0)))
        await self._cache.set_json(EXPOSURE_KEY, round(freed, 4), ttl_seconds=0)
        await self._cache.client.srem(POSITIONS_SET, event_id)
        ev = ExecutionEvent(
            kind=ExecutionKind.CLOSED,
            symbol=pos["symbol"],
            direction=Direction((pos.get("side") == "sell" and "short") or "long"),
            risk_event_id=event_id,
            size=pos.get("size"),
        )
        await self._producer.publish(Topic.EXECUTION, ev)
        EVENTS_PRODUCED.labels(SERVICE, Topic.EXECUTION.value, ev.event_type).inc()
        logger.info("CLOSED %s (event %s), exposure -> %s", pos["symbol"], event_id, freed)
