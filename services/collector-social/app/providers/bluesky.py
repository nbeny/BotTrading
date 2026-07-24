"""Bluesky (AT Protocol) social provider -> SocialEvent per cashtag.

Uses the keyless, unlimited public search endpoint. Aggregates ``$CASHTAG``
mentions / engagement over a window, mirroring the legacy collectors so the
sentiment layer treats every platform uniformly.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.events.base import Source
from cmi_common.events.social import SocialEvent
from cmi_common.observability import UPSTREAM_REQUESTS
from cmi_common.sources import RateLimited

SERVICE = "collector-social"
SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")
PREV_KEY = "bluesky:mentions:{symbol}"


class BlueskyProvider:
    name = "bluesky"

    def __init__(
        self,
        cache: Cache,
        *,
        query: str = "crypto",
        limit: int = 100,
        window_minutes: int = 60,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._cache = cache
        self._query = query
        self._limit = limit
        self._window = window_minutes
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[SocialEvent]:
        try:
            resp = await self._client.get(
                SEARCH_URL, params={"q": self._query, "limit": self._limit}
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                UPSTREAM_REQUESTS.labels(SERVICE, "bluesky", "ratelimit").inc()
                raise RateLimited() from exc
            raise
        UPSTREAM_REQUESTS.labels(SERVICE, "bluesky", "ok").inc()
        posts = resp.json().get("posts", [])

        agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"mentions": 0, "authors": set(), "engagement": 0.0, "text": []}
        )
        for post in posts:
            text = post.get("record", {}).get("text", "")
            engagement = (
                post.get("likeCount", 0)
                + post.get("repostCount", 0)
                + post.get("replyCount", 0)
            )
            author = post.get("author", {}).get("did")
            for symbol in {m.upper() for m in _CASHTAG.findall(text)}:
                a = agg[symbol]
                a["mentions"] += 1
                a["authors"].add(author)
                a["engagement"] += engagement
                a["text"].append(text)

        return [await self._to_event(symbol, a) for symbol, a in agg.items()]

    async def _to_event(self, symbol: str, a: dict[str, Any]) -> SocialEvent:
        key = PREV_KEY.format(symbol=symbol)
        prev = await self._cache.get_json(key) or 0
        mentions = a["mentions"]
        growth = ((mentions - prev) / prev) if prev else 0.0
        await self._cache.set_json(key, mentions, ttl_seconds=self._window * 60)
        return SocialEvent(
            source=Source.BLUESKY,
            symbol=symbol,
            platform="bluesky",
            window_minutes=self._window,
            mentions=mentions,
            mentions_growth=round(growth, 3),
            unique_authors=len(a["authors"]),
            engagement_score=float(a["engagement"]),
            top_posts=[t[:120] for t in a["text"][:5]],
            text_sample=" ".join(a["text"])[:2000],
        )
