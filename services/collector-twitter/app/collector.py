"""Twitter/X collector -> market.social.events.

Aggregates cashtag mentions / growth / engagement per symbol over a sampling
window using the X API v2 recent-search endpoint (bearer-token auth). Mirrors
``collector-reddit`` so downstream (sentiment-service, AI workers) treats both
platforms uniformly. Without a bearer token the collector is a no-op — X has no
unauthenticated search endpoint to fall back to.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.events.base import Source
from cmi_common.events.social import SocialEvent
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_PRODUCED, UPSTREAM_REQUESTS

logger = logging.getLogger(__name__)
SERVICE = "collector-twitter"

# $CASHTAG heuristic, identical to the Reddit collector so both platforms key
# symbols the same way. The sentiment/AI layers do the heavy lifting downstream.
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")
# Redis key prefix storing the previous window's mention count for growth calc.
PREV_KEY = "twitter:mentions:{symbol}"
SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"


class TwitterCollector:
    def __init__(
        self,
        cache: Cache,
        producer: EventProducer,
        *,
        bearer_token: str | None = None,
        query: str = "(crypto OR cryptocurrency OR altcoin) -is:retweet lang:en",
        max_results: int = 100,
        window_minutes: int = 60,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._cache = cache
        self._producer = producer
        self._bearer = bearer_token
        self._query = query
        self._max_results = max_results
        self._window = window_minutes
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _fetch_recent(self) -> list[dict[str, Any]]:
        if not await self._cache.allow("twitter", 60, 60):
            raise RuntimeError("Twitter rate limit exceeded")
        resp = await self._client.get(
            SEARCH_URL,
            params={
                "query": self._query,
                "max_results": self._max_results,
                "tweet.fields": "public_metrics,author_id,lang",
            },
            headers={"Authorization": f"Bearer {self._bearer}"},
        )
        resp.raise_for_status()
        UPSTREAM_REQUESTS.labels(SERVICE, "twitter", "ok").inc()
        return resp.json().get("data", [])

    async def poll_once(self) -> int:
        if not self._bearer:
            logger.info("no TWITTER_BEARER_TOKEN configured; skipping poll")
            return 0
        try:
            tweets = await self._fetch_recent()
        except httpx.HTTPError:
            UPSTREAM_REQUESTS.labels(SERVICE, "twitter", "error").inc()
            return 0

        # symbol -> aggregated stats
        agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "mentions": 0,
                "authors": set(),
                "engagement": 0.0,
                "text": [],
            }
        )
        for tweet in tweets:
            text = tweet.get("text", "")
            pm = tweet.get("public_metrics", {})
            engagement = (
                pm.get("like_count", 0)
                + pm.get("retweet_count", 0)
                + pm.get("reply_count", 0)
                + pm.get("quote_count", 0)
            )
            for symbol in {m.upper() for m in _CASHTAG.findall(text)}:
                a = agg[symbol]
                a["mentions"] += 1
                a["authors"].add(tweet.get("author_id"))
                a["engagement"] += engagement
                a["text"].append(text)

        published = 0
        for symbol, a in agg.items():
            event = await self._to_event(symbol, a)
            await self._producer.publish(Topic.SOCIAL, event)
            EVENTS_PRODUCED.labels(SERVICE, Topic.SOCIAL.value, event.event_type).inc()
            published += 1
        logger.info("twitter poll published %d social events", published)
        return published

    async def _to_event(self, symbol: str, a: dict[str, Any]) -> SocialEvent:
        prev = await self._cache.get_json(PREV_KEY.format(symbol=symbol)) or 0
        mentions = a["mentions"]
        growth = ((mentions - prev) / prev) if prev else 0.0
        await self._cache.set_json(
            PREV_KEY.format(symbol=symbol), mentions, ttl_seconds=self._window * 60
        )
        return SocialEvent(
            source=Source.TWITTER,
            symbol=symbol,
            platform="twitter",
            window_minutes=self._window,
            mentions=mentions,
            mentions_growth=round(growth, 3),
            unique_authors=len(a["authors"]),
            engagement_score=float(a["engagement"]),
            top_posts=[t[:120] for t in a["text"][:5]],
            text_sample=" ".join(a["text"])[:2000],
        )
