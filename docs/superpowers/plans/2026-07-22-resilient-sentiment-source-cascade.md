# Resilient Sentiment Source Cascade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed `sentiment-service` continuously all month within free API tiers by polling an ordered cascade of providers per channel, automatically failing over to an unlimited floor source when a metered source hits its quota, and returning to the primary when the quota window resets.

**Architecture:** A new `cmi_common/sources/` module provides a `Provider` protocol, a Redis-backed `CircuitBreaker` (TTL-based auto half-open), and a `SourceCascade` that polls providers in priority order, publishing events from the first healthy one and tripping breakers on `RateLimitedError`/errors. Two new orchestrator services — `collector-social` (Bluesky primary → Reddit fallback) and `collector-news` (CryptoCompare-news primary → RSS floor) — wire providers into a cascade behind the existing `run_periodic` poller. The paid `collector-twitter` service is retired. Downstream is unchanged: cascades still emit `SocialEvent`/`NewsEvent` on `Topic.SOCIAL`/`Topic.NEWS`.

**Tech Stack:** Python 3.12, FastAPI, httpx, respx (tests), Redis (`Cache`), aiokafka (`EventProducer`), Pydantic v2 events, Docker Compose.

**Why this ordering (design note):**
- **Bluesky is the social primary**, not a mere floor: it is unlimited (~3000 req/5min per IP), keyless, and — unlike Reddit — carries no "non-commercial only" restriction, so it is the safest default for a trading bot. Reddit sits behind it as an enrichment fallback (richer titles, but 100 req/min and legally fragile for commercial use), honoring the decision to keep Reddit as a fallback.
- **CryptoCompare `/data/social` is deliberately NOT in the sentiment cascade**: it returns aggregate metrics (follower/subscriber/post counts) with no post text, and `sentiment-service` scores `text_sample`. CryptoCompare contributes text via its **news** endpoint instead (news primary). A future "social-volume" signal could use CC-social, out of scope here.
- Both channels have a **metered primary + unlimited floor**, so the free tier is drained first and the pipeline can never fully dry up.

---

## File Structure

**New shared code (`libs/cmi_common/cmi_common/`):**
- `sources/__init__.py` — exports `Provider`, `RateLimitedError`, `CircuitBreaker`, `SourceCascade`
- `sources/cascade.py` — the cascade core (all four symbols above)
- `events/base.py` — MODIFY: add `Source.BLUESKY`, `Source.RSS`

**New service `services/collector-social/`:**
- `app/__init__.py`
- `app/providers/__init__.py`
- `app/providers/bluesky.py` — `BlueskyProvider`
- `app/providers/reddit.py` — `RedditProvider`
- `app/main.py` — wires cascade `[Bluesky, Reddit]` on `Topic.SOCIAL`
- `pyproject.toml`

**New service `services/collector-news/`:**
- `app/__init__.py`
- `app/providers/__init__.py`
- `app/providers/cryptocompare.py` — `CryptoCompareNewsProvider`
- `app/providers/rss.py` — `RSSProvider`
- `app/main.py` — wires cascade `[CryptoCompareNews, RSS]` on `Topic.NEWS`
- `pyproject.toml`

**Tests (`tests/`):** one file per new unit — `test_source_cascade.py`, `test_circuit_breaker.py`, `test_bluesky_provider.py`, `test_reddit_provider.py`, `test_cryptocompare_news_provider.py`, `test_rss_provider.py`.

**Infra:** `docker-compose.yml` (add two services, remove `collector-twitter`), retire `services/collector-twitter` + `services/collector-reddit` + `services/collector-cryptocompare` (their logic moves into providers).

---

## Phase 1 — Cascade core (`cmi_common`)

### Task 1: Add new `Source` enum members

**Files:**
- Modify: `libs/cmi_common/cmi_common/events/base.py:43-56`
- Test: `tests/test_events.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_events.py`:

```python
def test_new_social_sources_exist() -> None:
    from cmi_common.events.base import Source

    assert Source.BLUESKY.value == "bluesky"
    assert Source.RSS.value == "rss"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_events.py::test_new_social_sources_exist -v`
Expected: FAIL with `AttributeError: BLUESKY`

- [ ] **Step 3: Add the enum members**

In `libs/cmi_common/cmi_common/events/base.py`, inside `class Source`, after the `TWITTER = "twitter"` line add:

```python
    BLUESKY = "bluesky"
    RSS = "rss"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_events.py::test_new_social_sources_exist -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/events/base.py tests/test_events.py
git commit -m "feat(cmi_common): add bluesky + rss Source enum members"
```

---

### Task 2: `CircuitBreaker` (Redis-backed, TTL auto half-open)

**Files:**
- Create: `libs/cmi_common/cmi_common/sources/__init__.py`
- Create: `libs/cmi_common/cmi_common/sources/cascade.py`
- Test: `tests/test_circuit_breaker.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_circuit_breaker.py`:

```python
"""CircuitBreaker: trip opens a provider until its cooldown TTL expires."""

from __future__ import annotations

from cmi_common.sources import CircuitBreaker


class FakeRedis:
    """Minimal async redis stub tracking set-with-ttl and existence."""

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def exists(self, key: str) -> int:
        return 1 if key in self.keys else 0

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.keys[key] = value
        if ex is not None:
            self.ttls[key] = ex


class FakeCache:
    def __init__(self) -> None:
        self._redis = FakeRedis()

    @property
    def client(self) -> FakeRedis:
        return self._redis


async def test_closed_by_default() -> None:
    breaker = CircuitBreaker(FakeCache())
    assert await breaker.is_open("bluesky") is False


async def test_trip_opens_with_retry_after_ttl() -> None:
    cache = FakeCache()
    breaker = CircuitBreaker(cache, default_cooldown=300.0)

    await breaker.trip("bluesky", 42.0)

    assert await breaker.is_open("bluesky") is True
    assert cache.client.ttls["cb:bluesky"] == 42


async def test_trip_without_retry_after_uses_default() -> None:
    cache = FakeCache()
    breaker = CircuitBreaker(cache, default_cooldown=300.0)

    await breaker.trip("reddit", None)

    assert cache.client.ttls["cb:reddit"] == 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_circuit_breaker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmi_common.sources'`

