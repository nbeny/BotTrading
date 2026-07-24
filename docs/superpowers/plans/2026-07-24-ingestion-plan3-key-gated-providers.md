# Ingestion Plan 3 — Key-Gated Providers (verified free tiers)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four more free-tier ingestion providers whose APIs were live-verified in 2026 — NewsData.io (news), Neynar/Farcaster (social), YouTube Data API (social), Lens v3 (social) — each following the Plan-1 `Provider` pattern. Key-gated providers register only when their env key is set (no-op otherwise); Lens is keyless.

**Architecture:** Each provider maps its verified API response shape to `RawItem` and raises `RateLimitedError` on throttling. They register into the existing fan-out `AdaptivePollLoop` lists in the two collector services — conditionally for the three key-gated ones (`if os.getenv(...): providers.append(...)`), unconditionally for keyless Lens.

**Tech Stack:** Python 3.12, httpx (incl. one GraphQL POST for Lens), respx (tests), Pydantic v2, the `cmi_common.sources` framework.

**Verified API facts (encoded below, from live 2026 research):**
- **NewsData.io** — `GET https://newsdata.io/api/1/crypto?apikey=…&q=…&language=en&size=10`; list `results[]`; fields `article_id`, `title`, `link`, `description`, `pubDate` (`"YYYY-MM-DD HH:MM:SS"` UTC), `coin` (list of lowercase symbols), `language`. Free 200 credits/day, commercial allowed.
- **Neynar** — `GET https://api.neynar.com/v2/farcaster/cast/search/?q=…&limit=25`, header `x-api-key`; list `result.casts[]`; fields `hash`, `text`, `author.username`, `timestamp` (ISO8601), `reactions.likes_count`, `reactions.recasts_count`, `replies.count`.
- **YouTube** — `GET https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&order=date&q=…&maxResults=25&key=…`; list `items[]`; fields `id.videoId`, `snippet.title`, `snippet.description`, `snippet.channelTitle`, `snippet.publishedAt` (RFC3339). Quota exhaustion returns **HTTP 403** with body containing `"quotaExceeded"` (NOT 429).
- **Lens v3** — `POST https://api.lens.xyz/graphql` (keyless read); `data.posts.items[]`; per item `id`, `timestamp`, `author.username.value`, `stats.{comments,reposts}` + aliased upvote reactions, `metadata.content` (union subtype — inline fragments).

**Deferred (not in this plan):** Messari & CoinGecko-news (paid), StockTwits (dev registration closed), Coinpaprika (events not news), Telegram (needs a dedicated MTProto-session collector — separate plan).

---

## File Structure
- `services/collector-news/app/providers/newsdata.py` — `NewsDataProvider`
- `services/collector-social/app/providers/neynar.py` — `NeynarProvider`
- `services/collector-social/app/providers/youtube.py` — `YouTubeProvider`
- `services/collector-social/app/providers/lens.py` — `LensProvider`
- `services/collector-news/app/main.py` / `services/collector-social/app/main.py` — MODIFY (conditional registration)
- `tests/test_newsdata_provider.py`, `test_neynar_provider.py`, `test_youtube_provider.py`, `test_lens_provider.py`
- Ops: `.env.example`, `docker-compose.yml`, `CLAUDE.md`

---

## Task 1: `NewsDataProvider` (news, key-gated)

**Files:**
- Create: `services/collector-news/app/providers/newsdata.py`
- Test: `tests/test_newsdata_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_newsdata_provider.py`:

```python
"""NewsDataProvider: /api/1/crypto results[] -> RawItem per article."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "newsdata_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-news" / "app" / "providers" / "newsdata.py",
)
nd = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = nd
_spec.loader.exec_module(nd)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _article(aid: str, title: str) -> dict:
    return {
        "article_id": aid, "title": title, "link": "https://x/a",
        "description": "body text", "pubDate": "2024-01-02 12:00:00",
        "coin": ["btc"], "language": "english",
    }


@respx.mock
async def test_maps_results_to_rawitems() -> None:
    respx.get("https://newsdata.io/api/1/crypto").mock(return_value=httpx.Response(
        200, json={"status": "success", "results": [
            _article("a1", "Bitcoin surges"), _article("a2", "Ether news"),
        ]}))
    provider = nd.NewsDataProvider("KEY")
    items = await provider.fetch()
    await provider.close()

    assert {i.title for i in items} == {"Bitcoin surges", "Ether news"}
    a = next(i for i in items if i.external_id == "a1")
    assert a.source == "newsdata"
    assert a.kind == "news"
    assert a.symbols == ["BTC"]
    assert str(a.url) == "https://x/a"
    assert a.published_at is not None


async def test_no_key_is_noop() -> None:
    provider = nd.NewsDataProvider(None)
    assert await provider.fetch() == []
    await provider.close()


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get("https://newsdata.io/api/1/crypto").mock(return_value=httpx.Response(429))
    provider = nd.NewsDataProvider("KEY")
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_newsdata_provider.py -q -p no:cacheprovider`
Expected: FAIL — module missing

- [ ] **Step 3: Implement**

Create `services/collector-news/app/providers/newsdata.py`:

```python
"""NewsData.io news provider -> RawItem per article (key-gated).

Free tier: 200 credits/day, commercial use allowed. Articles are ~12h delayed,
so this is a background news source. Keyed by the stable ``article_id``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

CRYPTO_URL = "https://newsdata.io/api/1/crypto"


class NewsDataProvider:
    name = "newsdata"
    kind = "news"
    rate_limit = (1, 600)  # ~144/day, under the 200-credit/day free cap

    def __init__(self, api_key: str | None, *, query: str = "cryptocurrency",
                 user_agent: str = "cmi-collector/0.1") -> None:
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
        params = {
            "apikey": self._api_key, "q": self._query,
            "language": "en", "size": 10,
        }
        try:
            resp = await self._client.get(CRYPTO_URL, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(parse_retry_after(exc.response, default=900)) from exc
            raise
        items: list[RawItem] = []
        for art in resp.json().get("results", []):
            aid = art.get("article_id")
            link = art.get("link")
            if not aid or not link:
                continue
            symbols = [c.upper() for c in (art.get("coin") or []) if c]
            items.append(RawItem(
                source="newsdata", kind="news", external_id=str(aid),
                title=art.get("title", ""), text=art.get("description") or "",
                url=link, symbols=symbols, lang=art.get("language"),
                published_at=_pubdate(art.get("pubDate")),
            ))
        return items


def _pubdate(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_newsdata_provider.py -q -p no:cacheprovider`
Expected: PASS (3 tests)

- [ ] **Step 5: Register (conditional) in `collector-news/app/main.py` + commit**

Add import `from .providers.newsdata import NewsDataProvider`. After the base `providers = [...]` list is built, add:

```python
    if os.getenv("NEWSDATA_API_KEY"):
        providers.append(NewsDataProvider(os.getenv("NEWSDATA_API_KEY")))
```

Run `python -m py_compile services/collector-news/app/main.py` + `python -m ruff check services/collector-news/app`.

```bash
git add services/collector-news/app/providers/newsdata.py services/collector-news/app/main.py tests/test_newsdata_provider.py
git commit -m "feat(collector-news): add key-gated NewsData.io provider"
```

---

## Task 2: `NeynarProvider` (Farcaster social, key-gated)

**Files:**
- Create: `services/collector-social/app/providers/neynar.py`
- Test: `tests/test_neynar_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_neynar_provider.py`:

