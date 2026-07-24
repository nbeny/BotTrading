# Ingestion Plan 2 — Haiku Rewire + Keyless Providers

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the Plan-1 gap where `ai-worker-haiku`'s social/news features went dark (rewire them off the `SentimentEvent` stream it already consumes), and broaden free-tier ingestion with three keyless providers — GDELT (news), Mastodon and 4chan /biz/ (social) — plus Google News via config.

**Architecture:** Haiku stops subscribing to `market.{news,social}.events` (nothing produces them anymore) and derives `has_news`/`has_social` from `SentimentEvent.input_kind`. Three new `Provider`s follow the Plan-1 pattern (`fetch() -> list[RawItem]`, per-source rate limit, keyless) and register into the existing fan-out `AdaptivePollLoop` lists in the two collector services.

**Tech Stack:** Python 3.12, httpx, respx (tests), Pydantic v2, the `cmi_common.sources` framework from Plan 1.

**Prior art:** Plan 1 (`2026-07-24-multi-platform-ingestion-foundation.md`) — merged. Spec: `2026-07-24-multi-platform-ingestion-db-sentiment-design.md`. This is Plan 2 of the platform rollout; key-gated providers (Farcaster, StockTwits, Lens, YouTube, Telegram, Messari, NewsData, CoinGecko-news) are deferred to Plan 3 pending live API-shape verification.

---

## File Structure

**Haiku rewire:**
- `services/ai-worker-haiku/app/worker.py` — MODIFY `_extract` + `_ready`, drop `SocialEvent`/`NewsEvent` imports
- `services/ai-worker-haiku/app/main.py` — MODIFY subscription list (drop `Topic.NEWS`, `Topic.SOCIAL`)
- `tests/test_haiku_extract.py` — CREATE

**New providers:**
- `services/collector-news/app/providers/gdelt.py` — CREATE `GdeltProvider`
- `services/collector-social/app/providers/mastodon.py` — CREATE `MastodonProvider`
- `services/collector-social/app/providers/fourchan.py` — CREATE `FourchanProvider`
- `services/collector-news/app/main.py` / `services/collector-social/app/main.py` — MODIFY provider lists
- `tests/test_gdelt_provider.py`, `test_mastodon_provider.py`, `test_fourchan_provider.py` — CREATE

**Ops:** `.env.example`, `docker-compose.yml` (env), `CLAUDE.md` (remove the I1 ⚠️ once haiku is rewired)

---

## Phase A — Haiku rewire (closes the Plan-1 gap)

### Task 1: Derive social/news features from `SentimentEvent`

**Files:**
- Modify: `services/ai-worker-haiku/app/worker.py`
- Test: `tests/test_haiku_extract.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_haiku_extract.py`:

```python
"""HaikuWorker._extract derives social/news presence from SentimentEvent.input_kind."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from cmi_common.events.sentiment import SentimentEvent

_spec = importlib.util.spec_from_file_location(
    "haiku_worker",
    Path(__file__).resolve().parents[1]
    / "services" / "ai-worker-haiku" / "app" / "worker.py",
)
hw = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = hw
_spec.loader.exec_module(hw)


def _extract(event):
    # _extract is a bound method needing self, but it only reads instance-free
    # branches here; call it on a bare instance with stubbed collaborators.
    worker = hw.HaikuWorker.__new__(hw.HaikuWorker)
    return worker._extract(event)


def test_news_sentiment_sets_has_news() -> None:
    ev = SentimentEvent(symbol="BTC", sentiment_score=0.6, confidence=0.8,
                        model_name="m", input_kind="news", sample_size=1)
    symbol, fields, topic = _extract(ev)
    assert symbol == "BTC"
    assert fields["sentiment_score"] == 0.6
    assert fields["has_news"] is True
    assert "has_social" not in fields


def test_social_sentiment_sets_has_social() -> None:
    ev = SentimentEvent(symbol="ETH", sentiment_score=-0.2, confidence=0.5,
                        model_name="m", input_kind="social", sample_size=1)
    symbol, fields, topic = _extract(ev)
    assert symbol == "ETH"
    assert fields["has_social"] is True
    assert "has_news" not in fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_haiku_extract.py -q -p no:cacheprovider`
Expected: FAIL — the current `SentimentEvent` branch returns only `sentiment_score`/`sentiment_confidence`, so `has_news`/`has_social` are absent.

- [ ] **Step 3: Rewrite the `SentimentEvent` branch and remove `SocialEvent`/`NewsEvent` branches**

