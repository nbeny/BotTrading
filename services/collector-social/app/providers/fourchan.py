"""4chan /biz/ social provider -> RawItem per crypto thread OP (keyless).

Reads the public catalog.json. Noisy 'degen' signal; OP comment (``com``) is
HTML-ish, so tags are stripped before cashtag extraction.
"""

from __future__ import annotations

import html
import re

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

CATALOG_URL = "https://a.4cdn.org/biz/catalog.json"
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")
_TAG = re.compile(r"<[^>]+>")


class FourchanProvider:
    name = "fourchan"
    kind = "social"
    rate_limit = (30, 60)  # 4chan asks for <=1 req/sec

    def __init__(self, *, user_agent: str = "cmi-collector/0.1") -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        try:
            resp = await self._client.get(CATALOG_URL)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(
                    parse_retry_after(exc.response, default=60)
                ) from exc
            raise
        data = resp.json()
        if not isinstance(data, list):  # unexpected shape -> degrade, don't crash
            return []
        items: list[RawItem] = []
        for page in data:
            for thread in page.get("threads", []):
                no = thread.get("no")
                if no is None:
                    continue
                text = _strip_html(thread.get("com", ""))
                # No symbol extraction here: the collector's normalizer resolves
                # symbols for every provider and overwrites whatever we set. This
                # used to require an explicit $TICKER and `continue` otherwise,
                # which discarded 100% of /biz/ -- the source produced zero rows
                # for its entire life.
                items.append(
                    RawItem(
                        source="fourchan",
                        kind="social",
                        external_id=str(no),
                        text=text,
                        engagement=float(thread.get("replies", 0)),
                    )
                )
        return items


def _strip_html(content: str) -> str:
    # html.unescape covers all entities; strip tags first so entity text shows.
    return html.unescape(_TAG.sub(" ", content)).strip()