- [ ] **Step 3: Create the module with `RateLimitedError`, `Provider`, `CircuitBreaker`**

Create `libs/cmi_common/cmi_common/sources/cascade.py`:

```python
"""Provider cascade primitives: failover across free-tier data sources.

A ``SourceCascade`` polls an ordered list of ``Provider`` objects (primary
first, unlimited floor last) and publishes events from the first healthy one.
When a provider signals ``RateLimitedError`` (proactive quota guard) or raises, its
``CircuitBreaker`` is tripped and the cascade falls through to the next
provider, so the pipeline never goes dry. Breakers auto half-open when their
Redis TTL expires, letting the primary resume once its quota window resets.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..cache import Cache
from ..events.base import BaseEvent
from ..kafka import EventProducer, Topic
from ..observability import EVENTS_PRODUCED

logger = logging.getLogger(__name__)


class RateLimitedError(Exception):
    """Raised by a provider that has exhausted its quota for now.

    ``retry_after`` (seconds) hints how long to keep the breaker open; ``None``
    falls back to the cascade/breaker default cooldown.
    """

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


@runtime_checkable
class Provider(Protocol):
    """A single data source. ``name`` keys its breaker; ``fetch`` returns events."""

    name: str

    async def fetch(self) -> list[BaseEvent]:
        ...

    async def close(self) -> None:
        ...


class CircuitBreaker:
    """Redis-backed breaker shared across replicas.

    A tripped provider stays open until its cooldown key TTL expires, after
    which the next poll probes it again (half-open); success keeps it closed,
    another failure re-trips it.
    """

    def __init__(self, cache: Cache, *, default_cooldown: float = 300.0) -> None:
        self._cache = cache
        self._default = default_cooldown

    async def is_open(self, name: str) -> bool:
        return bool(await self._cache.client.exists(f"cb:{name}"))

    async def trip(self, name: str, cooldown: float | None = None) -> None:
        ttl = max(1, int(cooldown if cooldown is not None else self._default))
        await self._cache.client.set(f"cb:{name}", "1", ex=ttl)
```

Create `libs/cmi_common/cmi_common/sources/__init__.py`:

```python
"""Free-tier source cascade with per-provider circuit breaking."""

from __future__ import annotations

from .cascade import CircuitBreaker, Provider, RateLimitedError, SourceCascade

__all__ = ["CircuitBreaker", "Provider", "RateLimitedError", "SourceCascade"]
```

> Note: `SourceCascade` is added in Task 3; importing it here now will fail until then. Add the `SourceCascade` import line but expect Task 2's test to pass because the test only imports `CircuitBreaker`. To keep Task 2 green in isolation, temporarily set `__all__ = ["CircuitBreaker", "Provider", "RateLimitedError"]` and import only those; Task 3 restores the full list.

Use this Task-2 version of `__init__.py`:

```python
"""Free-tier source cascade with per-provider circuit breaking."""

from __future__ import annotations

from .cascade import CircuitBreaker, Provider, RateLimitedError

__all__ = ["CircuitBreaker", "Provider", "RateLimitedError"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_circuit_breaker.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/sources tests/test_circuit_breaker.py
git commit -m "feat(cmi_common): add RateLimitedError + CircuitBreaker cascade primitives"
```

---

### Task 3: `SourceCascade` failover logic

**Files:**
- Modify: `libs/cmi_common/cmi_common/sources/cascade.py` (append `SourceCascade`)
- Modify: `libs/cmi_common/cmi_common/sources/__init__.py` (restore full `__all__`)
- Test: `tests/test_source_cascade.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_source_cascade.py`:

```python
"""SourceCascade: primary drains first; failover on RateLimitedError/error."""

from __future__ import annotations

from cmi_common.events.base import Source
from cmi_common.events.social import SocialEvent
from cmi_common.kafka import Topic
from cmi_common.sources import CircuitBreaker, RateLimitedError, SourceCascade


class FakeRedis:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    async def exists(self, key: str) -> int:
        return 1 if key in self.keys else 0

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.keys[key] = value


class FakeCache:
    def __init__(self) -> None:
        self._redis = FakeRedis()

    @property
    def client(self) -> FakeRedis:
        return self._redis


class FakeProducer:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, _topic, event) -> None:
        self.published.append(event)


def _event(symbol: str) -> SocialEvent:
    return SocialEvent(source=Source.BLUESKY, symbol=symbol, mentions=1)


class StubProvider:
    def __init__(self, name: str, *, events=None, raises: Exception | None = None) -> None:
        self.name = name
        self._events = events or []
        self._raises = raises
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._events

    async def close(self) -> None:
        pass


async def test_primary_serves_and_fallback_untouched() -> None:
    primary = StubProvider("bluesky", events=[_event("BTC")])
    fallback = StubProvider("reddit", events=[_event("ETH")])
    producer = FakeProducer()
    cascade = SourceCascade(
        [primary, fallback], CircuitBreaker(FakeCache()), producer,
        Topic.SOCIAL, service="collector-social",
    )

    published = await cascade.poll_once()

    assert published == 1
    assert [e.symbol for e in producer.published] == ["BTC"]
    assert fallback.calls == 0  # primary healthy -> fallback never polled


async def test_rate_limited_primary_trips_and_falls_through() -> None:
    primary = StubProvider("bluesky", raises=RateLimitedError(30.0))
    fallback = StubProvider("reddit", events=[_event("ETH")])
    producer = FakeProducer()
    breaker = CircuitBreaker(FakeCache())
    cascade = SourceCascade(
        [primary, fallback], breaker, producer,
        Topic.SOCIAL, service="collector-social",
    )

    published = await cascade.poll_once()

    assert published == 1
    assert [e.symbol for e in producer.published] == ["ETH"]
    assert await breaker.is_open("bluesky") is True


async def test_open_breaker_skips_provider_without_calling() -> None:
    primary = StubProvider("bluesky", events=[_event("BTC")])
    fallback = StubProvider("reddit", events=[_event("ETH")])
    producer = FakeProducer()
    breaker = CircuitBreaker(FakeCache())
    await breaker.trip("bluesky", 300.0)
    cascade = SourceCascade(
        [primary, fallback], breaker, producer,
        Topic.SOCIAL, service="collector-social",
    )

    published = await cascade.poll_once()

    assert primary.calls == 0  # breaker open -> skipped entirely
    assert [e.symbol for e in producer.published] == ["ETH"]
    assert published == 1


async def test_all_exhausted_returns_zero() -> None:
    primary = StubProvider("bluesky", raises=RateLimitedError())
    fallback = StubProvider("reddit", raises=RuntimeError("boom"))
    producer = FakeProducer()
    cascade = SourceCascade(
        [primary, fallback], CircuitBreaker(FakeCache()), producer,
        Topic.SOCIAL, service="collector-social",
    )

    published = await cascade.poll_once()

    assert published == 0
    assert producer.published == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_source_cascade.py -v`
