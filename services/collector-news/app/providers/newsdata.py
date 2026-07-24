"""NewsData.io news provider -> RawItem per article (key-gated).

Free tier: 200 credits/day, commercial use allowed. Articles are ~12h delayed,
so this is a background news source. Keyed by the stable ``article_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

CRYPTO_URL = "https://newsdata.io/api/1/crypto"


class NewsDataProvider:
    name = "newsdata"
    kind = "news"
    rate_limit = (1, 600)  # ~144/day, under the 200-credit/day free cap

    def __init__(
        self,
        api_key: str | None,
        *,
        query: str = "cryptocurrency",
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._api_key = api_key
        self._query = query
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        if not self._api_key:
            return []
        params: dict[str, str | int] = {
            "apikey": self._api_key,
            "q": self._query,
            "language": "en",
            "size": 10,
        }
        try:
            resp = await self._client.get(CRYPTO_URL, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(
                    parse_retry_after(exc.response, default=900)
                ) from exc
            raise
        body = resp.json()
        if not isinstance(body, dict):  # unexpected shape -> degrade, don't crash
            return []
        items: list[RawItem] = []
        for art in body.get("results") or []:
            if not isinstance(art, dict):
                continue
            aid = art.get("article_id")
            link = art.get("link")
            if not aid or not link:
                continue
            symbols = [c.upper() for c in (art.get("coin") or []) if c]
            items.append(
                RawItem(
                    source="newsdata",
                    kind="news",
                    external_id=str(aid),
                    title=art.get("title", ""),
                    text=art.get("description") or "",
                    url=link,
                    symbols=symbols,
                    lang=art.get("language"),
                    published_at=_pubdate(art.get("pubDate")),
                )
            )
        return items


def _pubdate(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None
