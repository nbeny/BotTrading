"""RSS news provider -> one RawItem per article (keyless floor)."""

from __future__ import annotations

import hashlib
import logging
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

from cmi_common.sources import RawItem

logger = logging.getLogger(__name__)
DEFAULT_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]


class RSSProvider:
    name = "rss"
    kind = "news"
    rate_limit = (600, 60)  # effectively unlimited; loop cadence bounds it

    def __init__(
        self,
        *,
        feeds: list[str] | None = None,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._feeds = feeds or DEFAULT_FEEDS
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        items: list[RawItem] = []
        for feed in self._feeds:
            try:
                resp = await self._client.get(feed)
                resp.raise_for_status()
            except httpx.HTTPError:
                logger.warning("RSS feed unreachable: %s", feed)
                continue
            items.extend(self._parse(feed, resp.text))
        return items

    def _parse(self, feed: str, body: str) -> list[RawItem]:
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError:
            logger.warning("failed to parse RSS feed %s", feed)
            return []
        src = hashlib.sha1(feed.encode()).hexdigest()[:16]
        out: list[RawItem] = []
        for node in root.iterfind(".//item"):
            guid = _text(node, "guid") or _text(node, "link")
            link = _text(node, "link")
            if not guid or not link:
                continue
            try:
                out.append(
                    RawItem(
                        source="rss",
                        kind="news",
                        external_id=f"{src}:{guid}",
                        title=_text(node, "title") or "",
                        text=(_text(node, "description") or "")[:4000],
                        url=link,
                        published_at=_dt(_text(node, "pubDate")),
                    )
                )
            except Exception:
                logger.warning("skipping malformed RSS item %s in %s", guid, feed)
        return out


def _text(node: ElementTree.Element, tag: str) -> str | None:
    el = node.find(tag)
    return el.text.strip() if el is not None and el.text else None


def _dt(pubdate: str | None):
    if not pubdate:
        return None
    try:
        return parsedate_to_datetime(pubdate)
    except (TypeError, ValueError):
        return None
