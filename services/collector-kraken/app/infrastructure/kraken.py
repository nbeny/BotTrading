"""Kraken public REST client — AssetPairs, OHLC, Depth. No authentication.

Kraken reports throttling as HTTP 200 with a non-empty `error` array, so the
body is checked on every call: trusting the status code alone would turn a
rejection into an empty series and stop ingestion silently.
"""

from __future__ import annotations

from typing import Any

import httpx

from cmi_common.sources import RateLimitedError, parse_retry_after

BASE_URL = "https://api.kraken.com/0/public"
_RATE_LIMIT_MARKER = "rate limit"


class KrakenPublicClient:
    name = "kraken"
    #: Kraken tolerates roughly 1 public request/second sustained.
    rate_limit = (60, 60)

    def __init__(self, base_url: str = BASE_URL, *, timeout: float = 20.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "cmi-collector/0.1"}, timeout=timeout
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.get(f"{self._base}/{path}", params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(
                    parse_retry_after(exc.response, default=60)
                ) from exc
            raise
        payload = resp.json()
        errors = payload.get("error") or []
        if errors:
            joined = "; ".join(str(e) for e in errors)
            if _RATE_LIMIT_MARKER in joined.lower():
                raise RateLimitedError(60)
            raise ValueError(f"kraken error: {joined}")
        return payload

    async def asset_pairs(self) -> dict[str, Any]:
        return await self._get("AssetPairs", {})

    async def ohlc(
        self, pair: str, *, interval_minutes: int, since: int | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"pair": pair, "interval": interval_minutes}
        if since is not None:
            params["since"] = since
        return await self._get("OHLC", params)

    async def depth(self, pair: str, *, count: int = 20) -> dict[str, Any]:
        return await self._get("Depth", {"pair": pair, "count": count})
