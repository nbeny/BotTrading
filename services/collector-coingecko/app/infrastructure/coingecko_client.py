"""HTTP client for the CoinGecko API (infrastructure layer)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.observability import UPSTREAM_REQUESTS

logger = logging.getLogger(__name__)

SERVICE = "collector-coingecko"


class CoinGeckoClient:
    """Async wrapper over CoinGecko with Redis-backed caching + rate limiting."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        cache: Cache,
        *,
        rate_limit_per_min: int = 30,
        timeout: float = 15.0,
    ) -> None:
        headers = {"x-cg-pro-api-key": api_key} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=timeout
        )
        self._cache = cache
        self._rate_limit = rate_limit_per_min

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        # Respect provider quota across all replicas via Redis token bucket.
        if not await self._cache.allow("coingecko", self._rate_limit, 60):
            raise RuntimeError("CoinGecko rate limit exceeded")
        try:
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
            UPSTREAM_REQUESTS.labels(SERVICE, "coingecko", "ok").inc()
            return resp.json()
        except httpx.HTTPError:
            UPSTREAM_REQUESTS.labels(SERVICE, "coingecko", "error").inc()
            raise

    async def markets(
        self, vs_currency: str = "usd", per_page: int = 100, page: int = 1
    ) -> list[dict[str, Any]]:
        """Top coins by market cap with price / volume / change fields."""
        return await self._get(
            "/coins/markets",
            {
                "vs_currency": vs_currency,
                "order": "market_cap_desc",
                "per_page": per_page,
                "page": page,
                "price_change_percentage": "1h,24h,7d",
                "sparkline": "false",
            },
        )

    async def trending(self) -> list[str]:
        """Coin ids currently trending on CoinGecko search."""
        cache_key = "coingecko:trending"
        cached = await self._cache.get_json(cache_key)
        if cached is not None:
            return cached
        data = await self._get("/search/trending")
        ids = [c["item"]["id"] for c in data.get("coins", [])]
        await self._cache.set_json(cache_key, ids, ttl_seconds=120)
        return ids
