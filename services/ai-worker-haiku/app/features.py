"""Per-symbol feature store backed by Redis.

The Haiku worker correlates events arriving on different topics for the same
symbol. Rather than hold state in memory (which breaks with replicas), the
latest features per symbol are kept in a short-TTL Redis hash.
"""

from __future__ import annotations

from typing import Any

from cmi_common.cache import Cache

FEATURE_TTL = 900  # 15 min sliding correlation window
KEY = "features:{symbol}"


class FeatureStore:
    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    async def update(self, symbol: str, fields: dict[str, Any]) -> dict[str, Any]:
        current = await self._cache.get_json(KEY.format(symbol=symbol)) or {}
        current.update({k: v for k, v in fields.items() if v is not None})
        await self._cache.set_json(
            KEY.format(symbol=symbol), current, ttl_seconds=FEATURE_TTL
        )
        return current

    async def get(self, symbol: str) -> dict[str, Any]:
        return await self._cache.get_json(KEY.format(symbol=symbol)) or {}