Expected: FAIL with `ImportError: cannot import name 'SourceCascade'`

- [ ] **Step 3: Append `SourceCascade` to `cascade.py`**

Append to `libs/cmi_common/cmi_common/sources/cascade.py`:

```python
class SourceCascade:
    """Polls providers in priority order, serving the first healthy one.

    Primary first, unlimited floor last. Each tick skips providers whose
    breaker is open, tries the rest in order, and publishes the events from
    the first provider that returns without raising. ``RateLimitedError`` trips the
    breaker for its ``retry_after``; any other exception trips it for
    ``error_cooldown``. Both fall through to the next provider.
    """

    def __init__(
        self,
        providers: Sequence[Provider],
        breaker: CircuitBreaker,
        producer: EventProducer,
        topic: Topic,
        *,
        service: str,
        error_cooldown: float = 120.0,
    ) -> None:
        self._providers = list(providers)
        self._breaker = breaker
        self._producer = producer
        self._topic = topic
        self._service = service
        self._error_cooldown = error_cooldown

    async def close(self) -> None:
        for provider in self._providers:
            await provider.close()

    async def poll_once(self) -> int:
        for provider in self._providers:
            if await self._breaker.is_open(provider.name):
                logger.debug("provider %s breaker open; skipping", provider.name)
                continue
            try:
                events = await provider.fetch()
            except RateLimitedError as exc:
                await self._breaker.trip(provider.name, exc.retry_after)
                logger.info("provider %s rate-limited; failing over", provider.name)
                continue
            except Exception:  # noqa: BLE001 - any provider failure fails over
                await self._breaker.trip(provider.name, self._error_cooldown)
                logger.warning(
                    "provider %s errored; failing over", provider.name, exc_info=True
                )
                continue
            for event in events:
                await self._producer.publish(self._topic, event)
                EVENTS_PRODUCED.labels(
                    self._service, self._topic.value, event.event_type
                ).inc()
            logger.info("cascade served %d events from %s", len(events), provider.name)
            return len(events)
        logger.warning("all providers exhausted this tick; no events served")
        return 0
```

- [ ] **Step 4: Restore full exports**

Replace `libs/cmi_common/cmi_common/sources/__init__.py` contents with:

```python
"""Free-tier source cascade with per-provider circuit breaking."""

from __future__ import annotations

from .cascade import CircuitBreaker, Provider, RateLimitedError, SourceCascade

__all__ = ["CircuitBreaker", "Provider", "RateLimitedError", "SourceCascade"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_source_cascade.py tests/test_circuit_breaker.py -v`
Expected: PASS (4 + 3 tests)

- [ ] **Step 6: Commit**

```bash
git add libs/cmi_common/cmi_common/sources tests/test_source_cascade.py
git commit -m "feat(cmi_common): add SourceCascade failover over free-tier providers"
```

---

## Phase 2 — Social orchestrator (`collector-social`)

### Task 4: `BlueskyProvider` (social primary, unlimited)

**Files:**
- Create: `services/collector-social/app/__init__.py` (empty)
- Create: `services/collector-social/app/providers/__init__.py` (empty)
- Create: `services/collector-social/app/providers/bluesky.py`
- Test: `tests/test_bluesky_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bluesky_provider.py`:

```python
"""BlueskyProvider: public searchPosts -> aggregated SocialEvent per cashtag."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "bsky_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-social" / "app" / "providers" / "bluesky.py",
)
bsky = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = bsky
_spec.loader.exec_module(bsky)

from cmi_common.sources import RateLimitedError  # noqa: E402


class FakeCache:
    def __init__(self) -> None:
        self.stored: dict[str, object] = {}

    async def get_json(self, key: str):
        return self.stored.get(key)

    async def set_json(self, key: str, value, ttl_seconds: int | None = None) -> None:
        self.stored[key] = value


def _post(text: str, did: str, likes: int = 0) -> dict:
    return {
        "record": {"text": text},
        "author": {"did": did},
        "likeCount": likes,
        "repostCount": 0,
        "replyCount": 0,
    }


@respx.mock
async def test_aggregates_cashtags() -> None:
    respx.get(bsky.SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "posts": [
                    _post("$BTC breakout bullish", "did:a", likes=10),
                    _post("still holding $BTC", "did:b", likes=5),
                    _post("$ETH strong", "did:c", likes=2),
                ]
            },
        )
    )
    provider = bsky.BlueskyProvider(FakeCache())

    events = await provider.fetch()
    await provider.close()

    by_symbol = {e.symbol: e for e in events}
    assert set(by_symbol) == {"BTC", "ETH"}
    btc = by_symbol["BTC"]
    assert btc.platform == "bluesky"
    assert btc.source == "bluesky"  # use_enum_values -> plain string
    assert btc.mentions == 2
    assert btc.unique_authors == 2
    assert btc.engagement_score == 15.0
    assert "$BTC" in btc.text_sample


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get(bsky.SEARCH_URL).mock(return_value=httpx.Response(429))
    provider = bsky.BlueskyProvider(FakeCache())

    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bluesky_provider.py -v`
