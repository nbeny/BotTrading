"""Public Binance USDT-M futures endpoints. No key, no signature.

Binance publishes the consumed request weight on every response, so reading it
lets the collector back off before the exchange starts answering 418, rather
than after.

Open interest comes from the *history* endpoint rather than the snapshot one.
`/fapi/v1/openInterest` returns a bare base-unit figure; `openInterestHist` at
`period=1h&limit=25` spans exactly 24 hours (verified live: consecutive
requests bracket to a 86,400,000 ms span) and carries the USD notional
alongside it, so one request yields both the level and the change. Using the
snapshot would leave `open_interest_change_pct_24h` permanently null and ship
the positioning axis with one of its three terms unable to ever produce a value.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.observability import UPSTREAM_REQUESTS

logger = logging.getLogger(__name__)

SERVICE = "collector-binance-futures"
BASE_URL = "https://fapi.binance.com"
#: Binance allows 2400 weight/minute; stay well under it.
WEIGHT_CEILING = 1800


class BinanceRateLimitedError(RuntimeError):
    def __init__(self, retry_after: float | None) -> None:
        super().__init__("binance rate limited")
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class OpenInterest:
    """USD notional now, and how it moved over 24h.

    `change_pct_24h` is None when the history held a single point or a zero
    baseline: a level with no baseline is a level, not a trend, and reporting
    0.0 would assert a flatness never measured.
    """

    usd: float
    change_pct_24h: float | None


class BinanceFuturesClient:
    def __init__(
        self,
        cache: Cache,
        *,
        timeout: float = 15.0,
        rate_limit_per_min: int = 240,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cache = cache
        self._rate_limit = rate_limit_per_min
        self._client = httpx.AsyncClient(
            base_url=BASE_URL, timeout=timeout, transport=transport
        )
        #: Weight consumed in the current minute, as Binance last reported it.
        self.used_weight = 0

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def near_weight_ceiling(self) -> bool:
        return self.used_weight >= WEIGHT_CEILING

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not await self._cache.allow("binance-futures", self._rate_limit, 60):
            UPSTREAM_REQUESTS.labels(SERVICE, "binance-futures", "throttled").inc()
            raise RuntimeError("Binance futures rate limit exceeded")
        resp = await self._client.get(path, params=params)
        weight = resp.headers.get("X-MBX-USED-WEIGHT-1M")
        if weight is not None:
            self.used_weight = int(weight)
        if resp.status_code in (418, 429):
            UPSTREAM_REQUESTS.labels(SERVICE, "binance-futures", "ratelimit").inc()
            retry_after = resp.headers.get("Retry-After")
            raise BinanceRateLimitedError(float(retry_after) if retry_after else None)
        try:
            resp.raise_for_status()
        except httpx.HTTPError:
            UPSTREAM_REQUESTS.labels(SERVICE, "binance-futures", "error").inc()
            raise
        UPSTREAM_REQUESTS.labels(SERVICE, "binance-futures", "ok").inc()
        return resp.json()

    async def premium_index(self) -> list[dict[str, Any]]:
        """Funding rate and mark price for every perp — one request."""
        payload = await self._get("/fapi/v1/premiumIndex")
        return payload if isinstance(payload, list) else [payload]

    async def open_interest(self, ticker: str) -> OpenInterest | None:
        """Current USD open interest and its 24h change, in one request.

        The change is computed on base units on purpose. `sumOpenInterestValue`
        moves with price as well as with positioning, so a 10% USD rise during a
        10% rally is no new interest at all.
        """
        payload = await self._get(
            "/futures/data/openInterestHist",
            {"symbol": ticker, "period": "1h", "limit": 25},
        )
        if not payload:
            return None
        usd = float(payload[-1]["sumOpenInterestValue"])
        if len(payload) < 2:
            return OpenInterest(usd=usd, change_pct_24h=None)
        oldest = float(payload[0]["sumOpenInterest"])
        newest = float(payload[-1]["sumOpenInterest"])
        change = round(100.0 * (newest - oldest) / oldest, 4) if oldest > 0 else None
        return OpenInterest(usd=usd, change_pct_24h=change)

    async def long_short_ratio(self, ticker: str) -> float | None:
        """Most recent retail long/short account ratio bucket.

        Binance returns the series oldest first, so the current reading is the
        last element, not the first.
        """
        payload = await self._get(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": ticker, "period": "5m", "limit": 1},
        )
        if not payload:
            return None
        return float(payload[-1]["longShortRatio"])