In `services/ai-worker-haiku/app/worker.py`:

Replace the three branches (`SentimentEvent`, `SocialEvent`, `NewsEvent`) in `_extract` with a single enriched `SentimentEvent` branch:

```python
        if isinstance(event, SentimentEvent):
            fields = {
                "sentiment_score": event.sentiment_score,
                "sentiment_confidence": event.confidence,
            }
            # Since Plan-1, social/news reach haiku only via sentiment; derive
            # presence flags from input_kind (collectors no longer emit
            # Social/NewsEvent on Kafka).
            if event.input_kind == "news":
                fields["has_news"] = True
            elif event.input_kind == "social":
                fields["has_social"] = True
            return event.symbol, fields, Topic.SENTIMENT.value
```

Remove the now-dead `SocialEvent` and `NewsEvent` isinstance branches entirely.

Update the imports at the top of the file — drop `NewsEvent` and `SocialEvent`:

```python
from cmi_common.events import (
    AnalysisEvent,
    BaseEvent,
    DexEvent,
    PriceEvent,
    SentimentEvent,
    VolumeEvent,
)
```

Update `_ready` so social presence still counts as a signal (it previously used `social_growth`, now gone):

```python
    @staticmethod
    def _ready(f: dict) -> bool:
        has_market = any(k in f for k in ("price_change_pct_24h", "liquidity_usd"))
        has_signal = any(
            k in f for k in ("sentiment_score", "has_social", "volume_spike_ratio")
        )
        return has_market and has_signal
```

> `AnalysisEvent(... social_growth=features.get("social_growth"))` stays — it now resolves to `None` (the raw mentions-growth signal is intentionally dropped, per the approved rewire decision). Leave that line; it remains valid.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_haiku_extract.py -q -p no:cacheprovider`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/ai-worker-haiku/app/worker.py tests/test_haiku_extract.py
git commit -m "feat(ai-worker-haiku): derive social/news features from SentimentEvent"
```

---

### Task 2: Drop the orphaned NEWS/SOCIAL subscription

**Files:**
- Modify: `services/ai-worker-haiku/app/main.py`

- [ ] **Step 1: Update the consumer topic list**

In `services/ai-worker-haiku/app/main.py`, change the `EventConsumer` topic list from
`[Topic.PRICE, Topic.VOLUME, Topic.DEX, Topic.NEWS, Topic.SOCIAL, Topic.SENTIMENT]`
to:

```python
    consumer = EventConsumer(
        settings.kafka,
        [Topic.PRICE, Topic.VOLUME, Topic.DEX, Topic.SENTIMENT],
        worker.handle,
        group_id="ai-worker-haiku",
    )
```

- [ ] **Step 2: Verify it compiles and the suite is green**

Run: `python -m py_compile services/ai-worker-haiku/app/main.py`
Run: `python -m pytest -q -p no:cacheprovider` — expect all green (no test asserts the old NEWS/SOCIAL subscription).
Run: `python -m ruff check services/ai-worker-haiku/app` — expect clean.

- [ ] **Step 3: Commit**

```bash
git add services/ai-worker-haiku/app/main.py
git commit -m "refactor(ai-worker-haiku): stop subscribing to now-silent NEWS/SOCIAL topics"
```

---

## Phase B — GDELT news provider (keyless)

### Task 3: `GdeltProvider`

**Files:**
- Create: `services/collector-news/app/providers/gdelt.py`
- Test: `tests/test_gdelt_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gdelt_provider.py`:

```python
"""GdeltProvider: GDELT DOC 2.0 artlist JSON -> RawItem per article."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "gdelt_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-news" / "app" / "providers" / "gdelt.py",
)
gd = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = gd
_spec.loader.exec_module(gd)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _article(url: str, title: str) -> dict:
    return {
        "url": url, "title": title, "seendate": "20240102T120000Z",
        "domain": "example.com", "language": "English", "sourcecountry": "US",
    }


@respx.mock
async def test_maps_articles_to_rawitems() -> None:
    respx.get(gd.DOC_URL).mock(return_value=httpx.Response(200, json={
        "articles": [_article("https://x/a", "Bitcoin surges"),
                     _article("https://x/b", "Ether update")],
    }))
    provider = gd.GdeltProvider()
    items = await provider.fetch()
    await provider.close()

    assert {i.title for i in items} == {"Bitcoin surges", "Ether update"}
    a = next(i for i in items if i.title == "Bitcoin surges")
    assert a.source == "gdelt"
    assert a.kind == "news"
    assert a.external_id == "https://x/a"
    assert str(a.url) == "https://x/a"
    assert a.published_at is not None  # seendate parsed


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get(gd.DOC_URL).mock(return_value=httpx.Response(429))
    provider = gd.GdeltProvider()
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()


@respx.mock
async def test_empty_or_bad_json_returns_empty() -> None:
    respx.get(gd.DOC_URL).mock(return_value=httpx.Response(200, text="not json"))
    provider = gd.GdeltProvider()
    assert await provider.fetch() == []
    await provider.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_gdelt_provider.py -q -p no:cacheprovider`
