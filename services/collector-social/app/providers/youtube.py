"""YouTube Data API social provider -> RawItem per crypto video (key-gated).

Free quota: 10k units/day; search.list costs 100 units, so this is a low-cadence
broad signal (videos rarely carry $CASHTAGs -> mostly scored under MARKET).
Quota exhaustion returns HTTP 403 ``quotaExceeded`` (not 429) with a daily reset.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")


class YouTubeProvider:
    name = "youtube"
    kind = "social"
    rate_limit = (1, 900)  # ~96 searches/day, under the 100/day (100-unit) budget

    def __init__(
        self,
        api_key: str | None,
        *,
        query: str = "cryptocurrency",
        max_results: int = 25,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._api_key = api_key
        self._query = query
        self._max = max_results
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        if not self._api_key:
            return []
        params: dict[str, str | int] = {
            "part": "snippet",
            "type": "video",
            "order": "date",
            "q": self._query,
            "maxResults": self._max,
            "key": self._api_key,
        }
        try:
            resp = await self._client.get(SEARCH_URL, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                raise RateLimitedError(
                    parse_retry_after(exc.response, default=900)
                ) from exc
            if status == 403 and "quotaExceeded" in exc.response.text:
                # Daily quota -- back off an hour (reset is midnight Pacific).
                raise RateLimitedError(3600) from exc
            raise
        body = resp.json()
        if not isinstance(body, dict):  # unexpected shape -> degrade, don't crash
            return []
        items: list[RawItem] = []
        for item in body.get("items") or []:
            if not isinstance(item, dict):
                continue
            vid = (item.get("id") or {}).get("videoId")
            if not vid:
                continue
            sn = item.get("snippet") or {}
            title = sn.get("title", "")
            text = f"{title}. {sn.get('description', '')}".strip()
            items.append(
                RawItem(
                    source="youtube",
                    kind="social",
                    external_id=str(vid),
                    title=title,
                    text=text,
                    url=f"https://www.youtube.com/watch?v={vid}",
                    author=sn.get("channelTitle"),
                    symbols=sorted({m.upper() for m in _CASHTAG.findall(text)}),
                    published_at=_ts(sn.get("publishedAt")),
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
