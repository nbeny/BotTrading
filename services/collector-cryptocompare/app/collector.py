"""CryptoCompare news collector -> market.news.events."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.events.base import Source
from cmi_common.events.news import NewsEvent
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_PRODUCED, UPSTREAM_REQUESTS

logger = logging.getLogger(__name__)
SERVICE = "collector-cryptocompare"
# Redis key holding the last published article id, for incremental polling.
CURSOR_KEY = "cryptocompare:last_id"


class CryptoCompareCollector:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        cache: Cache,
        producer: EventProducer,
    ) -> None:
        headers = {"authorization": f"Apikey {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=15.0
        )
        self._cache = cache
        self._producer = producer

    async def close(self) -> None:
        await self._client.aclose()

    async def _fetch(self) -> list[dict[str, Any]]:
        if not await self._cache.allow("cryptocompare", 30, 60):
            raise RuntimeError("CryptoCompare rate limit exceeded")
        resp = await self._client.get(
            "/data/v2/news/", params={"lang": "EN", "sortOrder": "latest"}
        )
        resp.raise_for_status()
        UPSTREAM_REQUESTS.labels(SERVICE, "cryptocompare", "ok").inc()
        return resp.json().get("Data", [])

    async def poll_once(self) -> int:
        last_id = await self._cache.get_json(CURSOR_KEY)
        articles = await self._fetch()
        published = 0
        newest_id = last_id
        for art in articles:
            art_id = str(art.get("id"))
            if last_id is not None and art_id == str(last_id):
                break  # reached previously seen articles
            event = self._to_event(art)
            await self._producer.publish(Topic.NEWS, event)
            EVENTS_PRODUCED.labels(SERVICE, Topic.NEWS.value, event.event_type).inc()
            published += 1
            if newest_id == last_id or newest_id is None:
                newest_id = art_id
        if articles:
            await self._cache.set_json(
                CURSOR_KEY, str(articles[0].get("id")), ttl_seconds=86400
            )
        logger.info("cryptocompare poll published %d news", published)
        return published

    def _to_event(self, art: dict[str, Any]) -> NewsEvent:
        categories = [c for c in str(art.get("categories", "")).split("|") if c]
        # CryptoCompare tags coins in the 'categories' field; keep upper tickers.
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
