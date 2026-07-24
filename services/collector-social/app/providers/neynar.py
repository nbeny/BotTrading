"""Neynar (Farcaster) social provider -> RawItem per crypto cast (key-gated).

Free tier: 10M credits/month. Searches casts; keeps only those with a cashtag.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

SEARCH_URL = "https://api.neynar.com/v2/farcaster/cast/search/"
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")


class NeynarProvider:
    name = "neynar"
    kind = "social"
    rate_limit = (60, 60)  # free plan 600 RPM; stay well under

    def __init__(
        self,
        api_key: str | None,
        *,
        query: str = "bitcoin OR ethereum OR crypto",
        limit: int = 25,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._api_key = api_key
        self._query = query
        self._limit = limit
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        if not self._api_key:
            return []
        try:
            resp = await self._client.get(
                SEARCH_URL,
                params={"q": self._query, "limit": self._limit},
                headers={"x-api-key": self._api_key},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(
                    parse_retry_after(exc.response, default=60)
                ) from exc
            raise
        casts = resp.json().get("result", {}).get("casts", [])
        items: list[RawItem] = []
        for cast in casts:
            text = cast.get("text", "")
            symbols = sorted({m.upper() for m in _CASHTAG.findall(text)})
            if not symbols:
                continue
            h = cast.get("hash")
            if not h:
                continue
            reactions = cast.get("reactions", {})
            engagement = float(
                reactions.get("likes_count", 0)
                + reactions.get("recasts_count", 0)
                + cast.get("replies", {}).get("count", 0)
            )
            items.append(
                RawItem(
                    source="neynar",
                    kind="social",
                    external_id=str(h),
                    text=text,
                    author=cast.get("author", {}).get("username"),
                    symbols=symbols,
                    engagement=engagement,
                    published_at=_ts(cast.get("timestamp")),
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
