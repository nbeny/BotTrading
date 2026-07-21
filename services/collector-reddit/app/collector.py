"""Reddit collector -> market.social.events.

Aggregates mentions / growth / engagement per symbol over a sampling window.
Uses Reddit's public OAuth API (script app). Falls back to the read-only
``.json`` endpoints when no credentials are configured.
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
SERVICE = "collector-reddit"

# $CASHTAG or bare TICKER heuristic. Kept intentionally simple; the sentiment
# and AI layers do the heavy lifting downstream.
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")
# Redis key prefix storing the previous window's mention count for growth calc.
PREV_KEY = "reddit:mentions:{symbol}"


class RedditCollector:
    def __init__(
        self,
        cache: Cache,
        producer: EventProducer,
        *,
        subreddits: list[str] | None = None,
        window_minutes: int = 60,
        client_id: str | None = None,
        client_secret: str | None = None,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._cache = cache
        self._producer = producer
        self._subreddits = subreddits or ["CryptoCurrency", "CryptoMoonShots", "solana"]
        self._window = window_minutes
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )
        self._token: str | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def _auth(self) -> None:
        if not (self._client_id and self._client_secret):
            return
        cached = await self._cache.get_json("reddit:token")
        if cached:
            self._token = cached
            return
        resp = await self._client.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        await self._cache.set_json("reddit:token", self._token, ttl_seconds=3000)

    async def _fetch_new(self, subreddit: str) -> list[dict[str, Any]]:
        if not await self._cache.allow("reddit", 60, 60):
            raise RuntimeError("Reddit rate limit exceeded")
        if self._token:
            url = f"https://oauth.reddit.com/r/{subreddit}/new"
            headers = {"Authorization": f"bearer {self._token}"}
        else:
            url = f"https://www.reddit.com/r/{subreddit}/new.json"
            headers = {}
        resp = await self._client.get(url, params={"limit": 100}, headers=headers)
        resp.raise_for_status()
        UPSTREAM_REQUESTS.labels(SERVICE, "reddit", "ok").inc()
        return [c["data"] for c in resp.json().get("data", {}).get("children", [])]

    async def poll_once(self) -> int:
        await self._auth()
        # symbol -> aggregated stats
        agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "mentions": 0,
                "authors": set(),
                "engagement": 0.0,
                "titles": [],
                "text": [],
                "subreddit": None,
            }
        )
        for sub in self._subreddits:
            try:
                posts = await self._fetch_new(sub)
            except httpx.HTTPError:
                UPSTREAM_REQUESTS.labels(SERVICE, "reddit", "error").inc()
                continue
            for post in posts:
                title = post.get("title", "")
                body = post.get("selftext", "")
                for symbol in {m.upper() for m in _CASHTAG.findall(f"{title} {body}")}:
                    a = agg[symbol]
                    a["mentions"] += 1
                    a["authors"].add(post.get("author"))
                    a["engagement"] += post.get("score", 0) + post.get(
                        "num_comments", 0
                    )
                    a["subreddit"] = sub
                    if len(a["titles"]) < 5:
                        a["titles"].append(title)
                    a["text"].append(title)

        published = 0
        for symbol, a in agg.items():
            event = await self._to_event(symbol, a)
            await self._producer.publish(Topic.SOCIAL, event)
            EVENTS_PRODUCED.labels(SERVICE, Topic.SOCIAL.value, event.event_type).inc()
            published += 1
        logger.info("reddit poll published %d social events", published)
        return published

    async def _to_event(self, symbol: str, a: dict[str, Any]) -> SocialEvent:
        prev = await self._cache.get_json(PREV_KEY.format(symbol=symbol)) or 0
        mentions = a["mentions"]
        growth = ((mentions - prev) / prev) if prev else 0.0
        await self._cache.set_json(
            PREV_KEY.format(symbol=symbol), mentions, ttl_seconds=self._window * 60
        )
        return SocialEvent(
            source=Source.REDDIT,
            symbol=symbol,
            platform="reddit",
            subreddit=a["subreddit"],
            window_minutes=self._window,
            mentions=mentions,
            mentions_growth=round(growth, 3),
            unique_authors=len(a["authors"]),
            engagement_score=float(a["engagement"]),
            top_posts=a["titles"],
            text_sample=" ".join(a["text"])[:2000],
        )