Expected: FAIL — module missing

- [ ] **Step 3: Implement**

Create `services/collector-news/app/providers/gdelt.py`:

```python
"""GDELT news provider -> RawItem per article (keyless, global news + tone).

Uses the GDELT DOC 2.0 API artlist mode. Articles are not ticker-tagged, so
``symbols`` is empty and the sentiment worker scores them under ``MARKET`` —
acceptable for a broad macro-news floor.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from cmi_common.sources import RateLimitedError, RawItem, parse_retry_after

logger = logging.getLogger(__name__)
DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


class GdeltProvider:
    name = "gdelt"
    kind = "news"
    rate_limit = (10, 60)  # GDELT asks for gentle polling

    def __init__(self, *, query: str = "cryptocurrency", max_records: int = 75,
                 user_agent: str = "cmi-collector/0.1") -> None:
        self._query = query
        self._max = max_records
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=20.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[RawItem]:
        params = {
            "query": self._query, "mode": "artlist", "format": "json",
            "maxrecords": self._max, "sort": "datedesc",
        }
        try:
            resp = await self._client.get(DOC_URL, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(parse_retry_after(exc.response, default=60)) from exc
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
            items.append(RawItem(
                source="gdelt", kind="news", external_id=url,
                title=art.get("title", ""), text=art.get("title", ""),
                url=url, lang=art.get("language"),
                published_at=_seendate(art.get("seendate")),
            ))
        return items


def _seendate(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_gdelt_provider.py -q -p no:cacheprovider`
Expected: PASS (3 tests)

- [ ] **Step 5: Register in `collector-news` + commit**

In `services/collector-news/app/main.py`, import and append the provider:

```python
from .providers.gdelt import GdeltProvider
```
and add `GdeltProvider(query=os.getenv("GDELT_QUERY", "cryptocurrency"))` to the `providers` list.

Run `python -m py_compile services/collector-news/app/main.py` and `python -m ruff check services/collector-news/app`.

```bash
git add services/collector-news/app/providers/gdelt.py services/collector-news/app/main.py tests/test_gdelt_provider.py
git commit -m "feat(collector-news): add keyless GDELT news provider"
```

---

## Phase C — Mastodon + 4chan social providers (keyless)

### Task 4: `MastodonProvider`

**Files:**
- Create: `services/collector-social/app/providers/mastodon.py`
- Test: `tests/test_mastodon_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mastodon_provider.py`:

```python
"""MastodonProvider: hashtag timeline statuses -> RawItem per crypto post."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "masto_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-social" / "app" / "providers" / "mastodon.py",
)
ms = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = ms
_spec.loader.exec_module(ms)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _status(sid: str, html: str, acct: str, faves: int = 0) -> dict:
    return {
        "id": sid, "content": html, "url": f"https://m/{sid}",
        "created_at": "2024-01-02T12:00:00.000Z", "language": "en",
        "account": {"acct": acct},
        "favourites_count": faves, "reblogs_count": 0, "replies_count": 0,
    }


@respx.mock
async def test_maps_statuses_with_cashtags() -> None:
    url = "https://mastodon.social/api/v1/timelines/tag/crypto"
    respx.get(url).mock(return_value=httpx.Response(200, json=[
        _status("1", "<p>$BTC breaking out</p>", "alice", faves=4),
        _status("2", "<p>no ticker here</p>", "bob"),
    ]))
    provider = ms.MastodonProvider(instance="mastodon.social", hashtag="crypto")
    items = await provider.fetch()
    await provider.close()

    assert len(items) == 1               # only the $BTC status
    it = items[0]
    assert it.source == "mastodon"
    assert it.kind == "social"
    assert it.external_id == "1"
    assert it.symbols == ["BTC"]
    assert "$BTC" in it.text             # HTML stripped
    assert it.engagement == 4.0
    assert it.author == "alice"


@respx.mock
async def test_429_raises_rate_limited() -> None:
    url = "https://mastodon.social/api/v1/timelines/tag/crypto"
    respx.get(url).mock(return_value=httpx.Response(429))
    provider = ms.MastodonProvider(instance="mastodon.social", hashtag="crypto")
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_mastodon_provider.py -q -p no:cacheprovider`
Expected: FAIL — module missing

