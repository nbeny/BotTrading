"""Bluesky provider -> one RawItem per crypto post."""

from __future__ import annotations

import re

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")


class BlueskyProvider:
    name = "bluesky"
    kind = "social"
    rate_limit = (250, 300)  # ~3000/5min shared; stay well under

    def __init__(
        self,
        *,
        query: str = "crypto",
        limit: int = 100,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._query = query
        self._limit = limit
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        try:
            resp = await self._client.get(
                SEARCH_URL, params={"q": self._query, "limit": self._limit}
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(
                    parse_retry_after(exc.response, default=300)
                ) from exc
            raise
        posts = resp.json().get("posts", [])
        items: list[RawItem] = []
        for post in posts:
            text = post.get("record", {}).get("text", "")
            symbols = sorted({m.upper() for m in _CASHTAG.findall(text)})
            if not symbols:
                continue
            engagement = float(
                post.get("likeCount", 0)
                + post.get("repostCount", 0)
                + post.get("replyCount", 0)
            )
            items.append(
                RawItem(
                    source="bluesky",
                    kind="social",
                    external_id=str(post.get("uri") or post.get("cid")),
                    text=text,
                    author=post.get("author", {}).get("did"),
                    symbols=symbols,
                    engagement=engagement,
                )
            )
        return items
