"""RSSProvider: parse feed XML -> NewsEvent, dedupe via seen-guid cache."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import respx

_spec = importlib.util.spec_from_file_location(
    "rss_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-news" / "app" / "providers" / "rss.py",
)
rss = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = rss
_spec.loader.exec_module(rss)


class FakeCache:
    def __init__(self) -> None:
        self.stored: dict[str, object] = {}

    async def get_json(self, key: str):
        return self.stored.get(key)

    async def set_json(self, key: str, value, ttl_seconds: int | None = None) -> None:
        self.stored[key] = value


_FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>CoinDesk</title>
<item>
  <title>Bitcoin rallies</title>
  <link>https://coindesk.com/a1</link>
  <guid>a1</guid>
  <description>BTC up 10%</description>
  <pubDate>Wed, 02 Oct 2024 12:00:00 GMT</pubDate>
</item>
<item>
  <title>Ether news</title>
  <link>https://coindesk.com/a2</link>
  <guid>a2</guid>
  <description>ETH update</description>
  <pubDate>Wed, 02 Oct 2024 13:00:00 GMT</pubDate>
</item>
</channel></rss>"""


@respx.mock
async def test_parses_feed_into_news_events() -> None:
    respx.get("https://coindesk.com/feed").mock(
        return_value=httpx.Response(200, text=_FEED)
    )
    provider = rss.RSSProvider(
        FakeCache(), feeds=["https://coindesk.com/feed"], source_name="CoinDesk"
    )

    events = await provider.fetch()
    await provider.close()

    assert {e.title for e in events} == {"Bitcoin rallies", "Ether news"}
    a1 = next(e for e in events if e.article_id == "a1")
    assert str(a1.url) == "https://coindesk.com/a1"
    assert a1.source == "rss"
    assert a1.source_name == "CoinDesk"
    assert a1.published_at == 1727870400  # 2024-10-02 12:00:00 UTC


@respx.mock
async def test_seen_guids_are_not_republished() -> None:
    respx.get("https://coindesk.com/feed").mock(
        return_value=httpx.Response(200, text=_FEED)
    )
    cache = FakeCache()
    provider = rss.RSSProvider(
        cache, feeds=["https://coindesk.com/feed"], source_name="CoinDesk"
    )

    first = await provider.fetch()
    second = await provider.fetch()
    await provider.close()

    assert len(first) == 2
    assert second == []  # already-seen guids skipped on the second poll
