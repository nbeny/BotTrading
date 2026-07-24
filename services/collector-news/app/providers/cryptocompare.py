"""CryptoCompare news provider -> one RawItem per article."""

from __future__ import annotations

from typing import Any

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after


class CryptoCompareNewsProvider:
    name = "cryptocompare"
    kind = "news"
    rate_limit = (2, 60)  # ~86k/month under the 100k free cap

    def __init__(self, base_url: str, api_key: str | None) -> None:
        headers = {"authorization": f"Apikey {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        try:
            resp = await self._client.get(
                "/data/v2/news/", params={"lang": "EN", "sortOrder": "latest"}
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(
                    parse_retry_after(exc.response, default=60)
                ) from exc
            raise
        return [self._to_item(a) for a in resp.json().get("Data", [])]

    def _to_item(self, art: dict[str, Any]) -> RawItem:
        categories = [c for c in str(art.get("categories", "")).split("|") if c]
        symbols = [c.upper() for c in categories if c.isupper() and len(c) <= 6]
        return RawItem(
            source="cryptocompare",
            kind="news",
            external_id=str(art.get("id")),
            title=art.get("title", ""),
            text=art.get("body", "")[:4000],
            url=art.get("url"),
            symbols=symbols,
        )