```python
"""NeynarProvider: Farcaster cast search -> RawItem per crypto cast."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "neynar_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-social" / "app" / "providers" / "neynar.py",
)
ny = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = ny
_spec.loader.exec_module(ny)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _cast(h: str, text: str, likes: int = 0) -> dict:
    return {
        "hash": h, "text": text, "timestamp": "2024-01-02T12:00:00.000Z",
        "author": {"username": "alice", "fid": 1},
        "reactions": {"likes_count": likes, "recasts_count": 0},
        "replies": {"count": 0},
    }


@respx.mock
async def test_maps_casts_with_cashtags() -> None:
    respx.get(ny.SEARCH_URL).mock(return_value=httpx.Response(200, json={
        "result": {"casts": [
            _cast("0xaa", "$BTC looking strong", likes=5),
            _cast("0xbb", "no ticker here"),
        ]}}))
    provider = ny.NeynarProvider("KEY")
    items = await provider.fetch()
    await provider.close()

    assert len(items) == 1
    it = items[0]
    assert it.source == "neynar"
    assert it.kind == "social"
    assert it.external_id == "0xaa"
    assert it.symbols == ["BTC"]
    assert it.author == "alice"
    assert it.engagement == 5.0


async def test_no_key_is_noop() -> None:
    provider = ny.NeynarProvider(None)
    assert await provider.fetch() == []
    await provider.close()


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get(ny.SEARCH_URL).mock(return_value=httpx.Response(429))
    provider = ny.NeynarProvider("KEY")
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_neynar_provider.py -q -p no:cacheprovider`
Expected: FAIL — module missing

- [ ] **Step 3: Implement**

Create `services/collector-social/app/providers/neynar.py`:

```python
"""Neynar (Farcaster) social provider -> RawItem per crypto cast (key-gated).

Free tier: 10M credits/month. Searches casts; keeps only those with a cashtag.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

SEARCH_URL = "https://api.neynar.com/v2/farcaster/cast/search/"
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")


class NeynarProvider:
    name = "neynar"
    kind = "social"
    rate_limit = (60, 60)  # free plan 600 RPM; stay well under

    def __init__(self, api_key: str | None, *,
                 query: str = "bitcoin OR ethereum OR crypto", limit: int = 25,
                 user_agent: str = "cmi-collector/0.1") -> None:
        self._api_key = api_key
        self._query = query
        self._limit = limit
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        if not self._api_key:
            return []
        try:
            resp = await self._client.get(
                SEARCH_URL,
                params={"q": self._query, "limit": self._limit},
                headers={"x-api-key": self._api_key},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(parse_retry_after(exc.response, default=60)) from exc
            raise
        casts = resp.json().get("result", {}).get("casts", [])
        items: list[RawItem] = []
        for cast in casts:
            text = cast.get("text", "")
            symbols = sorted({m.upper() for m in _CASHTAG.findall(text)})
            if not symbols:
                continue
            h = cast.get("hash")
            if not h:
                continue
            reactions = cast.get("reactions", {})
            engagement = float(
                reactions.get("likes_count", 0) + reactions.get("recasts_count", 0)
                + cast.get("replies", {}).get("count", 0)
            )
            items.append(RawItem(
                source="neynar", kind="social", external_id=str(h),
                text=text, author=cast.get("author", {}).get("username"),
                symbols=symbols, engagement=engagement,
                published_at=_ts(cast.get("timestamp")),
            ))
        return items


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_neynar_provider.py -q -p no:cacheprovider`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit** (registration happens in Task 4 alongside YouTube + Lens)

```bash
git add services/collector-social/app/providers/neynar.py tests/test_neynar_provider.py
git commit -m "feat(collector-social): add key-gated Neynar/Farcaster provider"
```

---

## Task 3: `YouTubeProvider` (social, key-gated)

**Files:**
- Create: `services/collector-social/app/providers/youtube.py`
- Test: `tests/test_youtube_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_youtube_provider.py`:

```python
"""YouTubeProvider: search.list items -> RawItem per crypto video."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "youtube_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-social" / "app" / "providers" / "youtube.py",
)
yt = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = yt
_spec.loader.exec_module(yt)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _item(vid: str, title: str) -> dict:
    return {
        "id": {"videoId": vid},
        "snippet": {
            "title": title, "description": "desc", "channelTitle": "CryptoChan",
            "publishedAt": "2024-01-02T12:00:00Z",
        },
    }


@respx.mock
async def test_maps_videos_to_rawitems() -> None:
    respx.get(yt.SEARCH_URL).mock(return_value=httpx.Response(200, json={
        "items": [_item("v1", "Bitcoin analysis"), _item("v2", "ETH update")]}))
    provider = yt.YouTubeProvider("KEY")
    items = await provider.fetch()
    await provider.close()

    assert {i.external_id for i in items} == {"v1", "v2"}
    a = next(i for i in items if i.external_id == "v1")
    assert a.source == "youtube"
    assert a.kind == "social"
    assert "Bitcoin analysis" in a.text
    assert str(a.url) == "https://www.youtube.com/watch?v=v1"
    assert a.author == "CryptoChan"
    assert a.published_at is not None


async def test_no_key_is_noop() -> None:
    provider = yt.YouTubeProvider(None)
    assert await provider.fetch() == []
    await provider.close()


@respx.mock
async def test_quota_exceeded_403_raises_rate_limited() -> None:
    respx.get(yt.SEARCH_URL).mock(return_value=httpx.Response(
        403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}}))
    provider = yt.YouTubeProvider("KEY")
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_youtube_provider.py -q -p no:cacheprovider`
Expected: FAIL — module missing

- [ ] **Step 3: Implement**

Create `services/collector-social/app/providers/youtube.py`:

```python
"""YouTube Data API social provider -> RawItem per crypto video (key-gated).

Free quota: 10k units/day; search.list costs 100 units, so this is a low-cadence
broad signal (videos rarely carry $CASHTAGs → mostly scored under MARKET).
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

    def __init__(self, api_key: str | None, *, query: str = "cryptocurrency",
                 max_results: int = 25, user_agent: str = "cmi-collector/0.1") -> None:
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
        params = {
            "part": "snippet", "type": "video", "order": "date",
            "q": self._query, "maxResults": self._max, "key": self._api_key,
        }
        try:
            resp = await self._client.get(SEARCH_URL, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                raise RateLimitedError(parse_retry_after(exc.response, default=900)) from exc
            if status == 403 and "quotaExceeded" in exc.response.text:
                # Daily quota — back off an hour (reset is midnight Pacific).
                raise RateLimitedError(3600) from exc
            raise
        items: list[RawItem] = []
        for item in resp.json().get("items", []):
            vid = item.get("id", {}).get("videoId")
            if not vid:
                continue
            sn = item.get("snippet", {})
            title = sn.get("title", "")
            text = f"{title}. {sn.get('description', '')}".strip()
            items.append(RawItem(
                source="youtube", kind="social", external_id=str(vid),
                title=title, text=text,
                url=f"https://www.youtube.com/watch?v={vid}",
                author=sn.get("channelTitle"),
                symbols=sorted({m.upper() for m in _CASHTAG.findall(text)}),
                published_at=_ts(sn.get("publishedAt")),
            ))
        return items


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_youtube_provider.py -q -p no:cacheprovider`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/collector-social/app/providers/youtube.py tests/test_youtube_provider.py
git commit -m "feat(collector-social): add key-gated YouTube provider"
```

---

## Task 4: `LensProvider` (social, keyless GraphQL) + register the 3 social providers

**Files:**
- Create: `services/collector-social/app/providers/lens.py`
- Modify: `services/collector-social/app/main.py`
- Test: `tests/test_lens_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_lens_provider.py`:

```python
"""LensProvider: GraphQL posts search -> RawItem per crypto publication."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "lens_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-social" / "app" / "providers" / "lens.py",
)
ln = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = ln
_spec.loader.exec_module(ln)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _post(pid: str, content: str, comments: int = 0) -> dict:
    return {
        "id": pid, "timestamp": "2024-01-02T12:00:00Z",
        "author": {"username": {"value": "lens/alice"}},
        "stats": {"comments": comments, "reposts": 0, "upvotes": 0},
        "metadata": {"content": content},
    }


@respx.mock
async def test_maps_posts_with_cashtags() -> None:
    respx.post(ln.GRAPHQL_URL).mock(return_value=httpx.Response(200, json={
        "data": {"posts": {"items": [
            _post("0x01", "$BTC to the moon", comments=3),
            _post("0x02", "gm no ticker"),
        ]}}}))
    provider = ln.LensProvider()
    items = await provider.fetch()
    await provider.close()

    assert len(items) == 1
    it = items[0]
    assert it.source == "lens"
    assert it.kind == "social"
    assert it.external_id == "0x01"
    assert it.symbols == ["BTC"]
    assert it.author == "lens/alice"
    assert it.engagement == 3.0


@respx.mock
async def test_graphql_errors_return_empty() -> None:
    respx.post(ln.GRAPHQL_URL).mock(return_value=httpx.Response(
        200, json={"errors": [{"message": "bad query"}]}))
    provider = ln.LensProvider()
    assert await provider.fetch() == []
    await provider.close()


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.post(ln.GRAPHQL_URL).mock(return_value=httpx.Response(429))
    provider = ln.LensProvider()
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_lens_provider.py -q -p no:cacheprovider`
Expected: FAIL — module missing

- [ ] **Step 3: Implement**

Create `services/collector-social/app/providers/lens.py`:

```python
"""Lens v3 social provider -> RawItem per crypto publication (keyless GraphQL).