Expected: FAIL — file `bluesky.py` does not exist (spec load error)

- [ ] **Step 3: Implement the provider**

Create empty `services/collector-social/app/__init__.py` and `services/collector-social/app/providers/__init__.py`.

Create `services/collector-social/app/providers/bluesky.py`:

```python
"""Bluesky (AT Protocol) social provider -> SocialEvent per cashtag.

Uses the keyless, unlimited public search endpoint. Aggregates ``$CASHTAG``
mentions / engagement over a window, mirroring the legacy collectors so the
sentiment layer treats every platform uniformly.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.events.base import Source
from cmi_common.events.social import SocialEvent
from cmi_common.observability import UPSTREAM_REQUESTS
from cmi_common.sources import RateLimitedError

SERVICE = "collector-social"
SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")
PREV_KEY = "bluesky:mentions:{symbol}"


class BlueskyProvider:
    name = "bluesky"

    def __init__(
        self,
        cache: Cache,
        *,
        query: str = "crypto",
        limit: int = 100,
        window_minutes: int = 60,
        user_agent: str = "cmi-collector/0.1",
    ) -> None:
        self._cache = cache
        self._query = query
        self._limit = limit
        self._window = window_minutes
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[SocialEvent]:
        try:
            resp = await self._client.get(
                SEARCH_URL, params={"q": self._query, "limit": self._limit}
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                UPSTREAM_REQUESTS.labels(SERVICE, "bluesky", "ratelimit").inc()
                raise RateLimitedError() from exc
            raise
        UPSTREAM_REQUESTS.labels(SERVICE, "bluesky", "ok").inc()
        posts = resp.json().get("posts", [])

        agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"mentions": 0, "authors": set(), "engagement": 0.0, "text": []}
        )
        for post in posts:
            text = post.get("record", {}).get("text", "")
            engagement = (
                post.get("likeCount", 0)
                + post.get("repostCount", 0)
                + post.get("replyCount", 0)
            )
            author = post.get("author", {}).get("did")
            for symbol in {m.upper() for m in _CASHTAG.findall(text)}:
                a = agg[symbol]
                a["mentions"] += 1
                a["authors"].add(author)
                a["engagement"] += engagement
                a["text"].append(text)

        return [await self._to_event(symbol, a) for symbol, a in agg.items()]

    async def _to_event(self, symbol: str, a: dict[str, Any]) -> SocialEvent:
        key = PREV_KEY.format(symbol=symbol)
        prev = await self._cache.get_json(key) or 0
        mentions = a["mentions"]
        growth = ((mentions - prev) / prev) if prev else 0.0
        await self._cache.set_json(key, mentions, ttl_seconds=self._window * 60)
        return SocialEvent(
            source=Source.BLUESKY,
            symbol=symbol,
            platform="bluesky",
            window_minutes=self._window,
            mentions=mentions,
            mentions_growth=round(growth, 3),
            unique_authors=len(a["authors"]),
            engagement_score=float(a["engagement"]),
            top_posts=[t[:120] for t in a["text"][:5]],
            text_sample=" ".join(a["text"])[:2000],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bluesky_provider.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/collector-social/app tests/test_bluesky_provider.py
git commit -m "feat(collector-social): add BlueskyProvider (unlimited social primary)"
```

---

### Task 5: `RedditProvider` (social fallback, metered)

**Files:**
- Create: `services/collector-social/app/providers/reddit.py`
- Test: `tests/test_reddit_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reddit_provider.py`:

```python
"""RedditProvider: /new -> SocialEvent; quota exhaustion raises RateLimitedError."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "reddit_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-social" / "app" / "providers" / "reddit.py",
)
rd = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = rd
_spec.loader.exec_module(rd)

from cmi_common.sources import RateLimitedError  # noqa: E402


class FakeCache:
    def __init__(self, allow: bool = True) -> None:
        self.stored: dict[str, object] = {}
        self._allow = allow

    async def allow(self, *_a) -> bool:
        return self._allow

    async def get_json(self, key: str):
        return self.stored.get(key)

    async def set_json(self, key: str, value, ttl_seconds: int | None = None) -> None:
        self.stored[key] = value


def _post(title: str, author: str, score: int = 0, comments: int = 0) -> dict:
    return {
        "data": {
            "title": title,
            "selftext": "",
            "author": author,
            "score": score,
            "num_comments": comments,
        }
    }


@respx.mock
async def test_aggregates_cashtags_from_new() -> None:
    respx.get("https://www.reddit.com/r/CryptoCurrency/new.json").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"children": [
                _post("$BTC to the moon", "u1", score=10, comments=5),
                _post("holding $BTC", "u2", score=3, comments=1),
            ]}},
        )
    )
    provider = rd.RedditProvider(FakeCache(), subreddits=["CryptoCurrency"])

    events = await provider.fetch()
    await provider.close()

    assert len(events) == 1
    btc = events[0]
    assert btc.symbol == "BTC"
    assert btc.platform == "reddit"
    assert btc.source == "reddit"
    assert btc.mentions == 2
    assert btc.engagement_score == 19.0  # 10+5 + 3+1


async def test_quota_exhausted_raises_rate_limited() -> None:
    provider = rd.RedditProvider(FakeCache(allow=False), subreddits=["CryptoCurrency"])

    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reddit_provider.py -v`
Expected: FAIL — `reddit.py` does not exist

- [ ] **Step 3: Implement the provider**

Create `services/collector-social/app/providers/reddit.py`:

