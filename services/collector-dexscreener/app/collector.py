"""DexScreener collector: detects new pools / fast movers -> market.dex.events."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.events.base import Source
from cmi_common.events.market import DexEvent
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_PRODUCED, UPSTREAM_REQUESTS

logger = logging.getLogger(__name__)
SERVICE = "collector-dexscreener"

# Only surface pools with at least this liquidity to filter obvious rugs/noise.
MIN_LIQUIDITY_USD = Decimal("10000")
# A pool is "new" if seen for the first time within this cache window.
SEEN_TTL_SECONDS = 3600


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class DexScreenerCollector:
    def __init__(
        self,
        base_url: str,
        cache: Cache,
        producer: EventProducer,
        *,
        chains: list[str] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)
        self._cache = cache
        self._producer = producer
        self._chains = chains or ["solana", "ethereum", "base"]

    async def close(self) -> None:
        await self._client.aclose()

    async def _fetch_boosted(self) -> list[dict[str, Any]]:
        """Latest boosted/trending tokens are a cheap new-pool discovery feed."""
        if not await self._cache.allow("dexscreener", 60, 60):
            raise RuntimeError("DexScreener rate limit exceeded")
        resp = await self._client.get("/token-boosts/latest/v1")
        resp.raise_for_status()
        UPSTREAM_REQUESTS.labels(SERVICE, "dexscreener", "ok").inc()
        data = resp.json()
        return data if isinstance(data, list) else data.get("tokens", [])

    async def _fetch_pairs(self, chain: str, address: str) -> list[dict[str, Any]]:
        resp = await self._client.get(f"/token-pairs/v1/{chain}/{address}")
        resp.raise_for_status()
        return resp.json() or []

    async def poll_once(self) -> int:
        published = 0
        for token in await self._fetch_boosted():
            chain = token.get("chainId")
            address = token.get("tokenAddress")
            if chain not in self._chains or not address:
                continue
            try:
                pairs = await self._fetch_pairs(chain, address)
            except httpx.HTTPError:
                UPSTREAM_REQUESTS.labels(SERVICE, "dexscreener", "error").inc()
                continue
            for pair in pairs:
                event = self._to_event(pair)
                if event is None:
                    continue
                await self._producer.publish(Topic.DEX, event)
                EVENTS_PRODUCED.labels(SERVICE, Topic.DEX.value, event.event_type).inc()
                published += 1
        logger.info("dexscreener poll published %d events", published)
        return published

    def _to_event(self, pair: dict[str, Any]) -> DexEvent | None:
        liquidity = _dec((pair.get("liquidity") or {}).get("usd"))
        if liquidity is not None and liquidity < MIN_LIQUIDITY_USD:
            return None
        base = pair.get("baseToken", {})
        txns = pair.get("txns", {}).get("m5", {})
        change = pair.get("priceChange", {})
        return DexEvent(
            source=Source.DEXSCREENER,
            symbol=str(base.get("symbol", "?")).upper(),
            chain=pair.get("chainId", "unknown"),
            dex_id=pair.get("dexId", "unknown"),
            pair_address=pair.get("pairAddress", ""),
            base_token_address=base.get("address", ""),
            quote_token_symbol=pair.get("quoteToken", {}).get("symbol", "USDC"),
            price_usd=_dec(pair.get("priceUsd")),
            liquidity_usd=liquidity,
            volume_24h_usd=_dec((pair.get("volume") or {}).get("h24")),
            price_change_pct_5m=change.get("m5"),
            price_change_pct_1h=change.get("h1"),
            pair_created_at=pair.get("pairCreatedAt"),
            is_new_pool=True,
            txns_buys_5m=txns.get("buys"),
            txns_sells_5m=txns.get("sells"),
        )