Public reads need no auth. Queries the global feed with a searchQuery filter;
post text lives in a metadata union, so the query pulls ``content`` from the
common text-bearing subtypes via inline fragments.
"""

from __future__ import annotations

import re

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

GRAPHQL_URL = "https://api.lens.xyz/graphql"
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")

_QUERY = """
query($q: String!) {
  posts(request: {
    filter: {
      feeds: [{ globalFeed: true }]
      searchQuery: $q
      metadata: { mainContentFocus: [TEXT_ONLY, ARTICLE] }
    }
    pageSize: TEN
  }) {
    items {
      ... on Post {
        id
        timestamp
        author { username { value } }
        stats { comments reposts upvotes: reactions(request: { type: UPVOTE }) }
        metadata {
          ... on TextOnlyMetadata { content }
          ... on ArticleMetadata { content }
        }
      }
    }
  }
}
""".strip()


class LensProvider:
    name = "lens"
    kind = "social"
    rate_limit = (30, 60)

    def __init__(self, *, query: str = "crypto",
                 user_agent: str = "cmi-collector/0.1") -> None:
        self._query = query
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Content-Type": "application/json"},
            timeout=20.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        try:
            resp = await self._client.post(
                GRAPHQL_URL,
                json={"query": _QUERY, "variables": {"q": self._query}},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(parse_retry_after(exc.response, default=60)) from exc
            raise
        payload = resp.json()
        if payload.get("errors"):
            return []
        posts = (
            payload.get("data", {}).get("posts", {}).get("items", []) or []
        )
        items: list[RawItem] = []
        for post in posts:
            content = (post.get("metadata") or {}).get("content") or ""
            symbols = sorted({m.upper() for m in _CASHTAG.findall(content)})
            if not symbols:
                continue
            pid = post.get("id")
            if not pid:
                continue
            author = ((post.get("author") or {}).get("username") or {}).get("value")
            stats = post.get("stats") or {}
            engagement = float(
                stats.get("comments", 0) + stats.get("reposts", 0)
                + stats.get("upvotes", 0)
            )
            items.append(RawItem(
                source="lens", kind="social", external_id=str(pid),
                text=content, author=author, symbols=symbols,
                engagement=engagement, published_at=_ts(post.get("timestamp")),
            ))
        return items


def _ts(value: str | None):
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_lens_provider.py -q -p no:cacheprovider`
Expected: PASS (3 tests)

- [ ] **Step 5: Register all three new social providers in `collector-social/app/main.py`**

Add imports:
```python
from .providers.neynar import NeynarProvider
from .providers.youtube import YouTubeProvider
from .providers.lens import LensProvider
```
Inside `_build_providers`, after the existing appends, add:
```python
    if os.getenv("NEYNAR_API_KEY"):
        providers.append(NeynarProvider(os.getenv("NEYNAR_API_KEY")))
    if os.getenv("YOUTUBE_API_KEY"):
        providers.append(YouTubeProvider(os.getenv("YOUTUBE_API_KEY")))
    providers.append(LensProvider(query=os.getenv("LENS_QUERY", "crypto")))
```

Run `python -m py_compile services/collector-social/app/main.py` + `python -m ruff check services/collector-social/app`.

- [ ] **Step 6: Commit**

```bash
git add services/collector-social/app/providers/lens.py services/collector-social/app/main.py tests/test_lens_provider.py
git commit -m "feat(collector-social): add keyless Lens provider + register neynar/youtube/lens"
```

---

## Task 5: Ops & docs

**Files:**
- Modify: `.env.example`, `docker-compose.yml`, `CLAUDE.md`

- [ ] **Step 1: `.env.example`** — under the ingestion section add:

```
# Key-gated ingestion sources (Plan 3) — leave blank to disable that source
NEWSDATA_API_KEY=
NEYNAR_API_KEY=
YOUTUBE_API_KEY=
LENS_QUERY=crypto
```

- [ ] **Step 2: `docker-compose.yml`** — pass the keys through:
- `collector-news.environment`: add `NEWSDATA_API_KEY: ${NEWSDATA_API_KEY:-}`.
- `collector-social.environment`: add `NEYNAR_API_KEY: ${NEYNAR_API_KEY:-}`, `YOUTUBE_API_KEY: ${YOUTUBE_API_KEY:-}`, `LENS_QUERY: ${LENS_QUERY:-crypto}`.
Validate: `python -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('yaml OK')"`.

- [ ] **Step 3: `CLAUDE.md`** — update the pipeline source lists to `collector-social` (Bluesky, Reddit, Mastodon, 4chan, Farcaster, YouTube, Lens) and `collector-news` (CryptoCompare, RSS, GDELT, NewsData). Add a one-line note: "key-gated sources (Farcaster, YouTube, NewsData) activate when their env key is set; Telegram/StockTwits/Messari/CoinGecko-news deferred (paid or session-based)."

- [ ] **Step 4: Full suite + lint** — `python -m pytest -q -p no:cacheprovider` (expect all green: +12 provider tests). `python -m ruff check` + `ruff format --check` on the four new provider files + both mains; fix any ≤88-col/format. Pre-existing repo-wide lint failures out of scope.

- [ ] **Step 5: Commit**

```bash
git add .env.example docker-compose.yml CLAUDE.md
git commit -m "feat: wire NewsData/Neynar/YouTube/Lens env + docs"
```

---

## Self-Review

**Spec coverage:** Verified-free key-gated providers built: NewsData.io (Task 1), Neynar (Task 2), YouTube (Task 3), Lens (Task 4). Deferred with reasons (Messari/CoinGecko-news paid; StockTwits registration closed; Coinpaprika events-only; Telegram needs a dedicated MTProto-session collector) — documented in Task 3 doc note, not built.

**Placeholder scan:** every provider has full fetch + mapping + tests, using the field names verified in live research (NewsData `results[]/article_id/link/description/pubDate/coin`; Neynar `result.casts[]/hash/text/author.username/reactions`; YouTube `items[]/id.videoId/snippet.*`; Lens `data.posts.items[]/id/metadata.content/author.username.value/stats`). No TBD.

**Type consistency:** all four expose `name`/`kind`/`rate_limit`/`fetch()->list[RawItem]`/`close`; key-gated ones (`NewsData`, `Neynar`, `YouTube`) take `api_key: str | None` and return `[]` when falsy; each raises `RateLimitedError` (YouTube also maps 403 `quotaExceeded`). Registration guards (`if os.getenv(...)`) match the `api_key` constructor arg. `RawItem` kwargs match `cmi_common.sources.RawItem`.

**Special-case correctness noted:** YouTube quota exhaustion is HTTP 403 `quotaExceeded` (not 429) → mapped to `RateLimitedError(3600)`; NewsData is ~12h delayed (background source); Lens content is a metadata union (query uses inline fragments; `metadata.content` may be absent → empty content → filtered by the cashtag guard). **Symbol-less handling differs by provider on purpose:** Neynar and Lens SKIP casts/posts with no cashtag (targeted social signal); **YouTube KEEPS symbol-less videos (→ scored under MARKET, like GDELT/RSS)** because video titles rarely carry `$CASHTAG`s and filtering them would make YouTube produce almost nothing. This is intentional, not an inconsistency — do not add a `if not symbols: continue` guard to the YouTube provider.