```python
"""Reddit social provider -> SocialEvent per cashtag.

Ports the legacy collector-reddit aggregation into a cascade Provider: it
polls /new for each subreddit, aggregates ``$CASHTAG`` mentions/engagement, and
raises ``RateLimitedError`` when the shared per-minute quota is spent (proactive
guard) or Reddit returns 429 (reactive). Non-commercial free tier — kept as a
fallback behind Bluesky.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.events.base import Source
from cmi_common.events.social import SocialEvent
from cmi_common.observability import UPSTREAM_REQUESTS
from cmi_common.sources import RateLimitedError

SERVICE = "collector-social"
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")
PREV_KEY = "reddit:mentions:{symbol}"


class RedditProvider:
    name = "reddit"

    def __init__(
        self,
        cache: Cache,
        *,
        subreddits: list[str] | None = None,
        window_minutes: int = 60,
        client_id: str | None = None,
        client_secret: str | None = None,
        user_agent: str = "cmi-collector/0.1",
        rate_limit: int = 60,
    ) -> None:
        self._cache = cache
        self._subreddits = subreddits or ["CryptoCurrency", "CryptoMoonShots", "solana"]
        self._window = window_minutes
        self._client_id = client_id
        self._client_secret = client_secret
        self._rate_limit = rate_limit
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=15.0
        )
        self._token: str | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def _auth(self) -> None:
        if not (self._client_id and self._client_secret):
            return
        cached = await self._cache.get_json("reddit:token")
        if cached:
            self._token = cached
            return
        resp = await self._client.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        await self._cache.set_json("reddit:token", self._token, ttl_seconds=3000)

    async def _fetch_new(self, subreddit: str) -> list[dict[str, Any]]:
        if not await self._cache.allow("reddit", self._rate_limit, 60):
            raise RateLimitedError(60.0)
        if self._token:
            url = f"https://oauth.reddit.com/r/{subreddit}/new"
            headers = {"Authorization": f"bearer {self._token}"}
        else:
            url = f"https://www.reddit.com/r/{subreddit}/new.json"
            headers = {}
        try:
            resp = await self._client.get(url, params={"limit": 100}, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                UPSTREAM_REQUESTS.labels(SERVICE, "reddit", "ratelimit").inc()
                raise RateLimitedError() from exc
            raise
        UPSTREAM_REQUESTS.labels(SERVICE, "reddit", "ok").inc()
        return [c["data"] for c in resp.json().get("data", {}).get("children", [])]

    async def fetch(self) -> list[SocialEvent]:
        await self._auth()
        agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "mentions": 0,
                "authors": set(),
                "engagement": 0.0,
                "titles": [],
                "text": [],
                "subreddit": None,
            }
        )
        for sub in self._subreddits:
            posts = await self._fetch_new(sub)  # RateLimitedError propagates to cascade
            for post in posts:
                title = post.get("title", "")
                body = post.get("selftext", "")
                for symbol in {m.upper() for m in _CASHTAG.findall(f"{title} {body}")}:
                    a = agg[symbol]
                    a["mentions"] += 1
                    a["authors"].add(post.get("author"))
                    a["engagement"] += post.get("score", 0) + post.get("num_comments", 0)
                    a["subreddit"] = sub
                    if len(a["titles"]) < 5:
                        a["titles"].append(title)
                    a["text"].append(title)

        return [await self._to_event(symbol, a) for symbol, a in agg.items()]

    async def _to_event(self, symbol: str, a: dict[str, Any]) -> SocialEvent:
        key = PREV_KEY.format(symbol=symbol)
        prev = await self._cache.get_json(key) or 0
        mentions = a["mentions"]
        growth = ((mentions - prev) / prev) if prev else 0.0
        await self._cache.set_json(key, mentions, ttl_seconds=self._window * 60)
        return SocialEvent(
            source=Source.REDDIT,
            symbol=symbol,
            platform="reddit",
            subreddit=a["subreddit"],
            window_minutes=self._window,
            mentions=mentions,
            mentions_growth=round(growth, 3),
            unique_authors=len(a["authors"]),
            engagement_score=float(a["engagement"]),
            top_posts=a["titles"],
            text_sample=" ".join(a["text"])[:2000],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reddit_provider.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/collector-social/app/providers/reddit.py tests/test_reddit_provider.py
git commit -m "feat(collector-social): add RedditProvider (metered social fallback)"
```

---

### Task 6: `collector-social` service wiring

**Files:**
- Create: `services/collector-social/app/main.py`
- Create: `services/collector-social/pyproject.toml`

- [ ] **Step 1: Write the pyproject**

Create `services/collector-social/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "collector-social"
version = "0.1.0"
description = "Resilient social collector — Bluesky primary, Reddit fallback"
requires-python = ">=3.12"
dependencies = ["cmi-common", "httpx>=0.27"]

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

- [ ] **Step 2: Write the entrypoint**

Create `services/collector-social/app/main.py`:

```python
"""collector-social entrypoint: Bluesky -> Reddit cascade on market.social."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventProducer, Topic
from cmi_common.runner import run_periodic
from cmi_common.sources import CircuitBreaker, SourceCascade

from .providers.bluesky import BlueskyProvider
from .providers.reddit import RedditProvider

POLL_INTERVAL = float(os.getenv("SOCIAL_POLL_INTERVAL", "300"))
BLUESKY_QUERY = os.getenv("BLUESKY_QUERY", "crypto")
SUBREDDITS = os.getenv(
    "REDDIT_SUBREDDITS", "CryptoCurrency,CryptoMoonShots,solana"
).split(",")
BREAKER_COOLDOWN = float(os.getenv("SOURCE_BREAKER_COOLDOWN", "300"))


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    providers = [
        BlueskyProvider(cache, query=BLUESKY_QUERY),
        RedditProvider(
            cache,
            subreddits=SUBREDDITS,
            client_id=os.getenv("REDDIT_CLIENT_ID") or None,
            client_secret=os.getenv("REDDIT_CLIENT_SECRET") or None,
        ),
    ]
    cascade = SourceCascade(
        providers,
        CircuitBreaker(cache, default_cooldown=BREAKER_COOLDOWN),
        producer,
        Topic.SOCIAL,
        service="collector-social",
    )
    app.state.cache = cache
    app.state.producer = producer
    app.state.cascade = cascade
    app.state.poller = asyncio.create_task(
        run_periodic(cascade.poll_once, POLL_INTERVAL, name="social-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.cascade.close()
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("collector-social", on_startup=_startup, on_shutdown=_shutdown)
```

- [ ] **Step 3: Verify imports resolve (smoke)**

Run: `python -c "import importlib.util, pathlib; s=importlib.util.spec_from_file_location('m', pathlib.Path('services/collector-social/app/main.py'))" && pytest tests/test_bluesky_provider.py tests/test_reddit_provider.py -v`
Expected: PASS (existing provider tests still green)

- [ ] **Step 4: Commit**

```bash
git add services/collector-social/app/main.py services/collector-social/pyproject.toml
git commit -m "feat(collector-social): wire Bluesky->Reddit cascade poller"
```

---

## Phase 3 — News orchestrator (`collector-news`)

### Task 7: `CryptoCompareNewsProvider` (news primary, metered)

**Files:**
- Create: `services/collector-news/app/__init__.py` (empty)
- Create: `services/collector-news/app/providers/__init__.py` (empty)
- Create: `services/collector-news/app/providers/cryptocompare.py`
- Test: `tests/test_cryptocompare_news_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cryptocompare_news_provider.py`:

```python
"""CryptoCompareNewsProvider: /data/v2/news -> NewsEvent; quota -> RateLimitedError."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

