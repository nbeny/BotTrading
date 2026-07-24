"""Lens v3 social provider -> RawItem per crypto publication (keyless GraphQL).

Public reads need no auth. Queries the global feed with a searchQuery filter;
post text lives in a metadata union, so the query pulls ``content`` from the
common text-bearing subtypes via inline fragments.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

GRAPHQL_URL = "https://api.lens.xyz/graphql"
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")

_QUERY = """
query($q: String!) {
  posts(request: {
    filter: {
      feeds: [{ globalFeed: true }]
      searchQuery: $q
      metadata: { mainContentFocus: [TEXT_ONLY, ARTICLE] }
    }
    pageSize: TEN
  }) {
    items {
      ... on Post {
        id
        timestamp
        author { username { value } }
        stats { comments reposts upvotes: reactions(request: { type: UPVOTE }) }
        metadata {
          ... on TextOnlyMetadata { content }
          ... on ArticleMetadata { content }
        }
      }
    }
  }
}
""".strip()


class LensProvider:
    name = "lens"
    kind = "social"
    rate_limit = (30, 60)

    def __init__(
        self, *, query: str = "crypto", user_agent: str = "cmi-collector/0.1"
    ) -> None:
        self._query = query
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": user_agent,
                "Content-Type": "application/json",
            },
            timeout=20.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        try:
            resp = await self._client.post(
                GRAPHQL_URL,
                json={"query": _QUERY, "variables": {"q": self._query}},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(
                    parse_retry_after(exc.response, default=60)
                ) from exc
            raise
        payload = resp.json()
        if payload.get("errors"):
            return []
        posts = ((payload.get("data") or {}).get("posts") or {}).get("items") or []
        if not isinstance(posts, list):  # unexpected shape -> degrade, don't crash
            return []
        items: list[RawItem] = []
        for post in posts:
            if not isinstance(post, dict):
                continue
            content = (post.get("metadata") or {}).get("content") or ""
            symbols = sorted({m.upper() for m in _CASHTAG.findall(content)})
            if not symbols:
                continue
            pid = post.get("id")
            if not pid:
                continue
            author = ((post.get("author") or {}).get("username") or {}).get("value")
            stats = post.get("stats") or {}
            engagement = float(
                stats.get("comments", 0)
                + stats.get("reposts", 0)
                + stats.get("upvotes", 0)
            )
            items.append(
                RawItem(
                    source="lens",
                    kind="social",
                    external_id=str(pid),
                    text=content,
                    author=author,
                    symbols=symbols,
                    engagement=engagement,
                    published_at=_ts(post.get("timestamp")),
                )
            )
        return items


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
