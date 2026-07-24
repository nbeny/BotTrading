"""Mastodon social provider -> RawItem per crypto post (keyless public API).

Polls a public hashtag timeline on a configurable instance. Status ``content``
is HTML, so tags are stripped before cashtag extraction.
"""

from __future__ import annotations

import re

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")
_TAG = re.compile(r"<[^>]+>")


class MastodonProvider:
    name = "mastodon"
    kind = "social"
    rate_limit = (60, 60)

    def __init__(
        self,
        *,
        instance: str = "mastodon.social",
        hashtag: str = "crypto",
        limit: int = 40,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._url = f"https://{instance}/api/v1/timelines/tag/{hashtag}"
        self._limit = limit
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        try:
            resp = await self._client.get(self._url, params={"limit": self._limit})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(
                    parse_retry_after(exc.response, default=60)
                ) from exc
            raise
        items: list[RawItem] = []
        for st in resp.json():
            text = _strip_html(st.get("content", ""))
            symbols = sorted({m.upper() for m in _CASHTAG.findall(text)})
            if not symbols:
                continue
            engagement = float(
                st.get("favourites_count", 0)
                + st.get("reblogs_count", 0)
                + st.get("replies_count", 0)
            )
            items.append(
                RawItem(
                    source="mastodon",
                    kind="social",
                    external_id=str(st.get("id")),
                    text=text,
                    url=st.get("url"),
                    author=st.get("account", {}).get("acct"),
                    symbols=symbols,
                    engagement=engagement,
                    lang=st.get("language"),
                )
            )
        return items


def _strip_html(html: str) -> str:
    return _TAG.sub(" ", html).replace("&amp;", "&").strip()