_spec = importlib.util.spec_from_file_location(
    "cc_news_provider",
    Path(__file__).resolve().parents[1]
    / "services" / "collector-news" / "app" / "providers" / "cryptocompare.py",
)
cc = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = cc
_spec.loader.exec_module(cc)

from cmi_common.sources import RateLimitedError  # noqa: E402


class FakeCache:
    def __init__(self, allow: bool = True) -> None:
        self.stored: dict[str, object] = {}
        self._allow = allow

    async def allow(self, *_a) -> bool:
        return self._allow

    async def get_json(self, key: str):
        return self.stored.get(key)

    async def set_json(self, key: str, value, ttl_seconds: int | None = None) -> None:
        self.stored[key] = value


def _article(aid: int, title: str) -> dict:
    return {
        "id": aid,
        "title": title,
        "body": "body text",
        "url": "https://example.com/a",
        "published_on": 1700000000,
        "source_info": {"name": "CoinDesk"},
        "categories": "BTC|Trading",
    }


@respx.mock
async def test_publishes_new_articles() -> None:
    respx.get("https://min-api.cryptocompare.com/data/v2/news/").mock(
        return_value=httpx.Response(200, json={"Data": [
            _article(2, "second"), _article(1, "first"),
        ]})
    )
    provider = cc.CryptoCompareNewsProvider(
        "https://min-api.cryptocompare.com", None, FakeCache()
    )

    events = await provider.fetch()
    await provider.close()

    assert [e.title for e in events] == ["second", "first"]
    assert events[0].symbols == ["BTC"]
    assert events[0].source == "cryptocompare"


async def test_quota_exhausted_raises_rate_limited() -> None:
    provider = cc.CryptoCompareNewsProvider(
        "https://min-api.cryptocompare.com", None, FakeCache(allow=False)
    )

    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cryptocompare_news_provider.py -v`
Expected: FAIL — file does not exist

- [ ] **Step 3: Implement the provider**

Create empty `services/collector-news/app/__init__.py` and `services/collector-news/app/providers/__init__.py`.

Create `services/collector-news/app/providers/cryptocompare.py`:

```python
"""CryptoCompare news provider -> NewsEvent.

Ports the legacy collector into a cascade Provider: incremental polling via a
Redis cursor, and a proactive per-minute quota guard (``cache.allow``) that
raises ``RateLimitedError`` so the cascade fails over to RSS once the free monthly
budget's per-minute allotment is spent.
"""

from __future__ import annotations

from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.events.base import Source
from cmi_common.events.news import NewsEvent
from cmi_common.observability import UPSTREAM_REQUESTS
from cmi_common.sources import RateLimitedError

SERVICE = "collector-news"
CURSOR_KEY = "cryptocompare:last_id"


