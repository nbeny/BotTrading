"""Redis helpers: JSON cache, token-bucket rate limiting and distributed locks."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.lock import Lock

from ..config import RedisSettings


class Cache:
    """Small async facade over redis-py used by every service."""

    def __init__(self, settings: RedisSettings) -> None:
        self._redis: Redis = Redis.from_url(settings.url, decode_responses=True)

    @property
    def client(self) -> Redis:
        return self._redis

    async def close(self) -> None:
        await self._redis.aclose()

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    # --- JSON cache -----------------------------------------------------
    async def get_json(self, key: str) -> Any | None:
        raw = await self._redis.get(key)
        return json.loads(raw) if raw else None

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        payload = json.dumps(value, default=str)
        # ttl_seconds <= 0 means "persist, no expiry" (used for durable keys like
        # trading:runtime). Redis rejects `EX 0` ("invalid expire time"), so omit
        # the expiry entirely in that case instead of passing ex=0.
        if ttl_seconds and ttl_seconds > 0:
            await self._redis.set(key, payload, ex=ttl_seconds)
        else:
            await self._redis.set(key, payload)

    # --- Rate limiting (fixed-window token bucket) ----------------------
    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Return True if the call is within ``limit`` per ``window_seconds``.

        Used by collectors to respect provider API quotas across replicas.
        """
        bucket = f"rl:{key}"
        current = await self._redis.incr(bucket)
        if current == 1:
            await self._redis.expire(bucket, window_seconds)
        return current <= limit

    # --- Distributed lock ----------------------------------------------
    @asynccontextmanager
    async def lock(
        self, name: str, timeout: float = 30.0, blocking: bool = True
    ) -> AsyncIterator[Lock]:
        """Cluster-wide lock, e.g. to ensure a single collector poll runs."""
        lock = self._redis.lock(f"lock:{name}", timeout=timeout, blocking=blocking)
        acquired = await lock.acquire()
        try:
            yield lock
        finally:
            if acquired:
                try:
                    await lock.release()
                except Exception:
                    pass
