"""RSS news provider -> NewsEvent. Unlimited, keyless floor source.

Parses standard RSS 2.0 feeds with the stdlib XML parser (no extra deps) and
dedupes items across polls via a per-feed set of seen GUIDs in Redis, so the
cascade's floor never republishes the same article.
"""

from __future__ import annotations

import logging
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

from cmi_common.cache import Cache
from cmi_common.events.base import Source
from cmi_common.events.news import NewsEvent
from cmi_common.observability import UPSTREAM_REQUESTS

logger = logging.getLogger(__name__)
SERVICE = "collector-news"
DEFAULT_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]
SEEN_KEY = "rss:seen:{feed_hash}"


class RSSProvider:
    name = "rss"

    def __init__(
        self,
        cache: Cache,
        *,
        feeds: list[str] | None = None,
        source_name: str = "RSS",
        max_seen: int = 500,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._cache = cache
        self._feeds = feeds or DEFAULT_FEEDS
        self._source_name = source_name
        self._max_seen = max_seen
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[NewsEvent]:
        events: list[NewsEvent] = []
        for feed in self._feeds:
            try:
                resp = await self._client.get(feed)
                resp.raise_for_status()
            except httpx.HTTPError:
                UPSTREAM_REQUESTS.labels(SERVICE, "rss", "error").inc()
                continue
            UPSTREAM_REQUESTS.labels(SERVICE, "rss", "ok").inc()
            events.extend(await self._parse_feed(feed, resp.text))
        return events

    async def _parse_feed(self, feed: str, body: str) -> list[NewsEvent]:
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError:
            logger.warning("failed to parse RSS feed %s", feed)
            return []
        seen_key = SEEN_KEY.format(feed_hash=str(abs(hash(feed))))
        seen = set(await self._cache.get_json(seen_key) or [])
        events: list[NewsEvent] = []
        fresh: list[str] = []
        for item in root.iterfind(".//item"):
            guid = _text(item, "guid") or _text(item, "link") or ""
            if not guid or guid in seen:
                continue
            link = _text(item, "link")
            if not link:
                continue
            fresh.append(guid)
            events.append(
                NewsEvent(
                    source=Source.RSS,
                    article_id=guid,
                    title=_text(item, "title") or "",
                    body=(_text(item, "description") or "")[:4000],
                    url=link,
                    published_at=_epoch(_text(item, "pubDate")),
                    source_name=self._source_name,
                    symbols=[],
                    categories=[],
                    provider_sentiment=None,
                )
            )
        if fresh:
            merged = (fresh + list(seen))[: self._max_seen]
            await self._cache.set_json(seen_key, merged, ttl_seconds=7 * 86400)
        return events


def _text(item: ElementTree.Element, tag: str) -> str | None:
    el = item.find(tag)
    return el.text.strip() if el is not None and el.text else None


def _epoch(pubdate: str | None) -> int:
    if not pubdate:
        return 0
    try:
        return int(parsedate_to_datetime(pubdate).timestamp())
    except (TypeError, ValueError):
        return 0
