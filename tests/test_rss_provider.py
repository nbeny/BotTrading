"""RSSProvider: parse feed XML -> RawItem; malformed items skipped, stable id."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from datetime import UTC
from pathlib import Path

import httpx
import respx

_spec = importlib.util.spec_from_file_location(
    "rss_provider",
    Path(__file__).resolve().parents[1]
    / "services"
    / "collector-news"
    / "app"
    / "providers"
    / "rss.py",
)
rss = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = rss
_spec.loader.exec_module(rss)


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
async def test_parses_feed_into_raw_items() -> None:
    feed = "https://coindesk.com/feed"
    respx.get(feed).mock(return_value=httpx.Response(200, text=_FEED))
    provider = rss.RSSProvider(feeds=[feed])

    items = await provider.fetch()
    await provider.close()

    assert {i.title for i in items} == {"Bitcoin rallies", "Ether news"}
    assert {i.source for i in items} == {"rss"}
    assert all(i.kind == "news" for i in items)
    src = hashlib.sha1(feed.encode()).hexdigest()[:16]
    a1 = next(i for i in items if i.title == "Bitcoin rallies")
    assert a1.external_id == f"{src}:a1"  # stable feed-scoped id
    assert a1.url == "https://coindesk.com/a1"
    assert a1.published_at is not None
    assert a1.published_at.astimezone(UTC).hour == 12  # 12:00 UTC


_FEED_BAD_ITEM = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>CoinDesk</title>
<item>
  <title>Missing link item</title>
  <guid>bad1</guid>
  <description>no link element</description>
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
async def test_malformed_item_is_skipped_not_fatal() -> None:
    feed = "https://coindesk.com/feed"
    respx.get(feed).mock(return_value=httpx.Response(200, text=_FEED_BAD_ITEM))
    provider = rss.RSSProvider(feeds=[feed])

    items = await provider.fetch()  # must NOT raise on the bad item
    await provider.close()

    # the bad item (no <link>) is skipped; only the valid one is emitted
    src = hashlib.sha1(feed.encode()).hexdigest()[:16]
    assert [i.external_id for i in items] == [f"{src}:a2"]


@respx.mock
async def test_external_id_uses_stable_feed_hash() -> None:
    feed = "https://coindesk.com/feed"
    respx.get(feed).mock(return_value=httpx.Response(200, text=_FEED))
    provider = rss.RSSProvider(feeds=[feed])

    items = await provider.fetch()
    await provider.close()

    src = hashlib.sha1(feed.encode()).hexdigest()[:16]
    assert all(i.external_id.startswith(f"{src}:") for i in items)
