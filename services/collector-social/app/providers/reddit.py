"""Reddit provider -> one RawItem per crypto post."""

from __future__ import annotations

import re
from typing import Any

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")


class RedditProvider:
    name = "reddit"
    kind = "social"
    rate_limit = (60, 60)

    def __init__(
        self,
        *,
        subreddits: list[str] | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._subreddits = subreddits or [
            "CryptoCurrency",
            "CryptoMoonShots",
            "solana",
        ]
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )
        self._token: str | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def _ensure_token(self) -> str | None:
        """Application-only (userless) OAuth token. Reddit blocks the public
        .json endpoint from datacenter IPs (403); the OAuth API does not."""
        if not (self._client_id and self._client_secret):
            return None
        if self._token:
            return self._token
        resp = await self._client.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
        )
        resp.raise_for_status()
        self._token = resp.json().get("access_token")
        return self._token

    async def _fetch_sub(
        self, sub: str, *, retried: bool = False
    ) -> list[dict[str, Any]]:
        token = await self._ensure_token()
        if token:
            url = f"https://oauth.reddit.com/r/{sub}/new"
            headers = {"Authorization": f"Bearer {token}"}
        else:
            url = f"https://www.reddit.com/r/{sub}/new.json"
            headers = {}
        try:
            resp = await self._client.get(url, params={"limit": 100}, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 401 and token and not retried:
                self._token = None  # expired -> refresh once
                return await self._fetch_sub(sub, retried=True)
            if code == 429:
                raise RateLimitedError(
                    parse_retry_after(exc.response, default=60)
                ) from exc
            raise
        return [c["data"] for c in resp.json().get("data", {}).get("children", [])]

    async def fetch(self) -> list[RawItem]:
        items: list[RawItem] = []
        for sub in self._subreddits:
            for post in await self._fetch_sub(sub):
                title = post.get("title", "")
                body = post.get("selftext", "")
                symbols = sorted(
                    {m.upper() for m in _CASHTAG.findall(f"{title} {body}")}
                )
                if not symbols:
                    continue
                items.append(
                    RawItem(
                        source="reddit",
                        kind="social",
                        external_id=str(post.get("name") or post.get("id")),
                        title=title,
                        text=f"{title} {body}".strip(),
                        author=post.get("author"),
                        symbols=symbols,
                        engagement=float(
                            post.get("score", 0) + post.get("num_comments", 0)
                        ),
                    )
                )
        return items
