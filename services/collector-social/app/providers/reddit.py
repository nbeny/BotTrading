"""Reddit social provider -> SocialEvent per cashtag.

Ports the legacy collector-reddit aggregation into a cascade Provider: it
polls /new for each subreddit, aggregates ``$CASHTAG`` mentions/engagement, and
raises ``RateLimited`` when the shared per-minute quota is spent (proactive
guard) or Reddit returns 429 (reactive). Non-commercial free tier — kept as a
fallback behind Bluesky.
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
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")
PREV_KEY = "reddit:mentions:{symbol}"


class RedditProvider:
    name = "reddit"

    def __init__(
        self,
        cache: Cache,
        *,
        subreddits: list[str] | None = None,
        window_minutes: int = 60,
        client_id: str | None = None,
        client_secret: str | None = None,
        user_agent: str = "cmi-collector/0.1",
        rate_limit: int = 60,
    ) -> None:
        self._cache = cache
        self._subreddits = subreddits or ["CryptoCurrency", "CryptoMoonShots", "solana"]
        self._window = window_minutes
        self._client_id = client_id
        self._client_secret = client_secret
        self._rate_limit = rate_limit
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
        self._token = resp.json()["access_token"]
        await self._cache.set_json("reddit:token", self._token, ttl_seconds=3000)

    async def _fetch_new(self, subreddit: str) -> list[dict[str, Any]]:
        if not await self._cache.allow("reddit", self._rate_limit, 60):
            raise RateLimited(60.0)
        if self._token:
            url = f"https://oauth.reddit.com/r/{subreddit}/new"
            headers = {"Authorization": f"bearer {self._token}"}
        else:
            url = f"https://www.reddit.com/r/{subreddit}/new.json"
            headers = {}
        try:
            resp = await self._client.get(url, params={"limit": 100}, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                UPSTREAM_REQUESTS.labels(SERVICE, "reddit", "ratelimit").inc()
                raise RateLimited() from exc
            raise
        UPSTREAM_REQUESTS.labels(SERVICE, "reddit", "ok").inc()
        return [c["data"] for c in resp.json().get("data", {}).get("children", [])]

    async def fetch(self) -> list[SocialEvent]:
        await self._auth()
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
            posts = await self._fetch_new(sub)  # RateLimited propagates to cascade
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

        return [await self._to_event(symbol, a) for symbol, a in agg.items()]

    async def _to_event(self, symbol: str, a: dict[str, Any]) -> SocialEvent:
        key = PREV_KEY.format(symbol=symbol)
        prev = await self._cache.get_json(key) or 0
        mentions = a["mentions"]
        growth = ((mentions - prev) / prev) if prev else 0.0
        await self._cache.set_json(key, mentions, ttl_seconds=self._window * 60)
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