- [ ] **Step 3: Implement**

Create `services/collector-social/app/providers/mastodon.py`:

```python
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

    def __init__(self, *, instance: str = "mastodon.social", hashtag: str = "crypto",
                 limit: int = 40, user_agent: str = "cmi-collector/0.1") -> None:
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
                raise RateLimitedError(parse_retry_after(exc.response, default=60)) from exc
            raise
        items: list[RawItem] = []
        for st in resp.json():
            text = _strip_html(st.get("content", ""))
            symbols = sorted({m.upper() for m in _CASHTAG.findall(text)})
            if not symbols:
                continue
            engagement = float(
                st.get("favourites_count", 0) + st.get("reblogs_count", 0)
                + st.get("replies_count", 0)
            )
            items.append(RawItem(
                source="mastodon", kind="social", external_id=str(st.get("id")),
                text=text, url=st.get("url"),
                author=st.get("account", {}).get("acct"),
                symbols=symbols, engagement=engagement, lang=st.get("language"),
            ))
        return items


def _strip_html(html: str) -> str:
    return _TAG.sub(" ", html).replace("&amp;", "&").strip()
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_mastodon_provider.py -q -p no:cacheprovider`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/collector-social/app/providers/mastodon.py tests/test_mastodon_provider.py
git commit -m "feat(collector-social): add keyless Mastodon social provider"
```

---

### Task 5: `FourchanProvider`

**Files:**
- Create: `services/collector-social/app/providers/fourchan.py`
- Test: `tests/test_fourchan_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fourchan_provider.py`:

```python
"""FourchanProvider: /biz/ catalog.json threads -> RawItem per crypto OP."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "fourchan_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-social" / "app" / "providers" / "fourchan.py",
)
fc = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = fc
_spec.loader.exec_module(fc)

from cmi_common.sources import RateLimitedError  # noqa: E402


def _thread(no: int, com: str, replies: int = 0) -> dict:
    return {"no": no, "com": com, "replies": replies, "time": 1704196800}


@respx.mock
async def test_maps_threads_with_cashtags() -> None:
    respx.get(fc.CATALOG_URL).mock(return_value=httpx.Response(200, json=[
        {"page": 1, "threads": [
            _thread(101, "buy $BTC now<br>moon", replies=12),
            _thread(102, "generic no ticker thread"),
        ]},
    ]))
    provider = fc.FourchanProvider()
    items = await provider.fetch()
    await provider.close()

    assert len(items) == 1
    it = items[0]
    assert it.source == "fourchan"
    assert it.kind == "social"
    assert it.external_id == "101"
    assert it.symbols == ["BTC"]
    assert "$BTC" in it.text          # <br> stripped
    assert it.engagement == 12.0


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get(fc.CATALOG_URL).mock(return_value=httpx.Response(429))
    provider = fc.FourchanProvider()
    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_fourchan_provider.py -q -p no:cacheprovider`
Expected: FAIL — module missing

- [ ] **Step 3: Implement**

Create `services/collector-social/app/providers/fourchan.py`:

```python
"""4chan /biz/ social provider -> RawItem per crypto thread OP (keyless).

