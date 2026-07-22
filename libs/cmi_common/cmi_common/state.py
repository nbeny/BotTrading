"""Read-only platform state: runtime settings + live positions/pending from
Redis, trades from the DB. Shared by control-api (write plane) and api-gateway
(read plane) so the Redis key layout lives in exactly one place."""
from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select

RUNTIME_KEY = "trading:runtime"
POSITIONS_SET = "trading:positions"
PENDING_SET = "trading:pending"


class StateReader:
    def __init__(self, cache, *, db) -> None:
        self._cache = cache
        self._db = db

    async def settings(self) -> dict[str, Any]:
        return (await self._cache.get_json(RUNTIME_KEY)) or {}

    async def positions(self) -> list[dict[str, Any]]:
        ids = await self._cache.client.smembers(POSITIONS_SET)
        out = []
        for event_id in ids:
            pos = await self._cache.get_json(f"trading:position:{event_id}")
            if pos:
                out.append({"event_id": event_id, **pos})
        return out

    async def pending(self) -> list[dict[str, Any]]:
        ids = await self._cache.client.smembers(PENDING_SET)
        out = []
        for event_id in ids:
            sig = await self._cache.get_json(f"trading:pending:{event_id}")
            if sig:
                out.append({"event_id": event_id, **sig})
        return out

    async def trades(self, limit: int = 50) -> list[dict[str, Any]]:
        from cmi_common.db import Trade
        async with self._db.session() as s:
            rows = (await s.execute(
                select(Trade).order_by(desc(Trade.created_at)).limit(limit)
            )).scalars().all()
            return [
                {"symbol": r.symbol, "status": r.status, "entry_price": r.entry_price,
                 "fill_price": r.fill_price, "pnl": r.pnl, "created_at": r.created_at}
                for r in rows
            ]
