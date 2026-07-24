"""GDELT news provider -> RawItem per article (keyless, global news + tone).

Uses the GDELT DOC 2.0 API artlist mode. Articles are not ticker-tagged, so
``symbols`` is empty and the sentiment worker scores them under ``MARKET`` —
acceptable for a broad macro-news floor.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

logger = logging.getLogger(__name__)
DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


class GdeltProvider:
    name = "gdelt"
    kind = "news"
    rate_limit = (10, 60)  # GDELT asks for gentle polling

    def __init__(
        self,
        *,
        query: str = "cryptocurrency",
        max_records: int = 75,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._query = query
        self._max = max_records
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=20.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        params = {
            "query": self._query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": self._max,
            "sort": "datedesc",
        }
        try:
            resp = await self._client.get(DOC_URL, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(
                    parse_retry_after(exc.response, default=60)
                ) from exc
            raise
        try:
            articles = resp.json().get("articles", [])
        except ValueError:
            logger.warning("GDELT returned non-JSON body")
            return []
        items: list[RawItem] = []
        for art in articles:
            url = art.get("url")
            if not url:
                continue
            items.append(
                RawItem(
                    source="gdelt",
                    kind="news",
                    external_id=url,
                    title=art.get("title", ""),
                    text=art.get("title", ""),
                    url=url,
                    lang=art.get("language"),
                    published_at=_seendate(art.get("seendate")),
                )
            )
        return items


def _seendate(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None