class CryptoCompareNewsProvider:
    name = "cryptocompare-news"

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        cache: Cache,
        *,
        rate_limit: int = 2,
    ) -> None:
        headers = {"authorization": f"Apikey {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=15.0
        )
        self._cache = cache
        self._rate_limit = rate_limit

    async def close(self) -> None:
        await self._client.aclose()

    async def _fetch_raw(self) -> list[dict[str, Any]]:
        # ~2 calls/min => ~86k/month, under the 100k free-tier cap.
        if not await self._cache.allow("cryptocompare-news", self._rate_limit, 60):
            raise RateLimitedError(60.0)
        try:
            resp = await self._client.get(
                "/data/v2/news/", params={"lang": "EN", "sortOrder": "latest"}
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                UPSTREAM_REQUESTS.labels(SERVICE, "cryptocompare", "ratelimit").inc()
                raise RateLimitedError() from exc
            raise
        UPSTREAM_REQUESTS.labels(SERVICE, "cryptocompare", "ok").inc()
        return resp.json().get("Data", [])

    async def fetch(self) -> list[NewsEvent]:
        last_id = await self._cache.get_json(CURSOR_KEY)
        articles = await self._fetch_raw()
        events: list[NewsEvent] = []
        for art in articles:
            if last_id is not None and str(art.get("id")) == str(last_id):
                break
            events.append(self._to_event(art))
        if articles:
            await self._cache.set_json(
                CURSOR_KEY, str(articles[0].get("id")), ttl_seconds=86400
            )
        return events

    def _to_event(self, art: dict[str, Any]) -> NewsEvent:
        categories = [c for c in str(art.get("categories", "")).split("|") if c]
        symbols = [c.upper() for c in categories if c.isupper() and len(c) <= 6]
        return NewsEvent(
            source=Source.CRYPTOCOMPARE,
            article_id=str(art.get("id")),
            title=art.get("title", ""),
            body=art.get("body", "")[:4000],
            url=art.get("url"),
            published_at=int(art.get("published_on", 0)),
            source_name=art.get("source_info", {}).get("name", art.get("source", "?")),
            symbols=symbols,
            categories=categories,
            provider_sentiment=None,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cryptocompare_news_provider.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/collector-news/app/__init__.py services/collector-news/app/providers tests/test_cryptocompare_news_provider.py
git commit -m "feat(collector-news): add CryptoCompareNewsProvider (metered news primary)"
```

---

### Task 8: `RSSProvider` (news floor, unlimited)

**Files:**
- Create: `services/collector-news/app/providers/rss.py`
- Test: `tests/test_rss_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_rss_provider.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rss_provider.py -v`
Expected: FAIL — file does not exist

- [ ] **Step 3: Implement the provider**

Create `services/collector-news/app/providers/rss.py`:

> Post-review fixes applied: stable `hashlib.sha1` dedupe key (was `abs(hash())`, unstable across restarts) + per-item `NewsEvent` try/except-skip so one malformed `<link>` can't trip the floor breaker.

```python
"""RSS news provider -> NewsEvent. Unlimited, keyless floor source.

Parses standard RSS 2.0 feeds with the stdlib XML parser (no extra deps) and
dedupes items across polls via a per-feed set of seen GUIDs in Redis, so the
cascade's floor never republishes the same article.
"""

from __future__ import annotations

import hashlib
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
        feed_hash = hashlib.sha1(feed.encode()).hexdigest()[:16]
        seen_key = SEEN_KEY.format(feed_hash=feed_hash)
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
            try:
                event = NewsEvent(
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
            except Exception:
                # A malformed item (e.g. relative/invalid <link> failing URL
                # validation) must never take down the floor. It's already in
                # `fresh` so it's marked seen and not retried every poll.
                logger.warning("skipping malformed RSS item %s in %s", guid, feed)
                continue
            events.append(event)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_rss_provider.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/collector-news/app/providers/rss.py tests/test_rss_provider.py
git commit -m "feat(collector-news): add RSSProvider (unlimited news floor)"
```

---

### Task 9: `collector-news` service wiring

**Files:**
- Create: `services/collector-news/app/main.py`
- Create: `services/collector-news/pyproject.toml`

- [ ] **Step 1: Write the pyproject**

Create `services/collector-news/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "collector-news"
version = "0.1.0"
description = "Resilient news collector — CryptoCompare primary, RSS floor"
requires-python = ">=3.12"
dependencies = ["cmi-common", "httpx>=0.27"]

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

- [ ] **Step 2: Write the entrypoint**

Create `services/collector-news/app/main.py`:

```python
"""collector-news entrypoint: CryptoCompare -> RSS cascade on market.news."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.kafka import EventProducer, Topic
from cmi_common.runner import run_periodic
from cmi_common.sources import CircuitBreaker, SourceCascade

from .providers.cryptocompare import CryptoCompareNewsProvider
from .providers.rss import RSSProvider

POLL_INTERVAL = float(os.getenv("NEWS_POLL_INTERVAL", "300"))
CC_BASE_URL = os.getenv("CRYPTOCOMPARE_BASE_URL", "https://min-api.cryptocompare.com")
CC_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY") or None
RSS_FEEDS = [f for f in os.getenv("RSS_FEEDS", "").split(",") if f]
BREAKER_COOLDOWN = float(os.getenv("SOURCE_BREAKER_COOLDOWN", "300"))


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    providers = [
        CryptoCompareNewsProvider(CC_BASE_URL, CC_API_KEY, cache),
        RSSProvider(cache, feeds=RSS_FEEDS or None, source_name="RSS"),
    ]
    cascade = SourceCascade(
        providers,
        CircuitBreaker(cache, default_cooldown=BREAKER_COOLDOWN),
        producer,
        Topic.NEWS,
        service="collector-news",
    )
    app.state.cache = cache
    app.state.producer = producer
    app.state.cascade = cascade
    app.state.poller = asyncio.create_task(
        run_periodic(cascade.poll_once, POLL_INTERVAL, name="news-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.cascade.close()
    await app.state.producer.stop()
    await app.state.cache.close()


app = create_app("collector-news", on_startup=_startup, on_shutdown=_shutdown)
```

- [ ] **Step 3: Verify provider tests still green**

Run: `pytest tests/test_cryptocompare_news_provider.py tests/test_rss_provider.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add services/collector-news/app/main.py services/collector-news/pyproject.toml
git commit -m "feat(collector-news): wire CryptoCompare->RSS cascade poller"
```

---

## Phase 4 — Deploy & retire legacy collectors

### Task 10: Compose wiring + retire single-source collectors

**Files:**
- Modify: `docker-compose.yml:170-210` (replace the three collector blocks)
- Delete: `services/collector-twitter/`, `services/collector-reddit/`, `services/collector-cryptocompare/`
- Delete: `tests/test_twitter_collector.py`

- [ ] **Step 1: Replace the compose service blocks**

In `docker-compose.yml`, delete the `collector-cryptocompare`, `collector-reddit`, and `collector-twitter` blocks (lines ~170-210) and insert:

```yaml
  collector-social:
    <<: *service-defaults
    build: { context: ., dockerfile: docker/Dockerfile, args: { SERVICE_PATH: services/collector-social } }
    environment:
      <<: *common-env
      SOCIAL_POLL_INTERVAL: ${SOCIAL_POLL_INTERVAL:-300}
      BLUESKY_QUERY: ${BLUESKY_QUERY:-crypto}
      REDDIT_SUBREDDITS: ${REDDIT_SUBREDDITS:-CryptoCurrency,CryptoMoonShots,solana}
      REDDIT_CLIENT_ID: ${REDDIT_CLIENT_ID:-}
      REDDIT_CLIENT_SECRET: ${REDDIT_CLIENT_SECRET:-}
      SOURCE_BREAKER_COOLDOWN: ${SOURCE_BREAKER_COOLDOWN:-300}
    labels:
      - traefik.enable=true
      - traefik.http.routers.social.rule=Host(`social.cmi.localhost`)
      - traefik.http.routers.social.entrypoints=websecure
      - traefik.http.routers.social.tls=true
      - traefik.http.services.social.loadbalancer.server.port=8000

  collector-news:
    <<: *service-defaults
    build: { context: ., dockerfile: docker/Dockerfile, args: { SERVICE_PATH: services/collector-news } }
    environment:
      <<: *common-env
      NEWS_POLL_INTERVAL: ${NEWS_POLL_INTERVAL:-300}
      CRYPTOCOMPARE_API_KEY: ${CRYPTOCOMPARE_API_KEY:-}
      RSS_FEEDS: ${RSS_FEEDS:-}
      SOURCE_BREAKER_COOLDOWN: ${SOURCE_BREAKER_COOLDOWN:-300}
    labels:
      - traefik.enable=true
      - traefik.http.routers.news.rule=Host(`news.cmi.localhost`)
      - traefik.http.routers.news.entrypoints=websecure
      - traefik.http.routers.news.tls=true
      - traefik.http.services.news.loadbalancer.server.port=8000
```

- [ ] **Step 2: Delete retired services and their test**

```bash
git rm -r services/collector-twitter services/collector-reddit services/collector-cryptocompare tests/test_twitter_collector.py
```

- [ ] **Step 3: Validate compose config**

Run: `docker compose config >/dev/null && echo OK`
Expected: `OK` (no YAML/interpolation errors; `collector-social` and `collector-news` present, legacy collectors gone)

- [ ] **Step 4: Run the full suite + lint**

Run: `make test && make lint`
Expected: PASS — all new provider/cascade tests green, no references to deleted modules

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: replace single-source collectors with resilient social/news cascades"
```

---

### Task 11: Update docs + memory

**Files:**
- Modify: `CLAUDE.md` (pipeline diagram + collectors line)
- Create: `memory/sentiment-source-cascade.md`
- Modify: `memory/MEMORY.md` (index line)

- [ ] **Step 1: Update the pipeline description in `CLAUDE.md`**

In `CLAUDE.md`, in the "Pipeline" section, replace the collectors sentence:

```
Collectors (coingecko, dexscreener, cryptocompare, reddit, twitter) are stateless producers.
```

with:

```
Collectors (coingecko, dexscreener) are stateless producers. Social + news feeds run
through two resilient cascades — `collector-social` (Bluesky primary → Reddit fallback) and
`collector-news` (CryptoCompare primary → RSS floor) — each failing over on quota/error via a
Redis circuit breaker so the sentiment pipeline never dries up on free tiers.
```

- [ ] **Step 2: Write the memory file**

Create `memory/sentiment-source-cascade.md`:

```markdown
---
name: sentiment-source-cascade
description: social/news collectors are resilient cascades with circuit-breaker failover across free-tier sources
metadata:
  type: project
---

`collector-social` and `collector-news` replaced the single-source collectors
(twitter retired — X API is paid). Each wires an ordered `SourceCascade`
(`libs/cmi_common/cmi_common/sources/cascade.py`) of `Provider`s: primary
metered source first, unlimited floor last. A Redis `CircuitBreaker` trips a
provider on `RateLimitedError`/error (TTL-based auto half-open) and the cascade
fails over to the next, so the sentiment feed never goes dry on free tiers.

- Social: Bluesky (unlimited, keyless, no commercial restriction) → Reddit
  (100 req/min, non-commercial — kept as fallback only).
- News: CryptoCompare `/data/v2/news` (per-minute quota guard ≈2/min → ~86k/mo
  under the 100k free cap) → RSS (unlimited, keyless floor).

CryptoCompare `/data/social` is intentionally NOT used for sentiment — it
returns metrics without post text, and the scorer needs `text_sample`.

**Why:** as of 2026 the X API is pay-per-use and CryptoPanic dropped its free
tier; free-tier quotas alone can dry up mid-month. **How to apply:** add a new
free source by writing a `Provider` and inserting it into the cascade order in
the service's `main.py` — downstream (`sentiment-service`) is unchanged.
Related: [[web-terminal-backend-gap]].
```

- [ ] **Step 3: Add the index line**

In `memory/MEMORY.md`, append under the index list:

```
- [Sentiment source cascade](sentiment-source-cascade.md) — social/news collectors failover across free-tier sources via circuit breaker
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md memory/sentiment-source-cascade.md memory/MEMORY.md
git commit -m "docs: document resilient sentiment source cascade"
```

---

## Self-Review

**Spec coverage:**
- "Use free tiers, failover on rate limit, return when conditions allow" → `SourceCascade` (Task 3) + `CircuitBreaker` TTL auto half-open (Task 2) + proactive `cache.allow` guards in Reddit/CryptoCompare providers (Tasks 5, 7).
- "Run continuously all month, always have data" → unlimited floor providers Bluesky (Task 4) and RSS (Task 8) guarantee no dry spell; metered primaries drained first.
- "Replace paid APIs / find solutions" → X (paid) retired (Task 10); Bluesky + RSS added; Reddit kept as fallback; CryptoCompare kept via news.

**Placeholder scan:** No TODO/TBD; every code + test step contains full content. HTTP error handling is explicit (429 → `RateLimitedError`, other status re-raised, feed parse errors skipped).

**Type consistency:** `Provider.name`/`fetch`/`close` used consistently across all four providers and `SourceCascade`. `RateLimitedError(retry_after)` raised by providers, consumed by cascade → `breaker.trip(name, retry_after)`. `SocialEvent`/`NewsEvent` constructor kwargs match the schemas in `events/social.py` and `events/news.py` (note `NewsEvent.url` is a required `HttpUrl` — RSS/CryptoCompare both pass a real link). `Cache.allow(key, limit, window_seconds)` and `Cache.client.set(key, val, ex=)`/`exists` match `cache/redis.py`.

**Note for executor:** services aren't importable packages, so provider tests load modules via `importlib.util` (matching the existing `tests/test_twitter_collector.py` pattern). `cmi_common` IS importable, so cascade/breaker tests import it directly.