Reads the public catalog.json. Noisy 'degen' signal; OP comment (``com``) is
HTML-ish, so tags are stripped before cashtag extraction.
"""

from __future__ import annotations

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
                raise RateLimitedError(parse_retry_after(exc.response, default=60)) from exc
            raise
        items: list[RawItem] = []
        for page in resp.json():
            for thread in page.get("threads", []):
                text = _strip_html(thread.get("com", ""))
                symbols = sorted({m.upper() for m in _CASHTAG.findall(text)})
                if not symbols:
                    continue
                items.append(RawItem(
                    source="fourchan", kind="social",
                    external_id=str(thread.get("no")),
                    text=text, symbols=symbols,
                    engagement=float(thread.get("replies", 0)),
                ))
        return items


def _strip_html(html: str) -> str:
    return _TAG.sub(" ", html).replace("&#039;", "'").replace("&gt;", ">").strip()
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_fourchan_provider.py -q -p no:cacheprovider`
Expected: PASS (2 tests)

- [ ] **Step 5: Register both social providers in `collector-social` + commit**

In `services/collector-social/app/main.py`, import and append inside `_build_providers`:

```python
from .providers.mastodon import MastodonProvider
from .providers.fourchan import FourchanProvider
```
Append to the providers list:
```python
    providers.append(MastodonProvider(
        instance=os.getenv("MASTODON_INSTANCE", "mastodon.social"),
        hashtag=os.getenv("MASTODON_HASHTAG", "crypto"),
    ))
    providers.append(FourchanProvider())
```

Run `python -m py_compile services/collector-social/app/main.py` and `python -m ruff check services/collector-social/app`.

```bash
git add services/collector-social/app/providers/fourchan.py services/collector-social/app/main.py tests/test_fourchan_provider.py
git commit -m "feat(collector-social): add keyless 4chan /biz/ social provider + register mastodon"
```

---

## Phase D — Ops & docs

### Task 6: Env, docs, full verification

**Files:**
- Modify: `.env.example`, `docker-compose.yml`, `CLAUDE.md`

- [ ] **Step 1: Add the new knobs to `.env.example`**

Under the cascades/ingestion section, add:

```
# Keyless ingestion sources (Plan 2)
GDELT_QUERY=cryptocurrency
MASTODON_INSTANCE=mastodon.social
MASTODON_HASHTAG=crypto
# Add Google News to the news floor by appending its RSS URL to RSS_FEEDS, e.g.:
# RSS_FEEDS=https://news.google.com/rss/search?q=cryptocurrency
```

- [ ] **Step 2: Pass the env through compose**

In `docker-compose.yml`, add to `collector-news.environment`: `GDELT_QUERY: ${GDELT_QUERY:-cryptocurrency}`. Add to `collector-social.environment`: `MASTODON_INSTANCE: ${MASTODON_INSTANCE:-mastodon.social}` and `MASTODON_HASHTAG: ${MASTODON_HASHTAG:-crypto}`. Validate: `python -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('yaml OK')"`.

- [ ] **Step 3: Remove the resolved I1 gap note from `CLAUDE.md`**

Delete the `⚠️ **Plan-2 follow-up (known gap):**` paragraph added in Plan 1 (haiku is now rewired). In the pipeline paragraph, update the source lists to mention the new platforms: `collector-social` (Bluesky, Reddit, Mastodon, 4chan) and `collector-news` (CryptoCompare, RSS, GDELT).

- [ ] **Step 4: Full suite + lint**

Run: `python -m pytest -q -p no:cacheprovider` — expect all green (haiku extract + 3 provider test files added).
Run: `python -m ruff check libs services` and `ruff format --check` on the feature files (`services/collector-news/app/providers/gdelt.py`, `services/collector-social/app/providers/{mastodon,fourchan}.py`, `services/ai-worker-haiku/app/{worker,main}.py`) — fix any ≤88-col/format issues. Repo-wide pre-existing lint failures are out of scope (report, don't fix).

- [ ] **Step 5: Commit**

```bash
git add .env.example docker-compose.yml CLAUDE.md
git commit -m "feat: wire GDELT/Mastodon/4chan env + docs; close haiku social/news gap"
```

---

## Self-Review

**Spec coverage:** Spec platform inventory keyless-news → GDELT (Task 3) + Google News (config note, Task 6). Keyless-social → Mastodon (Task 4) + 4chan (Task 5). Coinpaprika deferred to Plan 3 (its free news endpoint needs live verification). Haiku rewire (I1 from Plan-1 final review) → Tasks 1–2, using the approved "derive from SentimentEvent" approach.

**Placeholder scan:** every provider has full fetch + mapping + tests; no TBD. GDELT/Mastodon/4chan response shapes are encoded from their documented public formats (GDELT DOC 2.0 artlist `{articles:[{url,title,seendate,domain,language}]}`; Mastodon status `{id,content,account.acct,favourites_count,...}`; 4chan catalog `[{threads:[{no,com,replies,time}]}]`).

**Type consistency:** all three providers expose `name`/`kind`/`rate_limit`/`fetch()->list[RawItem]`/`close()` matching the `Provider` protocol and the `AdaptivePollLoop` contract; each raises `RateLimitedError` on 429 via `parse_retry_after`. `RawItem` kwargs match `cmi_common.sources.RawItem`. `_extract` returns the `(symbol, fields, topic)` tuple shape the existing `handle` expects.

**Deferred to Plan 3 (key-gated, need live API-shape verification):** Farcaster/Neynar, StockTwits, Lens, YouTube, Telegram (news: Messari, NewsData, CoinGecko-news). Each will follow this same Provider pattern with a no-op-without-key guard.
