"""CryptoCompare news provider -> NewsEvent.

Ports the legacy collector into a cascade Provider: incremental polling via a
Redis cursor, and a proactive per-minute quota guard (``cache.allow``) that
raises ``RateLimitedError`` so the cascade fails over to RSS once the free monthly
budget's per-minute allotment is spent.
"""

from __future__ import annotations

from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.events.base import Source
from cmi_common.events.news import NewsEvent
from cmi_common.observability import UPSTREAM_REQUESTS
from cmi_common.sources import RateLimitedError

SERVICE = "collector-news"
CURSOR_KEY = "cryptocompare:last_id"


class CryptoCompareNewsProvider:
    name = "cryptocompare-news"

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        cache: Cache,
        *,
        rate_limit: int = 2,
    ) -> None:
        headers = {"authorization": f"Apikey {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=15.0
        )
        self._cache = cache
        self._rate_limit = rate_limit

    async def close(self) -> None:
        await self._client.aclose()

    async def _fetch_raw(self) -> list[dict[str, Any]]:
        # ~2 calls/min => ~86k/month, under the 100k free-tier cap.
        if not await self._cache.allow("cryptocompare-news", self._rate_limit, 60):
            raise RateLimitedError(60.0)
        try:
            resp = await self._client.get(
                "/data/v2/news/", params={"lang": "EN", "sortOrder": "latest"}
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                UPSTREAM_REQUESTS.labels(SERVICE, "cryptocompare", "ratelimit").inc()
                raise RateLimitedError() from exc
            raise
        UPSTREAM_REQUESTS.labels(SERVICE, "cryptocompare", "ok").inc()
        return resp.json().get("Data", [])

    async def fetch(self) -> list[NewsEvent]:
        last_id = await self._cache.get_json(CURSOR_KEY)
        articles = await self._fetch_raw()
        events: list[NewsEvent] = []
        for art in articles:
            if last_id is not None and str(art.get("id")) == str(last_id):
                break
            events.append(self._to_event(art))
        if articles:
            await self._cache.set_json(
                CURSOR_KEY, str(articles[0].get("id")), ttl_seconds=86400
            )
        return events

    def _to_event(self, art: dict[str, Any]) -> NewsEvent:
        categories = [c for c in str(art.get("categories", "")).split("|") if c]
        symbols = [c.upper() for c in categories if c.isupper() and len(c) <= 6]
        return NewsEvent(
            source=Source.CRYPTOCOMPARE,
            article_id=str(art.get("id")),
            title=art.get("title", ""),
            body=art.get("body", "")[:4000],
            url=art.get("url"),
            published_at=int(art.get("published_on", 0)),
            source_name=art.get("source_info", {}).get("name", art.get("source", "?")),
            symbols=symbols,
            categories=categories,
            provider_sentiment=None,
        )
