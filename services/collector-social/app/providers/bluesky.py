"""Bluesky provider -> one RawItem per crypto post."""

from __future__ import annotations

import re

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

# Unauthenticated AppView — 403s from datacenter IPs. When an app-password is
# provided we create a session and hit the PDS endpoint with a Bearer token,
# which is not IP-blocked.
PUBLIC_SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
AUTH_SEARCH_URL = "https://bsky.social/xrpc/app.bsky.feed.searchPosts"
SESSION_URL = "https://bsky.social/xrpc/com.atproto.server.createSession"
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
        identifier: str | None = None,
        app_password: str | None = None,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._query = query
        self._limit = limit
        self._identifier = identifier
        self._app_password = app_password
        self._jwt: str | None = None
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _ensure_session(self) -> str | None:
        if not (self._identifier and self._app_password):
            return None
        if self._jwt:
            return self._jwt
        resp = await self._client.post(
            SESSION_URL,
            json={"identifier": self._identifier, "password": self._app_password},
        )
        resp.raise_for_status()
        self._jwt = resp.json().get("accessJwt")
        return self._jwt

    async def fetch(self, *, retried: bool = False) -> list[RawItem]:
        jwt = await self._ensure_session()
        url = AUTH_SEARCH_URL if jwt else PUBLIC_SEARCH_URL
        headers = {"Authorization": f"Bearer {jwt}"} if jwt else {}
        try:
            resp = await self._client.get(
                url, params={"q": self._query, "limit": self._limit}, headers=headers
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (401, 400) and jwt and not retried:
                self._jwt = None  # expired -> refresh session once
                return await self.fetch(retried=True)
            if code == 429:
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
