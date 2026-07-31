# Derivatives & Fundamentals Sources + Scoring v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest DefiLlama fundamentals and Binance futures positioning as typed Kafka events, and extend the decision-engine scoring model to seven axes that renormalise over present evidence instead of scoring absent data as the worst case.

**Architecture:** Two new stateless collectors publish `FundamentalsEvent` and `DerivativesEvent` on their own topics. `ai-worker-haiku` folds them into its per-symbol Redis feature store, which already reaches `decision-engine` through `AnalysisEvent.meta["features"]`. `scoring.py` gains two axes and, critically, changes `_norm_*` to return `None` for absent input while the weighted sum divides by the present weight rather than by a constant 1.0.

**Tech Stack:** Python 3.12, FastAPI, httpx, aiokafka, Pydantic v2, SQLAlchemy 2.0 async, Redis, pytest (asyncio auto mode).

**Spec:** `docs/superpowers/specs/2026-07-31-derivatives-fundamentals-scoring-v2-design.md`

---

## Conventions used throughout

- Tests live in the repo-root `tests/` directory, not per service. Service modules are loaded with `load_service_module("<service-name>", "<dotted.module>")` from `tests/service_modules.py` — services all ship a package literally named `app`, so direct import would shadow.
- `async def test_*` needs no decorator; pytest runs in asyncio auto mode.
- Run the suite with `pytest tests/ -q`, lint with `make lint`.
- Every `*_pct_*` field is in percentage points (`5.0` means 5%). `funding_rate_8h` is the raw fraction Binance returns (`0.0001` = 0.01%), which is why it has no `pct` in its name.
- Commit after every task. Do not batch.

## File structure

**Created:**

| Path | Responsibility |
|---|---|
| `libs/cmi_common/cmi_common/db/universe.py` | `priced_symbols()` / `mention_counts()`, moved out of collector-kraken so two collectors cannot drift on what "majors" means |
| `services/collector-defillama/app/domain/unlocks.py` | Pure: emissions document → next unlock within 30 days |
| `services/collector-defillama/app/domain/mapper.py` | Pure: protocols + fees + unlock → `FundamentalsEvent` |
| `services/collector-defillama/app/infrastructure/llama_client.py` | HTTP + the 24h unlock cache |
| `services/collector-defillama/app/application/collector.py` | One polling cycle, including the round-robin |
| `services/collector-defillama/app/main.py` | Wiring |
| `services/collector-defillama/pyproject.toml` | Image build input |
| `services/collector-binance-futures/app/domain/symbols.py` | Pure: `BTCUSDT` → `BTC`, ambiguity guard |
| `services/collector-binance-futures/app/domain/mapper.py` | Pure: raw rows → `DerivativesEvent` |
| `services/collector-binance-futures/app/infrastructure/binance_client.py` | HTTP + weight-header self-throttle |
| `services/collector-binance-futures/app/application/collector.py` | Two-tier cycle |
| `services/collector-binance-futures/app/main.py` | Wiring |
| `services/collector-binance-futures/pyproject.toml` | Image build input |

Each service also needs empty `app/__init__.py`, `app/domain/__init__.py`, `app/application/__init__.py`, `app/infrastructure/__init__.py`.

**Modified:** `libs/cmi_common/cmi_common/events/{base,market,__init__}.py`, `libs/cmi_common/cmi_common/kafka/topics.py`, `services/collector-kraken/app/application/universe.py`, `services/ai-worker-haiku/app/{worker,main}.py`, `services/decision-engine/app/{scoring,engine}.py`, `docker-compose.yml`, `docker-compose.vps.yml`, `.github/workflows/deploy.yml`, `CLAUDE.md`.

---

## Task 1: Event schemas and topics

**Files:**
- Modify: `libs/cmi_common/cmi_common/events/base.py`
- Modify: `libs/cmi_common/cmi_common/events/market.py`
- Modify: `libs/cmi_common/cmi_common/events/__init__.py`
- Modify: `libs/cmi_common/cmi_common/kafka/topics.py`
- Test: `tests/test_derivatives_fundamentals_events.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_derivatives_fundamentals_events.py`:

```python
"""The two new market events survive a Kafka round-trip and are routable."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from cmi_common.events import (
    DerivativesEvent,
    FundamentalsEvent,
    parse_event,
)
from cmi_common.events.base import Source
from cmi_common.kafka.topics import TOPIC_EVENT, TOPIC_PARTITIONS, Topic


def test_derivatives_event_round_trips_through_parse_event() -> None:
    event = DerivativesEvent(
        source=Source.BINANCE_FUTURES,
        symbol="BTC",
        funding_rate_8h=0.0001,
        funding_annualized_pct=10.95,
        open_interest_usd=Decimal("1000000"),
        open_interest_change_pct_24h=5.0,
        long_short_account_ratio=1.8,
    )
    decoded = parse_event(event.as_kafka_value())
    assert isinstance(decoded, DerivativesEvent)
    assert decoded.symbol == "BTC"
    assert decoded.funding_rate_8h == 0.0001


def test_fundamentals_event_round_trips_through_parse_event() -> None:
    event = FundamentalsEvent(
        source=Source.DEFILLAMA,
        symbol="AAVE",
        coin_id="aave",
        tvl_usd=Decimal("5000000"),
        tvl_change_pct_7d=3.5,
        next_unlock_at=datetime(2026, 8, 15, tzinfo=UTC),
        next_unlock_pct_supply=2.5,
        has_unlock_schedule=True,
    )
    decoded = parse_event(event.as_kafka_value())
    assert isinstance(decoded, FundamentalsEvent)
    assert decoded.has_unlock_schedule is True
    assert decoded.next_unlock_pct_supply == 2.5


def test_events_partition_by_symbol() -> None:
    assert (
        DerivativesEvent(source=Source.BINANCE_FUTURES, symbol="ETH").partition_key()
        == "ETH"
    )
    assert (
        FundamentalsEvent(
            source=Source.DEFILLAMA, symbol="ETH", coin_id="ethereum"
        ).partition_key()
        == "ETH"
    )


def test_new_topics_are_registered_everywhere() -> None:
    # A topic missing from TOPIC_EVENT or TOPIC_PARTITIONS publishes fine and
    # fails later, which is how JournalEntryEvent shipped broken.
    for topic in (Topic.DERIVATIVES, Topic.FUNDAMENTALS):
        assert topic in TOPIC_EVENT
        assert topic in TOPIC_PARTITIONS
    assert TOPIC_EVENT[Topic.DERIVATIVES] is DerivativesEvent
    assert TOPIC_EVENT[Topic.FUNDAMENTALS] is FundamentalsEvent


def test_every_topic_has_partitions_and_an_event() -> None:
    assert set(TOPIC_EVENT) == set(Topic)
    assert set(TOPIC_PARTITIONS) == set(Topic)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_derivatives_fundamentals_events.py -q`
Expected: FAIL with `ImportError: cannot import name 'DerivativesEvent'`

- [ ] **Step 3: Add the enum members**

In `libs/cmi_common/cmi_common/events/base.py`, add to `EventType` after `DEX = "DexEvent"`:

```python
    DERIVATIVES = "DerivativesEvent"
    FUNDAMENTALS = "FundamentalsEvent"
```

and to `Source` after `DEXSCREENER = "dexscreener"`:

```python
    DEFILLAMA = "defillama"
    BINANCE_FUTURES = "binance-futures"
```

- [ ] **Step 4: Add the event models**

Append to `libs/cmi_common/cmi_common/events/market.py` (the module already imports `Decimal`, `Literal`, `Field`, `BaseEvent`, `EventType`; add `from datetime import datetime` at the top):

```python
class DerivativesEvent(BaseEvent):
    """Perp positioning from Binance futures, on ``market.derivatives.events``.

    Every field is nullable on purpose: the broad tier supplies funding for
    every perp in one request, while open interest and the long/short ratio are
    per-symbol calls made only for majors. A partial event is the normal case,
    not a degraded one.
    """

    event_type: Literal[EventType.DERIVATIVES] = EventType.DERIVATIVES
    symbol: str
    #: Raw fraction as Binance returns it: 0.0001 == 0.01% per 8h period.
    funding_rate_8h: float | None = None
    funding_annualized_pct: float | None = None
    open_interest_usd: Decimal | None = Field(default=None, ge=0)
    open_interest_change_pct_24h: float | None = None
    long_short_account_ratio: float | None = Field(default=None, gt=0)

    def partition_key(self) -> str:
        return self.symbol


class FundamentalsEvent(BaseEvent):
    """Protocol fundamentals and scheduled dilution, on
    ``market.fundamentals.events``.

    ``has_unlock_schedule`` is what keeps "no unlock is coming" distinct from
    "DefiLlama does not track this token". Only a minority of tokens have a
    published schedule, so collapsing the two would turn silence into a
    reassurance the data does not support.
    """

    event_type: Literal[EventType.FUNDAMENTALS] = EventType.FUNDAMENTALS
    symbol: str
    coin_id: str
    tvl_usd: Decimal | None = Field(default=None, ge=0)
    tvl_change_pct_7d: float | None = None
    fees_24h_usd: Decimal | None = Field(default=None, ge=0)
    fees_change_pct_7d: float | None = None
    #: None with ``has_unlock_schedule`` True means: schedule known, nothing
    #: within the next 30 days.
    next_unlock_at: datetime | None = None
    next_unlock_pct_supply: float | None = Field(default=None, ge=0)
    has_unlock_schedule: bool = False

    def partition_key(self) -> str:
        return self.symbol
```

- [ ] **Step 5: Export and register**

In `libs/cmi_common/cmi_common/events/__init__.py`, change the market import to:

```python
from .market import DerivativesEvent, DexEvent, FundamentalsEvent, PriceEvent, VolumeEvent
```

Add both to the `AnyEvent` union (one member per line, as the file's comment requires) after `| DexEvent`:

```python
        | DerivativesEvent
        | FundamentalsEvent
```

Add `"DerivativesEvent"` and `"FundamentalsEvent"` to `__all__`.

In `libs/cmi_common/cmi_common/kafka/topics.py`, import both from `..events.market`, then add to `Topic` after `DEX`:

```python
    DERIVATIVES = "market.derivatives.events"
    FUNDAMENTALS = "market.fundamentals.events"
```

to `TOPIC_EVENT`:

```python
    Topic.DERIVATIVES: DerivativesEvent,
    Topic.FUNDAMENTALS: FundamentalsEvent,
```

and to `TOPIC_PARTITIONS`:

```python
    # Funding for every perp, republished each cycle to outlive the feature TTL.
    Topic.DERIVATIVES: 6,
    # One event per protocol per 10 min: far quieter than the price topics.
    Topic.FUNDAMENTALS: 3,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_derivatives_fundamentals_events.py tests/test_events.py -q`
Expected: PASS, all tests

- [ ] **Step 7: Commit**

```bash
git add libs/cmi_common tests/test_derivatives_fundamentals_events.py
git commit -m "feat(events): add DerivativesEvent and FundamentalsEvent with their topics"
```

---

## Task 2: Move the universe helpers into cmi_common

Two collectors now need "which symbols do we price" and "which are majors". Duplicating the definitions would let them drift silently.

**Files:**
- Create: `libs/cmi_common/cmi_common/db/universe.py`
- Modify: `services/collector-kraken/app/application/universe.py`
- Test: `tests/test_shared_universe.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_shared_universe.py`:

```python
"""The universe helpers are shared, and collector-kraken still re-exports them."""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.db import universe as shared


def test_shared_module_exposes_the_two_sql_helpers() -> None:
    assert callable(shared.priced_symbols)
    assert callable(shared.mention_counts)


def test_collector_kraken_reexports_the_same_objects() -> None:
    # Same object, not a copy: a re-export that drifted into a second
    # implementation is exactly the failure this move exists to prevent.
    kraken = load_service_module("collector-kraken", "application.universe")
    assert kraken.priced_symbols is shared.priced_symbols
    assert kraken.mention_counts is shared.mention_counts


def test_majors_applies_the_mention_threshold() -> None:
    mentions = {"BTC": 421, "ETH": 163, "DOT": 11, "FOO": 3}
    assert shared.majors({"BTC", "ETH", "DOT", "FOO"}, mentions, 10) == {
        "BTC",
        "ETH",
        "DOT",
    }


def test_majors_excludes_symbols_absent_from_the_mention_map() -> None:
    assert shared.majors({"BTC", "GHOST"}, {"BTC": 50}, 10) == {"BTC"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shared_universe.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmi_common.db.universe'`

- [ ] **Step 3: Create the shared module**

Create `libs/cmi_common/cmi_common/db/universe.py`:

```python
"""Which symbols the platform considers live, and which of them are majors.

Both are derived from data on every call, never hard-coded. Shared rather than
per-collector: collector-kraken and collector-binance-futures both need the
majors set, and two copies of the definition would drift the moment one of them
was tuned.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ContentSentimentAgg, Price

#: Knee of the observed mention distribution (measured 2026-07-29: 11 symbols
#: clear 10 over 7 days, the next tier sits in single digits).
DEFAULT_MIN_MENTIONS = 10


def majors(
    symbols: set[str],
    mentions: dict[str, int],
    *,
    min_mentions: int = DEFAULT_MIN_MENTIONS,
) -> set[str]:
    """Symbols with enough sentiment coverage to fuse on.

    Which is also, and this is why the definition is shared, the set worth
    spending per-symbol API budget on: collector-binance-futures pays one open
    interest call and one long/short call per major, so the threshold decides
    the request bill as much as it decides the strategy.
    """
    return {s for s in symbols if mentions.get(s, 0) >= min_mentions}


async def priced_symbols(session: AsyncSession, *, hours: int = 24) -> set[str]:
    since = datetime.now(tz=UTC) - timedelta(hours=hours)
    stmt = select(Price.symbol).where(Price.time >= since).distinct()
    return set((await session.execute(stmt)).scalars().all())


async def mention_counts(session: AsyncSession, *, days: int = 7) -> dict[str, int]:
    """Mentions per symbol over the window, both kinds summed."""
    since = datetime.now(tz=UTC) - timedelta(days=days)
    stmt = (
        select(ContentSentimentAgg.symbol, func.sum(ContentSentimentAgg.mentions))
        .where(ContentSentimentAgg.bucket_start >= since)
        .group_by(ContentSentimentAgg.symbol)
    )
    return {sym: int(total or 0) for sym, total in (await session.execute(stmt)).all()}
```

- [ ] **Step 4: Re-export from collector-kraken**

In `services/collector-kraken/app/application/universe.py`, delete the `priced_symbols` and `mention_counts` function bodies and the now-unused `func`, `select`, `datetime`, `timedelta`, `UTC`, `ContentSentimentAgg`, `Price` imports, then add near the top:

```python
from cmi_common.db.universe import DEFAULT_MIN_MENTIONS, mention_counts, priced_symbols

__all__ = [
    "DEFAULT_MIN_MENTIONS",
    "ambiguous_symbols",
    "intersect",
    "mention_counts",
    "priced_symbols",
    "split_regimes",
    "token_symbol_ranks",
    "untradable",
]
```

Delete the local `DEFAULT_MIN_MENTIONS = 10` definition — it now comes from the shared module. Keep `intersect`, `ambiguous_symbols`, `untradable` and `token_symbol_ranks` where they are: they depend on `VenuePairSpec` or on the `Token` table and are Kraken's business.

**`split_regimes` stays in this file but must delegate to `majors()`.** Leaving it with its own
copy of the predicate would mean this task creates a second definition of the very rule it
exists to de-duplicate — and the shared one would serve only a consumer that does not exist
yet, while the real consumer kept its own:

```python
def split_regimes(
    specs: list[VenuePairSpec],
    *,
    mentions: dict[str, int],
    min_mentions: int = DEFAULT_MIN_MENTIONS,
) -> tuple[list[VenuePairSpec], list[VenuePairSpec]]:
    """(majors, alts) — majors are sentiment-covered enough to fuse on."""
    major_symbols = majors(
        {s.symbol for s in specs}, mentions, min_mentions=min_mentions
    )
    return (
        [s for s in specs if s.symbol in major_symbols],
        [s for s in specs if s.symbol not in major_symbols],
    )
```

The signature is unchanged, so `tests/test_kraken_universe.py` must stay green untouched. Add
`majors` to both the import and `__all__`. This also removes the local `majors` list variable,
which would otherwise shadow the imported name.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_shared_universe.py tests/ -q -k "universe or kraken"`
Expected: PASS, no regression in the existing collector-kraken tests

- [ ] **Step 6: Commit**

```bash
git add libs/cmi_common/cmi_common/db/universe.py services/collector-kraken tests/test_shared_universe.py
git commit -m "refactor(universe): share the priced/majors helpers between collectors"
```

---

## Task 3: DefiLlama unlock extraction (pure)

The emissions document is ~2.25 MB of which ~8 KB matters. This task is the parser, tested against a fixture with the exact shape the live API returns.

**Files:**
- Create: `services/collector-defillama/app/__init__.py` (empty)
- Create: `services/collector-defillama/app/domain/__init__.py` (empty)
- Create: `services/collector-defillama/app/domain/unlocks.py`
- Test: `tests/test_defillama_unlocks.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_defillama_unlocks.py`:

```python
"""Extracting the next unlock from a DefiLlama emissions document.

Fixture shape verified against the live response for `aave` on 2026-07-31:
metadata.events[] carries {timestamp, noOfTokens[], unlockType} and reaches
back to 2017, while supplyMetrics.maxSupply is the denominator.
"""

from __future__ import annotations

from datetime import UTC, datetime

from service_modules import load_service_module

unlocks = load_service_module("collector-defillama", "domain.unlocks")

NOW = datetime(2026, 7, 31, tzinfo=UTC)


def _doc(events: list[dict], max_supply: float = 1_000_000.0) -> dict:
    return {
        "gecko_id": "aave",
        "name": "Aave",
        "metadata": {"events": events},
        "supplyMetrics": {"maxSupply": max_supply},
        "documentedData": {"ignored": "megabytes of chart series"},
    }


def _event(when: datetime, tokens: float) -> dict:
    return {
        "timestamp": int(when.timestamp()),
        "noOfTokens": [tokens],
        "unlockType": "cliff",
        "description": "A cliff of {tokens[0]} tokens",
    }


def test_returns_the_next_future_unlock_within_thirty_days() -> None:
    due = datetime(2026, 8, 10, tzinfo=UTC)
    result = unlocks.next_unlock(_doc([_event(due, 25_000)]), now=NOW)
    assert result is not None
    assert result.at == due
    assert result.pct_supply == 2.5  # 25_000 / 1_000_000 -> percentage points


def test_historical_unlocks_are_ignored() -> None:
    # events[] reaches back years; without the future filter the "next" unlock
    # would be one from 2017 and every token would look maximally diluted.
    past = datetime(2017, 12, 9, tzinfo=UTC)
    assert unlocks.next_unlock(_doc([_event(past, 360_000)]), now=NOW) is None


def test_unlocks_beyond_thirty_days_are_ignored() -> None:
    far = datetime(2026, 10, 1, tzinfo=UTC)
    assert unlocks.next_unlock(_doc([_event(far, 50_000)]), now=NOW) is None


def test_several_unlocks_in_the_window_are_summed_and_dated_at_the_earliest() -> None:
    first = datetime(2026, 8, 5, tzinfo=UTC)
    second = datetime(2026, 8, 20, tzinfo=UTC)
    result = unlocks.next_unlock(
        _doc([_event(second, 30_000), _event(first, 20_000)]), now=NOW
    )
    assert result is not None
    assert result.at == first
    assert result.pct_supply == 5.0


def test_multiple_token_amounts_in_one_event_are_summed() -> None:
    due = datetime(2026, 8, 10, tzinfo=UTC)
    doc = _doc([{"timestamp": int(due.timestamp()), "noOfTokens": [10_000, 5_000]}])
    result = unlocks.next_unlock(doc, now=NOW)
    assert result is not None
    assert result.pct_supply == 1.5


def test_missing_max_supply_yields_no_reading_rather_than_a_zero() -> None:
    # No denominator means the percentage is unknown, and unknown must not be
    # served as a confident 0.0.
    due = datetime(2026, 8, 10, tzinfo=UTC)
    doc = _doc([_event(due, 25_000)], max_supply=0.0)
    assert unlocks.next_unlock(doc, now=NOW) is None


def test_document_without_events_yields_no_unlock() -> None:
    assert unlocks.next_unlock(_doc([]), now=NOW) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_defillama_unlocks.py -q`
Expected: FAIL — the `collector-defillama` service directory does not exist yet

- [ ] **Step 3: Create the package and the parser**

Create empty `services/collector-defillama/app/__init__.py` and `services/collector-defillama/app/domain/__init__.py`.

Create `services/collector-defillama/app/domain/unlocks.py`:

```python
"""Reduce a DefiLlama emissions document to its next unlock.

The document is ~2.25 MB, almost all of it `documentedData` and chart series.
Everything this module needs is `metadata.events` (~8 KB) and
`supplyMetrics.maxSupply`; the rest is parsed by json and thrown away.

`events` is a full history, oldest first, reaching back to a protocol's first
distribution. Filtering to the future is therefore load-bearing: without it the
"next" unlock for Aave is one from December 2017.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

#: Unlocks further out than this do not affect a trade being opened today.
HORIZON_DAYS = 30


@dataclass(frozen=True, slots=True)
class Unlock:
    at: datetime       # earliest contributing event in the window
    pct_supply: float  # percentage points of max supply, summed over the window


def next_unlock(
    document: dict[str, Any], *, now: datetime | None = None
) -> Unlock | None:
    """Total supply unlocking within the horizon, dated at its earliest event.

    Returns None when nothing is scheduled in the window — a measurement, and
    the good news.

    Raises ValueError when something *is* scheduled but cannot be sized, because
    the supply denominator is missing. Returning None there would be read one
    layer up as "we looked, nothing is coming": the client inserts the coin id
    on any non-raising call, the mapper turns that into
    ``has_unlock_schedule=True``, and the scorer turns *that* into a perfect
    fundamentals score — so a token about to dilute 10% of its supply would read
    as impeccably healthy. Raising leaves the key absent instead, so the axis
    reports unknown and is excluded from the score.

    A malformed document — a non-numeric timestamp, a null token count — raises
    for the same reason, and by design. Do not add a tolerant ``except: continue``
    to this loop: it would convert "unknown" into "nothing is coming", which is
    the failure this whole module is shaped to avoid. The caller must let the
    exception leave the key absent, which means ``next_unlock`` has to stay
    *outside* the client's try/except around the fetch.
    """
    if now is None:
        now = datetime.now(tz=UTC)
    horizon = now + timedelta(days=HORIZON_DAYS)

    earliest: datetime | None = None
    tokens = 0.0
    for event in document.get("metadata", {}).get("events", []):
        raw_ts = event.get("timestamp")
        if raw_ts is None:
            continue
        at = datetime.fromtimestamp(int(raw_ts), tz=UTC)
        if not (now < at <= horizon):
            continue
        contributed = sum(float(n) for n in event.get("noOfTokens") or [])
        if contributed <= 0:
            # A zero-size marker must not date an unlock it did not cause: the
            # date is surfaced to the frontend beside a size from another event.
            continue
        tokens += contributed
        if earliest is None or at < earliest:
            earliest = at

    if earliest is None:
        return None

    # Checked only now: with nothing scheduled, the denominator is irrelevant
    # and None is the honest answer rather than a failure.
    max_supply = float(document.get("supplyMetrics", {}).get("maxSupply") or 0.0)
    if max_supply <= 0:
        raise ValueError("unlock scheduled but maxSupply is missing or zero")
    return Unlock(at=earliest, pct_supply=round(100.0 * tokens / max_supply, 4))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_defillama_unlocks.py -q`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add services/collector-defillama tests/test_defillama_unlocks.py
git commit -m "feat(defillama): parse the next unlock out of an emissions document"
```

---

## Task 4: DefiLlama event mapping (pure)

**Files:**
- Create: `services/collector-defillama/app/domain/mapper.py`
- Test: `tests/test_defillama_mapper.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_defillama_mapper.py`:

```python
"""Protocol rows + fees + unlocks -> FundamentalsEvent."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from service_modules import load_service_module

from cmi_common.events import FundamentalsEvent

mapper = load_service_module("collector-defillama", "domain.mapper")
unlocks = load_service_module("collector-defillama", "domain.unlocks")

# coin_id -> symbol, as the tokens table supplies it.
KNOWN = {"aave": "AAVE", "ethereum": "ETH"}


def _protocol(slug: str, gecko: str | None, tvl: float, change_7d: float | None):
    return {"slug": slug, "gecko_id": gecko, "tvl": tvl, "change_7d": change_7d}


def test_protocol_maps_to_an_event_keyed_by_the_known_symbol() -> None:
    events = mapper.to_fundamentals_events(
        [_protocol("aave", "aave", 5_000_000.0, 3.5)], fees={}, unlocks={}, known=KNOWN
    )
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, FundamentalsEvent)
    assert event.symbol == "AAVE"
    assert event.coin_id == "aave"
    assert event.tvl_usd == Decimal("5000000")
    assert event.tvl_change_pct_7d == 3.5
    assert event.has_unlock_schedule is False


def test_protocol_without_a_gecko_id_is_dropped_not_guessed() -> None:
    # Matching on the ticker instead would reintroduce exactly the ambiguity
    # that collector-kraken's ambiguous_symbols exists to record.
    assert (
        mapper.to_fundamentals_events(
            [_protocol("mystery", None, 1.0, 0.0)], fees={}, unlocks={}, known=KNOWN
        )
        == []
    )


def test_protocol_we_do_not_track_is_dropped() -> None:
    assert (
        mapper.to_fundamentals_events(
            [_protocol("obscure", "obscure-coin", 1.0, 0.0)],
            fees={},
            unlocks={},
            known=KNOWN,
        )
        == []
    )


def test_parent_and_child_protocols_sum_into_one_event() -> None:
    # DefiLlama lists deployments separately; a token's TVL is the token's, not
    # one deployment's, so summing before emission is what makes the number mean
    # what the axis assumes it means.
    events = mapper.to_fundamentals_events(
        [
            _protocol("aave-v2", "aave", 3_000_000.0, 2.0),
            _protocol("aave-v3", "aave", 2_000_000.0, 6.0),
        ],
        fees={},
        unlocks={},
        known=KNOWN,
    )
    assert len(events) == 1
    assert events[0].tvl_usd == Decimal("5000000")


def test_tvl_weighted_change_is_used_when_deployments_disagree() -> None:
    events = mapper.to_fundamentals_events(
        [
            _protocol("aave-v2", "aave", 3_000_000.0, 0.0),
            _protocol("aave-v3", "aave", 1_000_000.0, 8.0),
        ],
        fees={},
        unlocks={},
        known=KNOWN,
    )
    # (3M*0 + 1M*8) / 4M == 2.0
    assert events[0].tvl_change_pct_7d == 2.0


def test_fees_are_joined_by_slug_not_by_gecko_id() -> None:
    # Verified against the live API: /overview/fees carries no gecko_id at all
    # (0 of 2514 rows), so the join has to go through slug. Keying on gecko_id
    # would find nothing and report every protocol as fee-less.
    events = mapper.to_fundamentals_events(
        [_protocol("aave-v3", "aave", 1.0, 0.0)],
        fees={"aave-v3": {"total24h": 42_000.0, "total7d": 200.0, "total14dto7d": 160.0}},
        unlocks={},
        known=KNOWN,
    )
    assert events[0].fees_24h_usd == Decimal("42000")
    # 7d-over-7d is derived: there is no change_7dover7d field in the payload.
    assert events[0].fees_change_pct_7d == 25.0


def test_fees_are_summed_across_a_tokens_deployments() -> None:
    events = mapper.to_fundamentals_events(
        [
            _protocol("aave-v2", "aave", 1.0, 0.0),
            _protocol("aave-v3", "aave", 1.0, 0.0),
        ],
        fees={
            "aave-v2": {"total24h": 1_000.0, "total7d": 50.0, "total14dto7d": 100.0},
            "aave-v3": {"total24h": 3_000.0, "total7d": 150.0, "total14dto7d": 100.0},
        },
        unlocks={},
        known=KNOWN,
    )
    assert events[0].fees_24h_usd == Decimal("4000")
    # (200 - 200) / 200 -> 0.0, computed on the sums rather than averaged per
    # deployment, which would have given +25%.
    assert events[0].fees_change_pct_7d == 0.0


def test_a_zero_fee_baseline_yields_no_change_rather_than_a_division() -> None:
    events = mapper.to_fundamentals_events(
        [_protocol("aave-v3", "aave", 1.0, 0.0)],
        fees={"aave-v3": {"total24h": 10.0, "total7d": 50.0, "total14dto7d": 0.0}},
        unlocks={},
        known=KNOWN,
    )
    assert events[0].fees_change_pct_7d is None


def test_a_protocol_with_no_fee_row_reports_no_fees() -> None:
    events = mapper.to_fundamentals_events(
        [_protocol("aave-v3", "aave", 1.0, 0.0)], fees={}, unlocks={}, known=KNOWN
    )
    assert events[0].fees_24h_usd is None
    assert events[0].fees_change_pct_7d is None


def test_a_known_schedule_with_a_pending_unlock_is_carried() -> None:
    due = datetime(2026, 8, 10, tzinfo=UTC)
    events = mapper.to_fundamentals_events(
        [_protocol("aave", "aave", 1.0, 0.0)],
        fees={},
        unlocks={"aave": unlocks.Unlock(at=due, pct_supply=2.5)},
        known=KNOWN,
    )
    assert events[0].has_unlock_schedule is True
    assert events[0].next_unlock_at == due
    assert events[0].next_unlock_pct_supply == 2.5


def test_a_known_schedule_with_nothing_pending_is_a_measurement() -> None:
    # None under a True flag is the good news: we looked, and nothing is coming.
    events = mapper.to_fundamentals_events(
        [_protocol("aave", "aave", 1.0, 0.0)],
        fees={},
        unlocks={"aave": None},
        known=KNOWN,
    )
    assert events[0].has_unlock_schedule is True
    assert events[0].next_unlock_at is None
    assert events[0].next_unlock_pct_supply is None


def test_an_untracked_token_reports_no_schedule() -> None:
    events = mapper.to_fundamentals_events(
        [_protocol("aave", "aave", 1.0, 0.0)], fees={}, unlocks={}, known=KNOWN
    )
    assert events[0].has_unlock_schedule is False


def test_emission_key_uses_the_parent_slug_when_there_is_one() -> None:
    # Measured against the live API: the emissions list contains "aave" while
    # /protocols only ever contains "aave-v2", "aave-v3" and friends. Matching
    # on slug alone covers 220 of 359 scheduled protocols and misses Aave
    # outright; going through parentProtocol covers 335.
    assert (
        mapper.emission_key({"slug": "aave-v3", "parentProtocol": "parent#aave"})
        == "aave"
    )


def test_emission_key_falls_back_to_the_slug() -> None:
    assert mapper.emission_key({"slug": "drift"}) == "drift"


def test_emission_key_is_absent_when_the_row_has_neither() -> None:
    assert mapper.emission_key({"name": "nameless"}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_defillama_mapper.py -q`
Expected: FAIL with `FileNotFoundError` on `domain/mapper.py`

- [ ] **Step 3: Write the mapper**

Create `services/collector-defillama/app/domain/mapper.py`:

```python
"""Fold DefiLlama's per-deployment rows into one event per token."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from cmi_common.events import FundamentalsEvent
from cmi_common.events.base import Source

from .unlocks import Unlock


@dataclass(slots=True)
class _Bucket:
    """Per-token accumulator.

    The two ``saw_*`` flags are the whole point: a bucket that starts at 0.0
    cannot otherwise tell "no deployment reported this" from "the deployments
    reported zero", and collapsing those is precisely the unknown-vs-measured
    confusion this platform already shipped once.
    """

    tvl: float = 0.0
    weighted_change: float = 0.0
    fees_24h: float = 0.0
    fees_7d: float = 0.0
    fees_prev_7d: float = 0.0
    saw_tvl: bool = False
    saw_fees: bool = False


def emission_key(row: dict[str, Any]) -> str | None:
    """The slug DefiLlama's emissions list uses for this protocol row.

    Emission slugs are *parent* slugs. The list contains ``aave``; ``/protocols``
    only ever contains ``aave-v2``, ``aave-v3`` and their siblings, each carrying
    ``parentProtocol: "parent#aave"``. Matching the emissions list against
    ``slug`` alone covers 220 of the 359 scheduled protocols and misses Aave
    entirely — and it misses them silently, since an unmatched protocol simply
    reports no schedule. Going through the parent covers 335.
    """
    parent = row.get("parentProtocol")
    if parent:
        return str(parent).split("#", 1)[-1] or None
    return row.get("slug") or None


def to_fundamentals_events(
    protocols: list[dict[str, Any]],
    *,
    fees: dict[str, dict[str, Any]],
    unlocks: dict[str, Unlock | None],
    known: dict[str, str],
) -> list[FundamentalsEvent]:
    """One event per tracked token.

    ``known`` maps CoinGecko id -> symbol. A protocol without a ``gecko_id``, or
    whose id we do not track, is dropped rather than matched on its ticker:
    ticker collisions are real and silently wrong.

    ``fees`` is keyed by protocol **slug**, because the fees payload carries no
    ``gecko_id`` at all — verified against the live API, 0 of 2,514 rows have
    one. Keying it by coin id would match nothing and quietly report every
    protocol as fee-less.

    ``unlocks`` maps CoinGecko id -> the pending unlock, or None when the
    schedule is known and empty. A key that is simply absent means DefiLlama
    publishes no schedule for that token, which is a different statement.
    """
    aggregated: dict[str, _Bucket] = {}
    for row in protocols:
        coin_id = row.get("gecko_id")
        if not coin_id or coin_id not in known:
            continue
        bucket = aggregated.setdefault(coin_id, _Bucket())
        raw_tvl = row.get("tvl")
        if raw_tvl is not None:
            bucket.saw_tvl = True
            bucket.tvl += float(raw_tvl)
            # TVL-weighted: a $3M deployment flat and a $1M deployment up 8% is
            # a 2% move for the token, not the 4% a plain mean would report.
            change = row.get("change_7d")
            if change is not None:
                bucket.weighted_change += float(raw_tvl) * float(change)
        # Fees aggregate the same way TVL does. Aave alone is seven deployment
        # rows sharing one gecko_id, so reading any single row would report a
        # fraction of the token's revenue as if it were all of it.
        fee_row = fees.get(row.get("slug") or "")
        if fee_row:
            bucket.saw_fees = True
            bucket.fees_24h += float(fee_row.get("total24h") or 0.0)
            bucket.fees_7d += float(fee_row.get("total7d") or 0.0)
            bucket.fees_prev_7d += float(fee_row.get("total14dto7d") or 0.0)

    events: list[FundamentalsEvent] = []
    for coin_id, bucket in aggregated.items():
        baseline = bucket.fees_prev_7d
        unlock = unlocks.get(coin_id)
        events.append(
            FundamentalsEvent(
                source=Source.DEFILLAMA,
                symbol=known[coin_id],
                coin_id=coin_id,
                # saw_*, not truthiness. A protocol that genuinely earned $0 in
                # fees has been measured; reporting None would make it
                # indistinguishable from one DefiLlama does not cover — and
                # since the scorer *excludes* an absent axis rather than
                # penalising it, the dead protocol would outscore the live one.
                tvl_usd=Decimal(str(bucket.tvl)) if bucket.saw_tvl else None,
                tvl_change_pct_7d=(
                    round(bucket.weighted_change / bucket.tvl, 4)
                    if bucket.tvl > 0
                    else None
                ),
                fees_24h_usd=(
                    Decimal(str(bucket.fees_24h)) if bucket.saw_fees else None
                ),
                # Derived: the payload has change_30dover30d but no 7d-over-7d.
                # A zero baseline yields None, not a division and not a 0.0 that
                # would read as "fees held flat" — that ratio is genuinely
                # undefined, unlike the value fields above.
                fees_change_pct_7d=(
                    round(100.0 * (bucket.fees_7d - baseline) / baseline, 4)
                    if baseline > 0
                    else None
                ),
                next_unlock_at=unlock.at if unlock else None,
                next_unlock_pct_supply=unlock.pct_supply if unlock else None,
                has_unlock_schedule=coin_id in unlocks,
            )
        )
    return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_defillama_mapper.py -q`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add services/collector-defillama tests/test_defillama_mapper.py
git commit -m "feat(defillama): map protocol rows, fees and unlocks to one event per token"
```

---

## Task 5: DefiLlama client

**Files:**
- Create: `services/collector-defillama/app/infrastructure/__init__.py` (empty)
- Create: `services/collector-defillama/app/infrastructure/llama_client.py`
- Test: `tests/test_defillama_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_defillama_client.py`:

```python
"""The client's caching and failure behaviour, without touching the network."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from service_modules import load_service_module

client_mod = load_service_module("collector-defillama", "infrastructure.llama_client")


class FakeCache:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.data: dict[str, Any] = dict(initial or {})
        self.writes: list[tuple[str, Any, int]] = []

    async def get_json(self, key: str) -> Any | None:
        return self.data.get(key)

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        self.data[key] = value
        self.writes.append((key, value, ttl_seconds))

    async def allow(self, key: str, limit: int, window: int) -> bool:
        return True


def _client(handler, cache: FakeCache):
    transport = httpx.MockTransport(handler)
    return client_mod.LlamaClient(cache, transport=transport)


async def test_cached_unlock_is_served_without_a_request() -> None:
    # The whole point of the 24h cache: a 2.25 MB body must not be refetched.
    cache = FakeCache(
        {"defillama:unlock:aave": {"at": "2026-08-10T00:00:00+00:00", "pct_supply": 2.5}}
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    unlock = await _client(handler, cache).unlock("aave", "aave")
    assert calls == []
    assert unlock is not None
    assert unlock.pct_supply == 2.5
    assert unlock.at == datetime(2026, 8, 10, tzinfo=UTC)


async def test_a_cached_empty_schedule_is_served_as_a_known_absence() -> None:
    cache = FakeCache({"defillama:unlock:aave": {"at": None, "pct_supply": None}})

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not fetch")

    result = await _client(handler, cache).unlock("aave", "aave")
    assert result is None


async def test_fetching_an_unlock_caches_the_extraction_not_the_body() -> None:
    document = {
        "gecko_id": "aave",
        "metadata": {
            "events": [
                {
                    "timestamp": int(datetime(2099, 1, 1, tzinfo=UTC).timestamp()),
                    "noOfTokens": [25_000],
                }
            ]
        },
        "supplyMetrics": {"maxSupply": 1_000_000.0},
        "documentedData": {"huge": "x" * 10_000},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document)

    cache = FakeCache()
    # 2099 is outside the 30-day horizon, so the extraction is an empty schedule.
    await _client(handler, cache).unlock("aave", "aave")
    key, value, ttl = cache.writes[0]
    assert key == "defillama:unlock:aave"
    # Pinning the value exactly is what proves the 2.25 MB body was discarded:
    # any leakage of documentedData or the raw events would fail this equality.
    # (An additional `"documentedData" not in str(value)` would be vacuous —
    # the equality above already forecloses it.)
    assert value == {"at": None, "pct_supply": None}
    assert ttl == client_mod.UNLOCK_TTL_SECONDS


async def test_a_failed_unlock_fetch_returns_absent_without_raising() -> None:
    # One protocol's 2.25 MB document failing must not abort the cycle for the
    # others, and must never be substituted by a zero.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    cache = FakeCache()
    result = await _client(handler, cache).unlock("aave", "aave")
    assert result is None
    assert cache.writes == []  # nothing poisoned the cache


async def test_protocols_are_returned_as_a_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/protocols"
        return httpx.Response(
            200, json=[{"slug": "aave-v3", "gecko_id": "aave", "tvl": 1.0}]
        )

    rows = await _client(handler, FakeCache()).protocols()
    assert rows[0]["slug"] == "aave-v3"


async def test_fees_are_keyed_by_slug_and_exclude_the_chart_series() -> None:
    # The exclude params take the response from 24.6 MB to 3.7 MB. Dropping
    # them would move 3.5 GB a day to read a handful of numbers, so they are
    # asserted rather than trusted.
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["excludeTotalDataChart"] == "true"
        assert request.url.params["excludeTotalDataChartBreakdown"] == "true"
        return httpx.Response(
            200,
            json={
                "protocols": [
                    {"slug": "aave-v3", "total24h": 42.0, "total7d": 200.0},
                    {"name": "no slug", "total24h": 1.0},
                ]
            },
        )

    fees = await _client(handler, FakeCache()).fees()
    assert fees["aave-v3"]["total24h"] == 42.0
    # A row without a slug cannot be joined to anything, so it is dropped
    # rather than keyed under an empty string.
    assert len(fees) == 1


async def test_emission_slugs_are_returned_as_a_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["aave", "drift", "benddao"])

    slugs = await _client(handler, FakeCache()).emission_slugs()
    assert slugs == {"aave", "drift", "benddao"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_defillama_client.py -q`
Expected: FAIL with `FileNotFoundError` on `infrastructure/llama_client.py`

- [ ] **Step 3: Write the client**

Create empty `services/collector-defillama/app/infrastructure/__init__.py`, then `services/collector-defillama/app/infrastructure/llama_client.py`:

```python
"""HTTP access to DefiLlama, plus the cache that makes unlocks affordable.

Two hosts are in play. `api.llama.fi` serves the bulk TVL and fee endpoints.
Unlock schedules are *not* on the free API — `api.llama.fi/emissions` answers
402 Payment Required and belongs to the paid Pro tier — so they come from
`defillama-datasets.llama.fi`, the dataset CDN the DefiLlama front-end itself
reads. That CDN serves one document per protocol at roughly 2.25 MB, of which
about 8 KB is useful.

Hence the cache: the extraction, never the body, is stored for 24 hours.
Schedules are near-static, so this loses nothing and turns a 2.25 MB fetch into
a Redis read for the rest of the day.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.observability import UPSTREAM_REQUESTS

from ..domain.unlocks import Unlock, next_unlock

logger = logging.getLogger(__name__)

SERVICE = "collector-defillama"
API_BASE = "https://api.llama.fi"
DATASETS_BASE = "https://defillama-datasets.llama.fi"
UNLOCK_KEY = "defillama:unlock:{coin_id}"
#: Schedules are published well in advance and change rarely.
UNLOCK_TTL_SECONDS = 86_400


class LlamaClient:
    def __init__(
        self,
        cache: Cache,
        *,
        timeout: float = 15.0,
        unlock_timeout: float = 30.0,
        rate_limit_per_min: int = 60,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._cache = cache
        self._unlock_timeout = unlock_timeout
        self._rate_limit = rate_limit_per_min
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, *, timeout: float | None = None) -> Any:
        if not await self._cache.allow("defillama", self._rate_limit, 60):
            raise RuntimeError("DefiLlama rate limit exceeded")
        # httpx reads an explicit timeout=None as "no timeout at all", not as
        # "use the client default", so the override is passed only when set.
        overrides = {"timeout": timeout} if timeout is not None else {}
        try:
            resp = await self._client.get(url, **overrides)
            resp.raise_for_status()
            UPSTREAM_REQUESTS.labels(SERVICE, "defillama", "ok").inc()
            return resp.json()
        except httpx.HTTPError:
            UPSTREAM_REQUESTS.labels(SERVICE, "defillama", "error").inc()
            raise

    async def protocols(self) -> list[dict[str, Any]]:
        """Every protocol with its TVL, 7d change, slug and gecko_id."""
        return await self._get(f"{API_BASE}/protocols")

    async def fees(self) -> dict[str, dict[str, Any]]:
        """Fee rows keyed by protocol **slug**.

        Not by gecko_id: the fees payload carries none — verified against the
        live API, 0 of 2,514 rows have one. The caller joins slug -> gecko_id
        through the protocols response.

        The two exclude parameters are not optional. Without them the response
        embeds full historical chart series and weighs 24.6 MB, which at a 600s
        cadence is 3.5 GB a day to read a handful of numbers. With them it is
        3.7 MB.
        """
        payload = await self._get(
            f"{API_BASE}/overview/fees"
            "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
        )
        rows = payload.get("protocols", []) if isinstance(payload, dict) else []
        return {row["slug"]: row for row in rows if row.get("slug")}

    async def emission_slugs(self) -> set[str]:
        """The protocol slugs that have a published unlock schedule (~4 KB)."""
        return set(await self._get(f"{DATASETS_BASE}/emissionsProtocolsList"))

    async def unlock(self, slug: str, coin_id: str) -> Unlock | None:
        """The pending unlock for a protocol, cached for a day.

        Returns None both when the schedule is known and empty and when the
        fetch failed. The caller distinguishes the two by whether the coin id is
        present in the map it builds — a failure must leave the key absent so the
        axis reports "unknown" rather than "nothing coming".
        """
        key = UNLOCK_KEY.format(coin_id=coin_id)
        cached = await self._cache.get_json(key)
        if cached is not None:
            return self._from_cache(cached)
        try:
            document = await self._get(
                f"{DATASETS_BASE}/emissions/{slug}", timeout=self._unlock_timeout
            )
        except Exception:
            logger.warning("unlock fetch failed for %s", slug, exc_info=True)
            return None
        unlock = next_unlock(document)
        await self._cache.set_json(
            key,
            {
                "at": unlock.at.isoformat() if unlock else None,
                "pct_supply": unlock.pct_supply if unlock else None,
            },
            ttl_seconds=UNLOCK_TTL_SECONDS,
        )
        return unlock

    @staticmethod
    def _from_cache(cached: dict[str, Any]) -> Unlock | None:
        at = cached.get("at")
        pct = cached.get("pct_supply")
        if not at or pct is None:
            return None
        return Unlock(at=datetime.fromisoformat(at), pct_supply=float(pct))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_defillama_client.py -q`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add services/collector-defillama tests/test_defillama_client.py
git commit -m "feat(defillama): HTTP client with a 24h unlock-extraction cache"
```

---

## Task 6: DefiLlama collector cycle and wiring

**Files:**
- Create: `services/collector-defillama/app/application/__init__.py` (empty)
- Create: `services/collector-defillama/app/application/collector.py`
- Create: `services/collector-defillama/app/main.py`
- Create: `services/collector-defillama/pyproject.toml`
- Test: `tests/test_defillama_collector.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_defillama_collector.py`:

```python
"""One DefiLlama polling cycle, including the unlock round-robin."""

from __future__ import annotations

from typing import Any

from service_modules import load_service_module

from cmi_common.kafka import Topic

collector_mod = load_service_module("collector-defillama", "application.collector")
unlocks = load_service_module("collector-defillama", "domain.unlocks")


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Any]] = []

    async def publish(self, topic: Topic, event: Any) -> None:
        self.published.append((topic, event))


class FakeClient:
    def __init__(self, slugs: set[str] | None = None) -> None:
        self.unlock_calls: list[str] = []
        self._slugs = slugs if slugs is not None else {"aave"}

    async def protocols(self) -> list[dict[str, Any]]:
        # Shaped like the live payload: deployment slugs under a parent, which
        # is what the emissions list is actually keyed by.
        return [
            {
                "slug": "aave-v3",
                "parentProtocol": "parent#aave",
                "gecko_id": "aave",
                "tvl": 3_000_000.0,
                "change_7d": 3.5,
            },
            {
                "slug": "aave-v2",
                "parentProtocol": "parent#aave",
                "gecko_id": "aave",
                "tvl": 2_000_000.0,
                "change_7d": 3.5,
            },
            {"slug": "uniswap", "gecko_id": "uniswap", "tvl": 4.0, "change_7d": 1.0},
        ]

    async def fees(self) -> dict[str, dict[str, Any]]:
        return {}

    async def emission_slugs(self) -> set[str]:
        return self._slugs

    async def unlock(self, slug: str, coin_id: str):
        self.unlock_calls.append(slug)
        return None


KNOWN = {"aave": "AAVE", "uniswap": "UNI"}


async def test_cycle_publishes_one_event_per_tracked_token() -> None:
    producer = FakeProducer()
    collector = collector_mod.DefiLlamaCollector(
        FakeClient(), producer, known_tokens=lambda: KNOWN
    )
    await collector.poll_once()
    assert {e.symbol for _, e in producer.published} == {"AAVE", "UNI"}
    assert all(topic is Topic.FUNDAMENTALS for topic, _ in producer.published)


async def test_only_protocols_with_a_schedule_are_ever_fetched() -> None:
    # ~320 of the 359 scheduled protocols are outside our universe, and the
    # other direction matters more: fetching a 2.25 MB document for a token with
    # no schedule at all would be pure waste.
    client = FakeClient(slugs={"aave"})
    collector = collector_mod.DefiLlamaCollector(
        client, FakeProducer(), known_tokens=lambda: KNOWN
    )
    await collector.poll_once()
    assert client.unlock_calls == ["aave"]


async def test_the_parent_slug_is_what_the_emissions_list_is_matched_on() -> None:
    # The failure this prevents: matching on the deployment slug finds nothing
    # for "aave" and the token silently reports no unlock schedule at all.
    # Measured on the live data, slug-matching loses 139 of 359 protocols.
    client = FakeClient(slugs={"aave"})
    producer = FakeProducer()
    collector = collector_mod.DefiLlamaCollector(
        client, producer, known_tokens=lambda: KNOWN
    )
    await collector.poll_once()
    aave = next(e for _, e in producer.published if e.symbol == "AAVE")
    assert aave.has_unlock_schedule is True


async def test_deployments_sharing_a_parent_are_fetched_once() -> None:
    # Aave is seven rows on the live API. One 2.25 MB document per deployment
    # would multiply the cost by the deployment count for no new information.
    client = FakeClient(slugs={"aave"})
    collector = collector_mod.DefiLlamaCollector(
        client, FakeProducer(), known_tokens=lambda: KNOWN
    )
    await collector.poll_once()
    assert client.unlock_calls == ["aave"]


async def test_the_round_robin_caps_fetches_per_cycle_and_advances() -> None:
    client = FakeClient(slugs={"aave", "uniswap"})
    collector = collector_mod.DefiLlamaCollector(
        client, FakeProducer(), known_tokens=lambda: KNOWN, max_unlock_fetches=1
    )
    await collector.poll_once()
    await collector.poll_once()
    # One per cycle, and the second cycle moves on rather than re-fetching.
    assert len(client.unlock_calls) == 2
    assert set(client.unlock_calls) == {"aave", "uniswap"}


async def test_a_token_whose_unlock_fetch_failed_reports_no_schedule() -> None:
    class FailingClient(FakeClient):
        async def unlock(self, slug: str, coin_id: str):
            raise RuntimeError("CDN down")

    producer = FakeProducer()
    collector = collector_mod.DefiLlamaCollector(
        FailingClient(), producer, known_tokens=lambda: KNOWN
    )
    await collector.poll_once()
    aave = next(e for _, e in producer.published if e.symbol == "AAVE")
    # Absent, not "nothing is coming" — the failure must not read as good news.
    assert aave.has_unlock_schedule is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_defillama_collector.py -q`
Expected: FAIL with `FileNotFoundError` on `application/collector.py`

- [ ] **Step 3: Write the collector**

Create empty `services/collector-defillama/app/application/__init__.py`, then `services/collector-defillama/app/application/collector.py`:

```python
"""One DefiLlama polling cycle."""

from __future__ import annotations

import logging
from collections.abc import Callable

from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_PRODUCED

from ..domain.mapper import emission_key, to_fundamentals_events
from ..domain.unlocks import Unlock
from ..infrastructure.llama_client import LlamaClient

logger = logging.getLogger(__name__)
SERVICE = "collector-defillama"

#: Each unlock document is ~2.25 MB. Three per cycle at a 600s cadence refreshes
#: a ~40-protocol universe in about two hours, well inside the 24h cache TTL,
#: while never pulling more than ~7 MB in one go.
DEFAULT_MAX_UNLOCK_FETCHES = 3


class DefiLlamaCollector:
    def __init__(
        self,
        client: LlamaClient,
        producer: EventProducer,
        *,
        known_tokens: Callable[[], dict[str, str]],
        max_unlock_fetches: int = DEFAULT_MAX_UNLOCK_FETCHES,
    ) -> None:
        self._client = client
        self._producer = producer
        self._known_tokens = known_tokens
        self._max_unlock_fetches = max_unlock_fetches
        #: Rotating cursor over the eligible slugs, so every protocol comes up
        #: in turn instead of the first few starving the rest.
        self._cursor = 0

    async def poll_once(self) -> int:
        known = self._known_tokens()
        protocols = await self._client.protocols()
        fees = await self._client.fees()
        unlocks = await self._collect_unlocks(protocols, known)

        events = to_fundamentals_events(
            protocols, fees=fees, unlocks=unlocks, known=known
        )
        for event in events:
            await self._producer.publish(Topic.FUNDAMENTALS, event)
            EVENTS_PRODUCED.labels(
                SERVICE, Topic.FUNDAMENTALS.value, event.event_type
            ).inc()
        logger.info(
            "defillama poll published %d events (%d unlocks known)",
            len(events),
            len(unlocks),
        )
        return len(events)

    async def _collect_unlocks(
        self, protocols: list[dict], known: dict[str, str]
    ) -> dict[str, Unlock | None]:
        """Unlock readings for the slice of the universe due this cycle.

        A coin id lands in the map only when its schedule was actually read —
        cached or freshly fetched. A failed fetch leaves the key out, so the
        event reports "no schedule known" rather than "no unlock coming".
        """
        scheduled = await self._client.emission_slugs()
        # emission_key, not row["slug"] — the emissions list is keyed by parent
        # slug. Several deployment rows collapse onto the same key (Aave is
        # seven), hence the dedupe: fetching a 2.25 MB document once per
        # deployment would multiply the cost by the deployment count.
        eligible = sorted(
            {
                (key, row["gecko_id"])
                for row in protocols
                if (key := emission_key(row)) in scheduled
                and row.get("gecko_id")
                and row["gecko_id"] in known
            }
        )
        if not eligible:
            return {}

        result: dict[str, Unlock | None] = {}
        for offset in range(min(self._max_unlock_fetches, len(eligible))):
            slug, coin_id = eligible[(self._cursor + offset) % len(eligible)]
            try:
                result[coin_id] = await self._client.unlock(slug, coin_id)
            except Exception:
                logger.warning("unlock lookup failed for %s", slug, exc_info=True)
        self._cursor = (self._cursor + self._max_unlock_fetches) % len(eligible)
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_defillama_collector.py -q`
Expected: PASS, 4 tests

- [ ] **Step 5: Write the entrypoint and packaging**

Create `services/collector-defillama/app/main.py`:

```python
"""collector-defillama service entrypoint (FastAPI + background poller)."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI
from sqlalchemy import select

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db.models import Token
from cmi_common.db.session import Database
from cmi_common.kafka import EventProducer
from cmi_common.runner import run_periodic

from .application.collector import DefiLlamaCollector
from .infrastructure.llama_client import LlamaClient

POLL_INTERVAL = float(os.getenv("DEFILLAMA_POLL_INTERVAL", "600"))
MAX_UNLOCK_FETCHES = int(os.getenv("DEFILLAMA_MAX_UNLOCK_FETCHES", "3"))
#: The coin_id -> symbol map changes at the pace of new listings, not of polls.
TOKEN_CACHE_TTL = 900.0


class _TokenMap:
    """Cached CoinGecko id -> symbol lookup, refreshed on a slow timer."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._value: dict[str, str] = {}
        self._loaded_at = 0.0

    async def refresh(self) -> None:
        loop = asyncio.get_running_loop()
        if self._value and loop.time() - self._loaded_at < TOKEN_CACHE_TTL:
            return
        async with self._db.sessionmaker() as session:
            rows = (await session.execute(select(Token.coin_id, Token.symbol))).all()
        self._value = {cid: sym for cid, sym in rows if cid and sym}
        self._loaded_at = loop.time()

    def get(self) -> dict[str, str]:
        return self._value


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    producer = EventProducer(settings.kafka)
    await producer.start()
    client = LlamaClient(cache)
    tokens = _TokenMap(db)
    collector = DefiLlamaCollector(
        client,
        producer,
        known_tokens=tokens.get,
        max_unlock_fetches=MAX_UNLOCK_FETCHES,
    )

    async def cycle() -> None:
        await tokens.refresh()
        await collector.poll_once()

    app.state.cache = cache
    app.state.db = db
    app.state.producer = producer
    app.state.client = client
    app.state.poller = asyncio.create_task(
        run_periodic(cycle, POLL_INTERVAL, name="defillama-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.client.close()
    await app.state.producer.stop()
    await app.state.db.dispose()
    await app.state.cache.close()


app = create_app("collector-defillama", on_startup=_startup, on_shutdown=_shutdown)
```

Create `services/collector-defillama/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "collector-defillama"
version = "0.1.0"
description = "DefiLlama collector — TVL, fees and token unlock schedules"
requires-python = ">=3.12"
dependencies = ["cmi-common", "httpx>=0.27", "sqlalchemy>=2.0", "asyncpg>=0.29"]

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

- [ ] **Step 6: Verify the module imports cleanly**

Run: `python -c "import sys; sys.path.insert(0, 'services/collector-defillama'); import app.main"`
Expected: no output, exit 0

- [ ] **Step 7: Commit**

```bash
git add services/collector-defillama tests/test_defillama_collector.py
git commit -m "feat(defillama): polling cycle, round-robin unlocks and service entrypoint"
```

---

## Task 7: Binance symbol and event mapping (pure)

**Files:**
- Create: `services/collector-binance-futures/app/__init__.py` (empty)
- Create: `services/collector-binance-futures/app/domain/__init__.py` (empty)
- Create: `services/collector-binance-futures/app/domain/symbols.py`
- Create: `services/collector-binance-futures/app/domain/mapper.py`
- Test: `tests/test_binance_futures_mapper.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_binance_futures_mapper.py`:

```python
"""Perp ticker resolution and DerivativesEvent construction."""

from __future__ import annotations

from decimal import Decimal

from service_modules import load_service_module

from cmi_common.events import DerivativesEvent

symbols = load_service_module("collector-binance-futures", "domain.symbols")
mapper = load_service_module("collector-binance-futures", "domain.mapper")


def test_usdt_perp_resolves_to_its_base_symbol() -> None:
    assert symbols.base_symbol("BTCUSDT") == "BTC"
    assert symbols.base_symbol("1000PEPEUSDT") == "1000PEPE"


def test_non_usdt_quotes_are_not_resolved() -> None:
    # Coin-margined and BUSD pairs price the same asset on a different unit;
    # mixing them into one funding reading would compare unlike things.
    assert symbols.base_symbol("BTCUSD_PERP") is None
    assert symbols.base_symbol("BTCBUSD") is None


def test_resolution_requires_the_symbol_to_be_one_we_price() -> None:
    assert symbols.resolve("BTCUSDT", priced={"BTC"}, ambiguous=set()) == "BTC"
    assert symbols.resolve("XYZUSDT", priced={"BTC"}, ambiguous=set()) is None


def test_an_ambiguous_ticker_is_skipped_not_arbitrated() -> None:
    # A ticker claimed by two coin ids cannot be attributed to either; recording
    # the ambiguity beats silently picking the higher-ranked one.
    assert symbols.resolve("SOLUSDT", priced={"SOL"}, ambiguous={"SOL"}) is None


def test_funding_row_becomes_an_event_with_the_annualised_rate() -> None:
    events = mapper.to_derivatives_events(
        [{"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}],
        priced={"BTC"},
        ambiguous=set(),
    )
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, DerivativesEvent)
    assert event.symbol == "BTC"
    assert event.funding_rate_8h == 0.0001
    # Three funding periods a day, 365 days: 0.0001 * 3 * 365 * 100 == 10.95%
    assert event.funding_annualized_pct == 10.95


def test_rows_we_cannot_resolve_are_dropped() -> None:
    assert (
        mapper.to_derivatives_events(
            [{"symbol": "GHOSTUSDT", "lastFundingRate": "0.0001"}],
            priced={"BTC"},
            ambiguous=set(),
        )
        == []
    )


def test_a_missing_funding_rate_yields_no_event_rather_than_a_zero() -> None:
    assert (
        mapper.to_derivatives_events(
            [{"symbol": "BTCUSDT"}], priced={"BTC"}, ambiguous=set()
        )
        == []
    )


def test_majors_detail_is_merged_onto_the_funding_event() -> None:
    event = mapper.with_majors_detail(
        mapper.to_derivatives_events(
            [{"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}],
            priced={"BTC"},
            ambiguous=set(),
        )[0],
        open_interest_usd=Decimal("1000000"),
        oi_change_pct_24h=5.0,
        long_short_ratio=1.8,
    )
    assert event.open_interest_usd == Decimal("1000000")
    assert event.open_interest_change_pct_24h == 5.0
    assert event.long_short_account_ratio == 1.8
    assert event.funding_rate_8h == 0.0001  # the broad-tier reading survives
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_binance_futures_mapper.py -q`
Expected: FAIL — the `collector-binance-futures` service directory does not exist yet

- [ ] **Step 3: Write the symbol resolver**

Create empty `services/collector-binance-futures/app/__init__.py` and `services/collector-binance-futures/app/domain/__init__.py`.

Create `services/collector-binance-futures/app/domain/symbols.py`:

```python
"""Resolve a Binance perp ticker to a symbol this platform prices.

Only USDT-margined perps are considered. Coin-margined contracts (`BTCUSD_PERP`)
quote in the asset itself and carry their own funding curve; folding them into
the same reading would average two different things.
"""

from __future__ import annotations

QUOTE = "USDT"


def base_symbol(ticker: str) -> str | None:
    """`BTCUSDT` -> `BTC`, or None for any other quote."""
    if "_" in ticker or not ticker.endswith(QUOTE):
        return None
    base = ticker[: -len(QUOTE)]
    return base or None


def resolve(ticker: str, *, priced: set[str], ambiguous: set[str]) -> str | None:
    """The symbol this perp belongs to, if we price it unambiguously."""
    base = base_symbol(ticker)
    if base is None or base not in priced or base in ambiguous:
        return None
    return base
```

- [ ] **Step 4: Write the event mapper**

Create `services/collector-binance-futures/app/domain/mapper.py`:

```python
"""Binance futures rows -> DerivativesEvent."""

from __future__ import annotations

from decimal import Decimal

from cmi_common.events import DerivativesEvent
from cmi_common.events.base import Source

from .symbols import resolve

#: Binance settles funding every 8 hours: three periods a day.
PERIODS_PER_YEAR = 3 * 365


def to_derivatives_events(
    rows: list[dict], *, priced: set[str], ambiguous: set[str]
) -> list[DerivativesEvent]:
    """One event per resolvable perp carrying a funding rate.

    A row without `lastFundingRate` produces nothing: an absent rate must reach
    the scorer as an absent axis, never as a neutral zero.
    """
    events: list[DerivativesEvent] = []
    for row in rows:
        symbol = resolve(
            str(row.get("symbol") or ""), priced=priced, ambiguous=ambiguous
        )
        if symbol is None:
            continue
        raw_rate = row.get("lastFundingRate")
        if raw_rate is None or raw_rate == "":
            continue
        rate = float(raw_rate)
        events.append(
            DerivativesEvent(
                source=Source.BINANCE_FUTURES,
                symbol=symbol,
                funding_rate_8h=rate,
                funding_annualized_pct=round(rate * PERIODS_PER_YEAR * 100, 4),
            )
        )
    return events


def with_majors_detail(
    event: DerivativesEvent,
    *,
    open_interest_usd: Decimal | None,
    oi_change_pct_24h: float | None,
    long_short_ratio: float | None,
) -> DerivativesEvent:
    """The same event with the per-symbol majors readings folded in.

    Events are frozen, so this returns a copy rather than mutating.
    """
    return event.model_copy(
        update={
            "open_interest_usd": open_interest_usd,
            "open_interest_change_pct_24h": oi_change_pct_24h,
            "long_short_account_ratio": long_short_ratio,
        }
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_binance_futures_mapper.py -q`
Expected: PASS, 8 tests

- [ ] **Step 6: Commit**

```bash
git add services/collector-binance-futures tests/test_binance_futures_mapper.py
git commit -m "feat(binance-futures): resolve perp tickers and build DerivativesEvent"
```

---

## Task 8: Binance client

**Files:**
- Create: `services/collector-binance-futures/app/infrastructure/__init__.py` (empty)
- Create: `services/collector-binance-futures/app/infrastructure/binance_client.py`
- Test: `tests/test_binance_futures_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_binance_futures_client.py`:

```python
"""Binance futures HTTP behaviour, without touching the network."""

from __future__ import annotations

import httpx
from service_modules import load_service_module

client_mod = load_service_module(
    "collector-binance-futures", "infrastructure.binance_client"
)


class FakeCache:
    async def allow(self, key: str, limit: int, window: int) -> bool:
        return True


def _client(handler):
    return client_mod.BinanceFuturesClient(
        FakeCache(), transport=httpx.MockTransport(handler)
    )


async def test_premium_index_returns_every_perp_in_one_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fapi/v1/premiumIndex"
        return httpx.Response(
            200,
            json=[
                {"symbol": "BTCUSDT", "lastFundingRate": "0.0001"},
                {"symbol": "ETHUSDT", "lastFundingRate": "-0.0002"},
            ],
        )

    rows = await _client(handler).premium_index()
    assert [r["symbol"] for r in rows] == ["BTCUSDT", "ETHUSDT"]


async def test_open_interest_history_yields_the_usd_level_and_the_24h_change() -> None:
    # One request gives both. The level comes from sumOpenInterestValue (USD),
    # the change from sumOpenInterest (base units) — the USD series moves with
    # price as well as with positioning, so it cannot answer "is conviction
    # entering the book".
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["period"] == "1h"
        assert request.url.params["limit"] == "25"
        return httpx.Response(
            200,
            json=[
                {
                    "sumOpenInterest": "100.0",
                    "sumOpenInterestValue": "1000000.0",
                    "timestamp": 1785445200000,
                },
                {
                    "sumOpenInterest": "110.0",
                    "sumOpenInterestValue": "1100000.0",
                    "timestamp": 1785531600000,
                },
            ],
        )

    reading = await _client(handler).open_interest("BTCUSDT")
    assert reading is not None
    assert reading.usd == 1100000.0
    assert reading.change_pct_24h == 10.0


async def test_a_single_open_interest_point_yields_no_change() -> None:
    # A level without a baseline is a level, not a trend. Reporting 0.0 would
    # claim flat positioning we never measured.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "sumOpenInterest": "100.0",
                    "sumOpenInterestValue": "1000000.0",
                    "timestamp": 1785445200000,
                }
            ],
        )

    reading = await _client(handler).open_interest("BTCUSDT")
    assert reading is not None
    assert reading.usd == 1000000.0
    assert reading.change_pct_24h is None


async def test_an_empty_open_interest_history_yields_no_reading() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    assert await _client(handler).open_interest("BTCUSDT") is None


async def test_long_short_ratio_reads_the_most_recent_bucket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"longShortRatio": "1.5", "timestamp": 1},
                {"longShortRatio": "1.9", "timestamp": 2},
            ],
        )

    assert await _client(handler).long_short_ratio("BTCUSDT") == 1.9


async def test_an_empty_ratio_response_yields_none_not_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    assert await _client(handler).long_short_ratio("BTCUSDT") is None


async def test_used_weight_header_is_recorded_for_self_throttling() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], headers={"X-MBX-USED-WEIGHT-1M": "1800"})

    client = _client(handler)
    await client.premium_index()
    assert client.used_weight == 1800


async def test_rate_limit_status_raises_a_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "12"})

    client = _client(handler)
    try:
        await client.premium_index()
    except client_mod.BinanceRateLimited as exc:
        assert exc.retry_after == 12.0
    else:
        raise AssertionError("expected BinanceRateLimited")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_binance_futures_client.py -q`
Expected: FAIL with `FileNotFoundError` on `infrastructure/binance_client.py`

- [ ] **Step 3: Write the client**

Create empty `services/collector-binance-futures/app/infrastructure/__init__.py`, then `services/collector-binance-futures/app/infrastructure/binance_client.py`:

```python
"""Public Binance USDT-M futures endpoints. No key, no signature.

Binance publishes the consumed request weight on every response. Reading it lets
the collector back off before the exchange starts answering 418, rather than
after.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.observability import UPSTREAM_REQUESTS

logger = logging.getLogger(__name__)

SERVICE = "collector-binance-futures"
BASE_URL = "https://fapi.binance.com"
#: Binance allows 2400 weight/minute; stay well under it.
WEIGHT_CEILING = 1800


class BinanceRateLimited(RuntimeError):
    def __init__(self, retry_after: float | None) -> None:
        super().__init__("binance rate limited")
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class OpenInterest:
    """USD notional now, and how it moved over 24h.

    `change_pct_24h` is None when the history held a single point: a level with
    no baseline is a level, not a trend.
    """

    usd: float
    change_pct_24h: float | None


class BinanceFuturesClient:
    def __init__(
        self,
        cache: Cache,
        *,
        timeout: float = 15.0,
        rate_limit_per_min: int = 240,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._cache = cache
        self._rate_limit = rate_limit_per_min
        self._client = httpx.AsyncClient(
            base_url=BASE_URL, timeout=timeout, transport=transport
        )
        #: Weight consumed in the current minute, as Binance last reported it.
        self.used_weight = 0

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def near_weight_ceiling(self) -> bool:
        return self.used_weight >= WEIGHT_CEILING

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not await self._cache.allow("binance-futures", self._rate_limit, 60):
            raise RuntimeError("Binance futures rate limit exceeded")
        resp = await self._client.get(path, params=params)
        weight = resp.headers.get("X-MBX-USED-WEIGHT-1M")
        if weight is not None:
            self.used_weight = int(weight)
        if resp.status_code in (418, 429):
            UPSTREAM_REQUESTS.labels(SERVICE, "binance-futures", "ratelimit").inc()
            retry_after = resp.headers.get("Retry-After")
            raise BinanceRateLimited(float(retry_after) if retry_after else None)
        try:
            resp.raise_for_status()
        except httpx.HTTPError:
            UPSTREAM_REQUESTS.labels(SERVICE, "binance-futures", "error").inc()
            raise
        UPSTREAM_REQUESTS.labels(SERVICE, "binance-futures", "ok").inc()
        return resp.json()

    async def premium_index(self) -> list[dict[str, Any]]:
        """Funding rate and mark price for every perp — one request."""
        payload = await self._get("/fapi/v1/premiumIndex")
        return payload if isinstance(payload, list) else [payload]

    async def open_interest(self, ticker: str) -> OpenInterest | None:
        """Current USD open interest and its 24h change, in one request.

        `/futures/data/openInterestHist` at `period=1h&limit=25` spans exactly
        24 hours and carries both the USD notional and the base-unit size, which
        `/fapi/v1/openInterest` does not: that endpoint returns a bare base-unit
        snapshot, leaving the change unmeasurable and the positioning axis's
        open-interest term permanently dead.

        The change is computed on base units on purpose. `sumOpenInterestValue`
        moves with price as well as with positioning, so a 10% USD rise during a
        10% rally is no new interest at all.
        """
        payload = await self._get(
            "/futures/data/openInterestHist",
            {"symbol": ticker, "period": "1h", "limit": 25},
        )
        if not payload:
            return None
        usd = float(payload[-1]["sumOpenInterestValue"])
        if len(payload) < 2:
            return OpenInterest(usd=usd, change_pct_24h=None)
        oldest = float(payload[0]["sumOpenInterest"])
        newest = float(payload[-1]["sumOpenInterest"])
        change = round(100.0 * (newest - oldest) / oldest, 4) if oldest > 0 else None
        return OpenInterest(usd=usd, change_pct_24h=change)

    async def long_short_ratio(self, ticker: str) -> float | None:
        """Most recent 5-minute retail long/short account ratio bucket."""
        payload = await self._get(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": ticker, "period": "5m", "limit": 1},
        )
        if not payload:
            return None
        return float(payload[-1]["longShortRatio"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_binance_futures_client.py -q`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add services/collector-binance-futures tests/test_binance_futures_client.py
git commit -m "feat(binance-futures): public futures client with weight self-throttling"
```

---

## Task 9: Binance two-tier collector and wiring

**Files:**
- Create: `services/collector-binance-futures/app/application/__init__.py` (empty)
- Create: `services/collector-binance-futures/app/application/collector.py`
- Create: `services/collector-binance-futures/app/main.py`
- Create: `services/collector-binance-futures/pyproject.toml`
- Test: `tests/test_binance_futures_collector.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_binance_futures_collector.py`:

```python
"""The two-tier Binance cycle: funding for all, detail for majors."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from service_modules import load_service_module

from cmi_common.kafka import Topic

collector_mod = load_service_module(
    "collector-binance-futures", "application.collector"
)
client_mod = load_service_module(
    "collector-binance-futures", "infrastructure.binance_client"
)


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Any]] = []

    async def publish(self, topic: Topic, event: Any) -> None:
        self.published.append((topic, event))


class FakeClient:
    near_weight_ceiling = False

    def __init__(self) -> None:
        self.oi_calls: list[str] = []
        self.ls_calls: list[str] = []

    async def premium_index(self) -> list[dict[str, Any]]:
        return [
            {"symbol": "BTCUSDT", "lastFundingRate": "0.0001"},
            {"symbol": "ETHUSDT", "lastFundingRate": "0.0002"},
            {"symbol": "DOGEUSDT", "lastFundingRate": "0.0003"},
        ]

    async def open_interest(self, ticker: str):
        self.oi_calls.append(ticker)
        return client_mod.OpenInterest(usd=1_000_000.0, change_pct_24h=7.5)

    async def long_short_ratio(self, ticker: str) -> float | None:
        self.ls_calls.append(ticker)
        return 1.8


def _collector(client, producer, majors=frozenset({"BTC"})):
    return collector_mod.BinanceFuturesCollector(
        client,
        producer,
        universe=lambda: (
            {"BTC", "ETH", "DOGE"},  # priced
            set(majors),  # majors
            set(),  # ambiguous
        ),
    )


async def test_every_priced_perp_gets_a_funding_event() -> None:
    producer = FakeProducer()
    await _collector(FakeClient(), producer).poll_once()
    assert {e.symbol for _, e in producer.published} == {"BTC", "ETH", "DOGE"}
    assert all(topic is Topic.DERIVATIVES for topic, _ in producer.published)


async def test_only_majors_incur_the_per_symbol_calls() -> None:
    client = FakeClient()
    await _collector(client, FakeProducer()).poll_once()
    assert client.oi_calls == ["BTCUSDT"]
    assert client.ls_calls == ["BTCUSDT"]


async def test_majors_carry_the_detail_and_others_do_not() -> None:
    producer = FakeProducer()
    await _collector(FakeClient(), producer).poll_once()
    events = {e.symbol: e for _, e in producer.published}
    assert events["BTC"].long_short_account_ratio == 1.8
    assert events["BTC"].open_interest_usd == Decimal("1000000.0")
    assert events["BTC"].open_interest_change_pct_24h == 7.5
    # A non-major is not a degraded reading; those fields were never measured.
    assert events["ETH"].long_short_account_ratio is None
    assert events["ETH"].open_interest_usd is None
    assert events["ETH"].open_interest_change_pct_24h is None
    assert events["ETH"].funding_rate_8h == 0.0002


async def test_a_failing_detail_call_still_publishes_the_funding_reading() -> None:
    class PartialClient(FakeClient):
        async def long_short_ratio(self, ticker: str) -> float | None:
            raise RuntimeError("endpoint flaky")

    producer = FakeProducer()
    await _collector(PartialClient(), producer).poll_once()
    btc = next(e for _, e in producer.published if e.symbol == "BTC")
    assert btc.funding_rate_8h == 0.0001
    assert btc.long_short_account_ratio is None


async def test_detail_tier_is_skipped_near_the_weight_ceiling() -> None:
    class HeavyClient(FakeClient):
        near_weight_ceiling = True

    client = HeavyClient()
    producer = FakeProducer()
    await _collector(client, producer).poll_once()
    assert client.oi_calls == []
    # The broad tier already happened, so funding still goes out.
    assert len(producer.published) == 3


async def test_an_empty_broad_tier_is_counted_for_the_outage_warning() -> None:
    class EmptyClient(FakeClient):
        async def premium_index(self) -> list[dict[str, Any]]:
            return []

    collector = _collector(EmptyClient(), FakeProducer())
    await collector.poll_once()
    await collector.poll_once()
    assert collector.empty_cycles == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_binance_futures_collector.py -q`
Expected: FAIL with `FileNotFoundError` on `application/collector.py`

- [ ] **Step 3: Write the collector**

Create empty `services/collector-binance-futures/app/application/__init__.py`, then `services/collector-binance-futures/app/application/collector.py`:

```python
"""One Binance futures cycle: funding for everything, detail for majors."""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal

from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_PRODUCED

from ..domain.mapper import to_derivatives_events, with_majors_detail
from ..infrastructure.binance_client import BinanceFuturesClient

logger = logging.getLogger(__name__)
SERVICE = "collector-binance-futures"

#: Binance geo-blocks some IP ranges. An empty broad tier for this many cycles
#: means the source is gone, not that the market went quiet — and renormalised
#: scoring would otherwise hide the outage perfectly.
EMPTY_CYCLES_BEFORE_WARNING = 3

Universe = Callable[[], tuple[set[str], set[str], set[str]]]


class BinanceFuturesCollector:
    def __init__(
        self,
        client: BinanceFuturesClient,
        producer: EventProducer,
        *,
        universe: Universe,
    ) -> None:
        self._client = client
        self._producer = producer
        self._universe = universe
        self.empty_cycles = 0

    async def poll_once(self) -> int:
        priced, majors, ambiguous = self._universe()
        rows = await self._client.premium_index()
        if not rows:
            self.empty_cycles += 1
            if self.empty_cycles >= EMPTY_CYCLES_BEFORE_WARNING:
                logger.warning(
                    "binance broad tier empty for %d cycles — geo-block or outage?",
                    self.empty_cycles,
                )
            return 0
        self.empty_cycles = 0

        events = to_derivatives_events(rows, priced=priced, ambiguous=ambiguous)
        published = 0
        for event in events:
            if event.symbol in majors and not self._client.near_weight_ceiling:
                event = await self._detail(event)
            await self._producer.publish(Topic.DERIVATIVES, event)
            EVENTS_PRODUCED.labels(
                SERVICE, Topic.DERIVATIVES.value, event.event_type
            ).inc()
            published += 1
        logger.info("binance futures poll published %d events", published)
        return published

    async def _detail(self, event):
        """Fold in the per-symbol readings, tolerating either one failing.

        A flaky detail endpoint must not cost us the funding rate we already
        have: the event goes out with whatever was measured and nothing else.
        """
        ticker = f"{event.symbol}USDT"
        reading = None
        ratio = None
        try:
            reading = await self._client.open_interest(ticker)
        except Exception:
            logger.warning("open interest failed for %s", ticker, exc_info=True)
        try:
            ratio = await self._client.long_short_ratio(ticker)
        except Exception:
            logger.warning("long/short failed for %s", ticker, exc_info=True)
        return with_majors_detail(
            event,
            open_interest_usd=Decimal(str(reading.usd)) if reading else None,
            oi_change_pct_24h=reading.change_pct_24h if reading else None,
            long_short_ratio=ratio,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_binance_futures_collector.py -q`
Expected: PASS, 6 tests

- [ ] **Step 5: Write the entrypoint and packaging**

Create `services/collector-binance-futures/app/main.py`:

```python
"""collector-binance-futures entrypoint (FastAPI + background poller)."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db.session import Database
from cmi_common.db.universe import (
    DEFAULT_MIN_MENTIONS,
    majors,
    mention_counts,
    priced_symbols,
)
from cmi_common.kafka import EventProducer
from cmi_common.runner import run_periodic

from .application.collector import BinanceFuturesCollector
from .infrastructure.binance_client import BinanceFuturesClient

POLL_INTERVAL = float(os.getenv("BINANCE_FUTURES_POLL_INTERVAL", "300"))
MIN_MENTIONS = int(
    os.getenv("KRAKEN_MAJOR_MIN_MENTIONS_7D", str(DEFAULT_MIN_MENTIONS))
)
#: The universe moves at the pace of listings and mention counts, not of polls.
UNIVERSE_TTL = 900.0


class _Universe:
    """Cached (priced, majors, ambiguous) triple, refreshed on a slow timer."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._value: tuple[set[str], set[str], set[str]] = (set(), set(), set())
        self._loaded_at = 0.0

    async def refresh(self) -> None:
        loop = asyncio.get_running_loop()
        if self._value[0] and loop.time() - self._loaded_at < UNIVERSE_TTL:
            return
        async with self._db.sessionmaker() as session:
            priced = await priced_symbols(session)
            mentions = await mention_counts(session)
        # Ambiguity is Kraken's venue-pair concern; nothing here can resolve a
        # ticker collision, so the set stays empty until a shared source exists.
        self._value = (
            priced,
            majors(priced, mentions, min_mentions=MIN_MENTIONS),
            set(),
        )
        self._loaded_at = loop.time()

    def get(self) -> tuple[set[str], set[str], set[str]]:
        return self._value


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    producer = EventProducer(settings.kafka)
    await producer.start()
    client = BinanceFuturesClient(cache)
    universe = _Universe(db)
    collector = BinanceFuturesCollector(client, producer, universe=universe.get)

    async def cycle() -> None:
        await universe.refresh()
        await collector.poll_once()

    app.state.cache = cache
    app.state.db = db
    app.state.producer = producer
    app.state.client = client
    app.state.poller = asyncio.create_task(
        run_periodic(cycle, POLL_INTERVAL, name="binance-futures-poll")
    )


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    app.state.poller.cancel()
    await asyncio.gather(app.state.poller, return_exceptions=True)
    await app.state.client.close()
    await app.state.producer.stop()
    await app.state.db.dispose()
    await app.state.cache.close()


app = create_app(
    "collector-binance-futures", on_startup=_startup, on_shutdown=_shutdown
)
```

Create `services/collector-binance-futures/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "collector-binance-futures"
version = "0.1.0"
description = "Binance futures collector — funding, open interest, long/short ratio"
requires-python = ">=3.12"
dependencies = ["cmi-common", "httpx>=0.27", "sqlalchemy>=2.0", "asyncpg>=0.29"]

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

- [ ] **Step 6: Verify the module imports cleanly**

Run: `python -c "import sys; sys.path.insert(0, 'services/collector-binance-futures'); import app.main"`
Expected: no output, exit 0

- [ ] **Step 7: Commit**

```bash
git add services/collector-binance-futures tests/test_binance_futures_collector.py
git commit -m "feat(binance-futures): two-tier polling cycle and service entrypoint"
```

---

## Task 10: Haiku ingestion of the two new topics

**Files:**
- Modify: `services/ai-worker-haiku/app/worker.py:17-24` (imports), `:140-199` (`_extract`)
- Modify: `services/ai-worker-haiku/app/main.py` (consumer topic list)
- Test: `tests/test_haiku_context_features.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_haiku_context_features.py`:

```python
"""Derivatives and fundamentals reach the per-symbol feature store."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from service_modules import load_service_module

from cmi_common.events import DerivativesEvent, FundamentalsEvent, PriceEvent
from cmi_common.events.base import Source

worker_mod = load_service_module("ai-worker-haiku", "worker")


class FakeStore:
    def __init__(self) -> None:
        self.state: dict[str, dict[str, Any]] = {}

    async def update(self, symbol: str, fields: dict[str, Any]) -> dict[str, Any]:
        current = self.state.setdefault(symbol, {})
        current.update({k: v for k, v in fields.items() if v is not None})
        return current

    async def get(self, symbol: str) -> dict[str, Any]:
        return self.state.get(symbol, {})


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, topic: Any, event: Any) -> None:
        self.published.append(event)


def _worker(store: FakeStore) -> Any:
    return worker_mod.HaikuWorker(store, FakeProducer())


async def test_derivatives_event_lands_in_the_feature_store() -> None:
    store = FakeStore()
    await _worker(store).handle(
        DerivativesEvent(
            source=Source.BINANCE_FUTURES,
            symbol="BTC",
            funding_rate_8h=0.0001,
            open_interest_usd=Decimal("1000000"),
            long_short_account_ratio=1.8,
        )
    )
    features = await store.get("BTC")
    assert features["funding_rate_8h"] == 0.0001
    assert features["open_interest_usd"] == 1000000.0
    assert features["long_short_account_ratio"] == 1.8


async def test_fundamentals_event_lands_in_the_feature_store() -> None:
    store = FakeStore()
    await _worker(store).handle(
        FundamentalsEvent(
            source=Source.DEFILLAMA,
            symbol="AAVE",
            coin_id="aave",
            tvl_change_pct_7d=3.5,
            next_unlock_at=datetime(2026, 8, 10, tzinfo=UTC),
            next_unlock_pct_supply=2.5,
            has_unlock_schedule=True,
        )
    )
    features = await store.get("AAVE")
    assert features["tvl_change_pct_7d"] == 3.5
    assert features["next_unlock_pct_supply"] == 2.5
    assert features["has_unlock_schedule"] is True
    assert features["next_unlock_at"] == "2026-08-10T00:00:00+00:00"


async def test_context_events_alone_do_not_make_a_symbol_ready() -> None:
    # Funding is context for a signal, not a signal. Scoring a symbol we have no
    # price for would invent an opportunity out of an exchange statistic.
    store = FakeStore()
    worker = _worker(store)
    await worker.handle(
        DerivativesEvent(
            source=Source.BINANCE_FUTURES, symbol="BTC", funding_rate_8h=0.0001
        )
    )
    assert worker_mod.HaikuWorker._ready(await store.get("BTC")) is False


async def test_a_partial_derivatives_event_does_not_erase_known_fields() -> None:
    # The broad tier republishes funding alone every cycle; if that wiped the
    # majors detail, the axis would oscillate between rich and thin readings.
    store = FakeStore()
    worker = _worker(store)
    await worker.handle(
        DerivativesEvent(
            source=Source.BINANCE_FUTURES,
            symbol="BTC",
            funding_rate_8h=0.0001,
            long_short_account_ratio=1.8,
        )
    )
    await worker.handle(
        DerivativesEvent(
            source=Source.BINANCE_FUTURES, symbol="BTC", funding_rate_8h=0.0005
        )
    )
    features = await store.get("BTC")
    assert features["funding_rate_8h"] == 0.0005
    assert features["long_short_account_ratio"] == 1.8


async def test_price_events_still_work() -> None:
    store = FakeStore()
    await _worker(store).handle(
        PriceEvent(
            source=Source.COINGECKO,
            symbol="BTC",
            coin_id="bitcoin",
            price_usd=Decimal("60000"),
            price_change_pct_24h=5.0,
        )
    )
    assert (await store.get("BTC"))["price_change_pct_24h"] == 5.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_haiku_context_features.py -q`
Expected: FAIL — `test_derivatives_event_lands_in_the_feature_store` gets an empty dict, because `_extract` returns `(None, {}, "")` for unknown events

- [ ] **Step 3: Extend `_extract`**

In `services/ai-worker-haiku/app/worker.py`, add to the import block from `cmi_common.events`:

```python
    DerivativesEvent,
    FundamentalsEvent,
```

(keep the list alphabetical: `AnalysisEvent, BaseEvent, DerivativesEvent, DexEvent, FundamentalsEvent, PriceEvent, SentimentEvent, VolumeEvent`)

Insert these two branches in `_extract`, immediately before the final `return None, {}, ""`:

```python
        if isinstance(event, DerivativesEvent):
            return (
                event.symbol,
                {
                    "funding_rate_8h": event.funding_rate_8h,
                    "funding_annualized_pct": event.funding_annualized_pct,
                    "open_interest_usd": (
                        float(event.open_interest_usd)
                        if event.open_interest_usd is not None
                        else None
                    ),
                    "open_interest_change_pct_24h": event.open_interest_change_pct_24h,
                    "long_short_account_ratio": event.long_short_account_ratio,
                },
                Topic.DERIVATIVES.value,
            )
        if isinstance(event, FundamentalsEvent):
            # has_unlock_schedule is a bool and False is meaningful, but the
            # store drops None and keeps False, so it survives correctly.
            return (
                event.symbol,
                {
                    "tvl_usd": (
                        float(event.tvl_usd) if event.tvl_usd is not None else None
                    ),
                    "tvl_change_pct_7d": event.tvl_change_pct_7d,
                    "fees_change_pct_7d": event.fees_change_pct_7d,
                    "next_unlock_at": (
                        event.next_unlock_at.isoformat()
                        if event.next_unlock_at
                        else None
                    ),
                    "next_unlock_pct_supply": event.next_unlock_pct_supply,
                    "has_unlock_schedule": event.has_unlock_schedule,
                },
                Topic.FUNDAMENTALS.value,
            )
```

Leave `_ready()` untouched: these are context signals, not triggers.

- [ ] **Step 4: Subscribe to the new topics**

In `services/ai-worker-haiku/app/main.py`, find the `EventConsumer(...)` topic list and add `Topic.DERIVATIVES` and `Topic.FUNDAMENTALS` to it.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_haiku_context_features.py tests/test_analysis_settling.py -q`
Expected: PASS, no regression in the settling tests

- [ ] **Step 6: Commit**

```bash
git add services/ai-worker-haiku tests/test_haiku_context_features.py
git commit -m "feat(haiku): fold derivatives and fundamentals into the feature store"
```

---

## Task 11: The two new scoring axes (pure normalisations)

**Files:**
- Modify: `services/decision-engine/app/scoring.py`
- Test: `tests/test_scoring_new_axes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scoring_new_axes.py`:

```python
"""Normalisation of the positioning and fundamentals axes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "de_scoring_axes",
    Path(__file__).resolve().parents[1]
    / "services"
    / "decision-engine"
    / "app"
    / "scoring.py",
)
scoring = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = scoring
_spec.loader.exec_module(scoring)


# --- positioning -----------------------------------------------------------


def test_positive_funding_lowers_positioning() -> None:
    # THE sign test. Positive funding means longs are paying shorts, i.e. the
    # crowded side. Getting this backwards yields a model that is confidently
    # wrong rather than obviously broken, so it is asserted explicitly.
    crowded = scoring._norm_positioning(funding=0.001, ratio=None, oi_change=None)
    neutral = scoring._norm_positioning(funding=0.0, ratio=None, oi_change=None)
    squeezed = scoring._norm_positioning(funding=-0.001, ratio=None, oi_change=None)
    assert crowded < neutral < squeezed
    assert abs(neutral - 0.5) < 1e-9


def test_a_crowded_long_ratio_lowers_positioning() -> None:
    crowded = scoring._norm_positioning(funding=None, ratio=2.0, oi_change=None)
    balanced = scoring._norm_positioning(funding=None, ratio=1.0, oi_change=None)
    assert crowded < balanced
    assert abs(balanced - 0.5) < 1e-9


def test_rising_open_interest_raises_positioning() -> None:
    rising = scoring._norm_positioning(funding=None, ratio=None, oi_change=20.0)
    flat = scoring._norm_positioning(funding=None, ratio=None, oi_change=0.0)
    assert rising > flat


def test_positioning_is_the_mean_of_present_terms() -> None:
    # Funding alone (broad tier) must give a usable reading; a missing ratio is
    # not a zero term dragging the axis down.
    funding_only = scoring._norm_positioning(funding=-0.001, ratio=None, oi_change=None)
    assert funding_only > 0.9


def test_positioning_absent_when_nothing_is_known() -> None:
    assert scoring._norm_positioning(funding=None, ratio=None, oi_change=None) is None


# --- fundamentals ----------------------------------------------------------


def test_rising_tvl_raises_fundamentals() -> None:
    rising = scoring._norm_fundamentals(
        tvl_change=15.0, fees_change=None, unlock_pct=None, unlock_days=None,
        has_schedule=False,
    )
    flat = scoring._norm_fundamentals(
        tvl_change=0.0, fees_change=None, unlock_pct=None, unlock_days=None,
        has_schedule=False,
    )
    assert rising > flat


def test_an_imminent_large_unlock_crushes_fundamentals() -> None:
    imminent = scoring._norm_fundamentals(
        tvl_change=None, fees_change=None, unlock_pct=5.0, unlock_days=3.0,
        has_schedule=True,
    )
    assert imminent is not None
    assert imminent < 0.15


def test_a_distant_unlock_is_harmless() -> None:
    distant = scoring._norm_fundamentals(
        tvl_change=None, fees_change=None, unlock_pct=5.0, unlock_days=30.0,
        has_schedule=True,
    )
    assert distant == 1.0


def test_a_known_empty_schedule_is_the_best_reading() -> None:
    clean = scoring._norm_fundamentals(
        tvl_change=None, fees_change=None, unlock_pct=None, unlock_days=None,
        has_schedule=True,
    )
    assert clean == 1.0


def test_an_untracked_token_gets_no_unlock_term() -> None:
    # Absent from DefiLlama's schedule list is not a clean bill of health; with
    # no other input the axis must be absent, not 1.0.
    assert (
        scoring._norm_fundamentals(
            tvl_change=None, fees_change=None, unlock_pct=None, unlock_days=None,
            has_schedule=False,
        )
        is None
    )


def test_unlock_severity_scales_with_size() -> None:
    small = scoring._norm_fundamentals(
        tvl_change=None, fees_change=None, unlock_pct=1.0, unlock_days=0.0,
        has_schedule=True,
    )
    large = scoring._norm_fundamentals(
        tvl_change=None, fees_change=None, unlock_pct=5.0, unlock_days=0.0,
        has_schedule=True,
    )
    assert large < small < 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scoring_new_axes.py -q`
Expected: FAIL with `AttributeError: module has no attribute '_norm_positioning'`

- [ ] **Step 3: Add the normalisations**

In `services/decision-engine/app/scoring.py`, add `import math` is already present; append these functions after `_norm_liquidity`:

```python
#: Funding rate at which the crowding read saturates, measured rather than
#: guessed: across all 854 Binance perps on 2026-07-31 the 5th percentile sits
#: at -0.000156 and the 95th at +0.000159, with a median of +0.000050. An
#: earlier draft used 0.0004 and spanned only 0.19 between those percentiles —
#: an axis that varies by a fifth of its range over 90% of the book is not
#: discriminating, it is decoration. This scale spans 0.66.
#:
#: Note the median lands at 0.378, below neutral: positive funding is the
#: normal state of crypto perps, so the typical symbol reads mildly crowded.
#: That is a property of the market, not a bias to correct out.
_FUNDING_SCALE = 0.0001
#: An unlock of this share of supply is treated as maximally severe.
_UNLOCK_FULL_SEVERITY_PCT = 5.0
#: Unlocks further out than this do not bear on a position opened today.
_UNLOCK_HORIZON_DAYS = 30.0


def _mean_present(terms: list[float | None]) -> float | None:
    """Average of the terms that exist, or None when none do.

    Averaging over present terms rather than over all of them is what lets an
    axis be partially observed: funding alone is a real reading, not a reading
    dragged toward zero by the two calls we did not make.
    """
    values = [t for t in terms if t is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _norm_positioning(
    *, funding: float | None, ratio: float | None, oi_change: float | None
) -> float | None:
    """How favourably the derivatives crowd is positioned, in [0, 1].

    Contrarian on crowding, confirmatory on engagement. Positive funding means
    longs are paying shorts — the crowded side — so it *lowers* the score; a
    long/short account ratio above 1 does the same. Rising open interest raises
    it: conviction is entering the book.
    """
    return _mean_present(
        [
            None if funding is None else _sigmoid(-funding / _FUNDING_SCALE),
            None if not ratio or ratio <= 0 else _sigmoid(-math.log(ratio), k=1.5),
            None if oi_change is None else _sigmoid(oi_change / 20.0),
        ]
    )


def _norm_fundamentals(
    *,
    tvl_change: float | None,
    fees_change: float | None,
    unlock_pct: float | None,
    unlock_days: float | None,
    has_schedule: bool,
) -> float | None:
    """Protocol health net of scheduled dilution, in [0, 1].

    The unlock term exists only when a schedule is actually known. A token
    DefiLlama does not track contributes nothing here: silence is not a clean
    bill of health, and treating it as 1.0 would reward being unmeasured.
    """
    unlock_term: float | None = None
    if has_schedule:
        if unlock_pct is None or unlock_days is None:
            # Schedule read, nothing pending: a measurement, and a good one.
            unlock_term = 1.0
        else:
            severity = max(0.0, min(1.0, unlock_pct / _UNLOCK_FULL_SEVERITY_PCT))
            proximity = max(0.0, min(1.0, 1.0 - unlock_days / _UNLOCK_HORIZON_DAYS))
            unlock_term = 1.0 - severity * proximity
    return _mean_present(
        [
            None if tvl_change is None else _sigmoid(tvl_change / 15.0),
            None if fees_change is None else _sigmoid(fees_change / 25.0),
            unlock_term,
        ]
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scoring_new_axes.py -q`
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add services/decision-engine/app/scoring.py tests/test_scoring_new_axes.py
git commit -m "feat(scoring): add the positioning and fundamentals normalisations"
```

---

## Task 12: Renormalisation, new weights, and the absent-axis change

This is the task that changes existing behaviour. Three assertions in `tests/test_scoring.py` become false **by design** and are rewritten, not patched until green.

**Files:**
- Modify: `services/decision-engine/app/scoring.py`
- Modify: `tests/test_scoring.py`
- Test: `tests/test_scoring_renormalisation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scoring_renormalisation.py`:

```python
"""Absent axes leave the score; they no longer sink it."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "de_scoring_renorm",
    Path(__file__).resolve().parents[1]
    / "services"
    / "decision-engine"
    / "app"
    / "scoring.py",
)
scoring = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = scoring
_spec.loader.exec_module(scoring)


LEGACY_WEIGHTS = {
    "volume_growth": 0.25,
    "social_score": 0.20,
    "news_score": 0.20,
    "market_trend": 0.20,
    "liquidity_score": 0.15,
}


def test_weights_sum_to_one() -> None:
    assert abs(sum(scoring.WEIGHTS.values()) - 1.0) < 1e-9


def test_legacy_axes_keep_their_relative_proportions() -> None:
    # The rescale expresses no new opinion about the old model; it only makes
    # room. Any drift here is a silent recalibration.
    for key, legacy in LEGACY_WEIGHTS.items():
        assert abs(scoring.WEIGHTS[key] - legacy * 0.75) < 1e-9


def test_new_axes_carry_the_freed_weight() -> None:
    assert abs(scoring.WEIGHTS["positioning"] - 0.15) < 1e-9
    assert abs(scoring.WEIGHTS["fundamentals"] - 0.10) < 1e-9


def test_a_symbol_missing_axes_is_scored_on_what_is_known() -> None:
    # Every present axis at 0.8. Under the old constant denominator this landed
    # at 60 (0.8 * 0.75) and failed a threshold of 70 while being uniformly
    # strong. That is the deadlock this change removes.
    f = scoring.Features(
        price_change_pct_24h=1e6,  # saturates _norm_trend to ~1.0
        volume_spike_ratio=1e6,
        liquidity_usd=1e12,
        sentiment_score=1.0,
        social_growth=1e6,
        news_impact=1.0,
    )
    result = scoring.score(f)
    assert result.opportunity_score == 100
    assert result.confidence == 0.75  # five of seven axes present


def test_migration_identity_holds_on_unrounded_values() -> None:
    """score_v2 == score_v1 / confidence_v1 for legacy-only features.

    The threshold chosen at deploy time is derived from exactly this relation,
    so it is pinned here. Asserted unrounded: decision_journal.score is stored
    as an integer, which costs up to a point.
    """
    f = scoring.Features(
        price_change_pct_24h=12.0,
        volume_spike_ratio=4.0,
        liquidity_usd=500_000.0,
        sentiment_score=0.4,
        social_growth=0.8,
        news_impact=1.0,
    )
    result = scoring.score(f)

    # Recompute the v1 weighted sum from the same breakdown and legacy weights.
    v1_sum = sum(result.breakdown[k] * LEGACY_WEIGHTS[k] for k in LEGACY_WEIGHTS)
    v1_confidence = sum(LEGACY_WEIGHTS.values())  # all five present here
    expected = 100.0 * v1_sum / v1_confidence
    assert abs(result.opportunity_score - expected) <= 1.0


def test_an_unknown_liquidity_leaves_the_score_but_a_measured_zero_does_not() -> None:
    # The unknown-vs-zero rule, at the axis level.
    assert scoring._norm_liquidity(None) is None
    assert scoring._norm_liquidity(0) == 0.0


def test_a_symbol_with_no_evidence_at_all_produces_no_score() -> None:
    result = scoring.score(scoring.Features())
    assert result.confidence == 0.0
    assert result.opportunity_score == 0


def test_positioning_participates_in_the_score() -> None:
    without = scoring.score(scoring.Features(price_change_pct_24h=10.0))
    with_crowded = scoring.score(
        scoring.Features(price_change_pct_24h=10.0, funding_rate_8h=0.001)
    )
    assert with_crowded.opportunity_score < without.opportunity_score
    assert with_crowded.confidence > without.confidence


def test_an_imminent_unlock_lowers_the_score() -> None:
    clean = scoring.score(
        scoring.Features(
            price_change_pct_24h=10.0, has_unlock_schedule=True
        )
    )
    diluting = scoring.score(
        scoring.Features(
            price_change_pct_24h=10.0,
            has_unlock_schedule=True,
            next_unlock_pct_supply=5.0,
            next_unlock_days=2.0,
        )
    )
    assert diluting.opportunity_score < clean.opportunity_score
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scoring_renormalisation.py -q`
Expected: FAIL — `WEIGHTS` has no `positioning` key, `Features` has no `funding_rate_8h`

- [ ] **Step 3: Rewrite the aggregation**

In `services/decision-engine/app/scoring.py`:

Replace `WEIGHTS` with:

```python
WEIGHTS = {
    "volume_growth": 0.1875,
    "social_score": 0.1500,
    "news_score": 0.1500,
    "market_trend": 0.1500,
    "liquidity_score": 0.1125,
    "positioning": 0.1500,
    "fundamentals": 0.1000,
}
```

Add to `Features` (keeping the existing fields):

```python
    #: Binance perp positioning. funding is the raw 8h fraction, not a percent.
    funding_rate_8h: float | None = None
    long_short_account_ratio: float | None = None
    open_interest_change_pct_24h: float | None = None
    #: DefiLlama fundamentals.
    tvl_change_pct_7d: float | None = None
    fees_change_pct_7d: float | None = None
    next_unlock_pct_supply: float | None = None
    next_unlock_days: float | None = None
    has_unlock_schedule: bool = False
```

Change every `_norm_*` to return `None` for absent input:

```python
def _norm_volume(ratio: float | None) -> float | None:
    if ratio is None:
        return None
    return max(0.0, min(1.0, _sigmoid(ratio - 3, k=0.7)))


def _norm_social(growth: float | None) -> float | None:
    if growth is None:
        return None
    return max(0.0, min(1.0, _sigmoid(growth, k=1.5)))


def _norm_trend(change_24h: float | None) -> float | None:
    if change_24h is None:
        return None
    return max(0.0, min(1.0, _sigmoid(change_24h / 15.0)))


def _norm_liquidity(liq: float | None) -> float | None:
    # A measured zero is the worst score; an unknown is no score at all. The
    # two used to collapse onto 0.0, which is what made "we did not collect it"
    # indistinguishable from "there is none".
    if liq is None:
        return None
    if liq <= 0:
        return 0.0
    return max(0.0, min(1.0, (math.log10(liq) - 3) / 4))
```

And `_norm_news` — keep the existing behaviour, but return `None` when nothing at all is known:

```python
def _norm_news(
    impact: float | None,
    sentiment: float | None,
    market_sentiment: float | None = None,
) -> float | None:
    # Absence of news is neutral, not bearish — the original fix. Under
    # renormalisation, excluding the axis expresses that better than a 0.5 that
    # actively drags a strong symbol toward the middle.
    if impact is None and sentiment is None and market_sentiment is None:
        return None
    base = impact if impact is not None else 0.0
    if sentiment is not None:
        raw = sentiment
    elif market_sentiment is not None:
        raw = market_sentiment * _MARKET_DAMPING
    else:
        raw = 0.0
    return max(0.0, min(1.0, 0.5 * base + 0.5 * ((raw + 1) / 2)))
```

Replace `score()` and delete `_signal_present` entirely — presence is now read off the
normalised values rather than re-derived from the raw fields, so the two can no longer
disagree:

```python
def score(features: Features) -> ScoreResult:
    sub: dict[str, float | None] = {
        "volume_growth": _norm_volume(features.volume_spike_ratio),
        "social_score": _norm_social(features.social_growth),
        "news_score": _norm_news(
            features.news_impact,
            features.sentiment_score,
            features.market_sentiment,
        ),
        "market_trend": _norm_trend(features.price_change_pct_24h),
        "liquidity_score": _norm_liquidity(features.liquidity_usd),
        "positioning": _norm_positioning(
            funding=features.funding_rate_8h,
            ratio=features.long_short_account_ratio,
            oi_change=features.open_interest_change_pct_24h,
        ),
        "fundamentals": _norm_fundamentals(
            tvl_change=features.tvl_change_pct_7d,
            fees_change=features.fees_change_pct_7d,
            unlock_pct=features.next_unlock_pct_supply,
            unlock_days=features.next_unlock_days,
            has_schedule=features.has_unlock_schedule,
        ),
    }
    present = {k: v for k, v in sub.items() if v is not None}
    breakdown = dict(present)
    present_weight = sum(WEIGHTS[k] for k in present)
    if present_weight <= 0:
        # No evidence at all. A confidence of 0 is what says so; a score would
        # only invent a number to sit beside it.
        return ScoreResult(0, 0.0, {})
    weighted = sum(present[k] * WEIGHTS[k] for k in present)
    opportunity = int(round(100 * weighted / present_weight))
    # confidence keeps its meaning: the share of model weight backed by real
    # evidence. market_sentiment stays out of it — a market-wide read is the
    # same number for every symbol, so counting it would lift the whole book.
    return ScoreResult(opportunity, round(present_weight, 3), breakdown)
```

Update the module docstring's model block to list all seven axes and state that the weighted sum is divided by the present weight.

- [ ] **Step 4: Rewrite the three now-false assertions in `tests/test_scoring.py`**

These encoded the old behaviour and must state the new one deliberately.

Replace `test_empty_features_score_near_zero_with_no_confidence` with:

```python
def test_empty_features_produce_no_score_at_all() -> None:
    # Was: score 5, from a neutral news axis applied to a symbol we know nothing
    # about. Under renormalisation an axis with no input is excluded rather than
    # valued, so an empty symbol has no denominator and therefore no score. The
    # confidence of 0.0 is what carries the emptiness.
    result = scoring.score(scoring.Features())
    assert result.opportunity_score == 0
    assert result.confidence == 0.0
```

Replace `test_no_news_now_outscores_maximally_bearish_news` with:

```python
def test_bearish_news_scores_below_neutral_news() -> None:
    # The original conflation — silence scoring like panic — is now prevented by
    # exclusion rather than by a neutral constant, so the comparison is between
    # two symbols that both have a news reading.
    neutral = scoring.score(scoring.Features(sentiment_score=0.0))
    panicking = scoring.score(scoring.Features(sentiment_score=-1.0))
    assert panicking.opportunity_score < neutral.opportunity_score
```

In `test_strong_signals_score_high`, change the confidence assertion:

```python
    assert result.opportunity_score > 60
    # Five of seven axes: the derivatives and fundamentals axes are absent for a
    # symbol with no perp and no protocol, which is most of them.
    assert result.confidence == 0.75
```

Leave `test_confidence_reflects_missing_signals` and `test_weights_sum_to_one` as they are — both still hold.

- [ ] **Step 5: Run the full scoring suite**

Run: `pytest tests/test_scoring.py tests/test_scoring_new_axes.py tests/test_scoring_renormalisation.py -q`
Expected: PASS, all tests

- [ ] **Step 6: Commit**

```bash
git add services/decision-engine/app/scoring.py tests/test_scoring.py tests/test_scoring_renormalisation.py
git commit -m "feat(scoring): renormalise over present axes and add the two new ones"
```

---

## Task 13: Feed the new features through the decision engine

`scoring.Features` now has seven axes' worth of inputs, but `engine.py` still builds it from five.

**Files:**
- Modify: `services/decision-engine/app/engine.py:98-110`
- Test: `tests/test_decision_engine_context_features.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_decision_engine_context_features.py`:

```python
"""The engine reads the new features out of AnalysisEvent.meta."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from service_modules import load_service_module

from cmi_common.events import AnalysisEvent
from cmi_common.events.base import Source
from cmi_common.kafka import Topic

engine_mod = load_service_module("decision-engine", "engine")


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Any]] = []

    async def publish(self, topic: Topic, event: Any) -> None:
        self.published.append((topic, event))


def _analysis(features: dict[str, Any]) -> AnalysisEvent:
    return AnalysisEvent(
        source=Source.AI_HAIKU,
        symbol="BTC",
        opportunity_score=80,
        confidence=0.9,
        reason="test",
        summary="",
        price_change_pct_24h=10.0,
        meta={"features": features},
    )


async def test_funding_reaches_the_scored_features() -> None:
    producer = FakeProducer()
    engine = engine_mod.DecisionEngine(producer, decision_threshold=0)
    await engine.handle(_analysis({"funding_rate_8h": 0.001}))
    _, event = producer.published[0]
    assert "positioning" in event.meta["breakdown"]


async def test_unlock_proximity_is_derived_from_the_timestamp() -> None:
    # The store carries an ISO date; the scorer wants days remaining. Deriving
    # it at read time keeps the stored value absolute and the score current.
    producer = FakeProducer()
    engine = engine_mod.DecisionEngine(producer, decision_threshold=0)
    due = datetime.now(tz=UTC) + timedelta(days=3)
    await engine.handle(
        _analysis(
            {
                "has_unlock_schedule": True,
                "next_unlock_at": due.isoformat(),
                "next_unlock_pct_supply": 5.0,
            }
        )
    )
    _, event = producer.published[0]
    assert event.meta["breakdown"]["fundamentals"] < 0.2


async def test_a_malformed_unlock_date_is_ignored_rather_than_fatal() -> None:
    producer = FakeProducer()
    engine = engine_mod.DecisionEngine(producer, decision_threshold=0)
    await engine.handle(
        _analysis(
            {
                "has_unlock_schedule": True,
                "next_unlock_at": "not-a-date",
                "next_unlock_pct_supply": 5.0,
            }
        )
    )
    # Known schedule, unreadable date: falls back to the clean reading rather
    # than crashing the consumer loop on one bad field.
    _, event = producer.published[0]
    assert event.meta["breakdown"]["fundamentals"] == 1.0


async def test_absent_context_features_leave_the_axes_out() -> None:
    producer = FakeProducer()
    engine = engine_mod.DecisionEngine(producer, decision_threshold=0)
    await engine.handle(_analysis({}))
    _, event = producer.published[0]
    assert "positioning" not in event.meta["breakdown"]
    assert "fundamentals" not in event.meta["breakdown"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_decision_engine_context_features.py -q`
Expected: FAIL — `positioning` is absent from the breakdown because `Features` is never populated

- [ ] **Step 3: Populate the new features**

In `services/decision-engine/app/engine.py`, add `from datetime import UTC, datetime` to the imports and this helper next to `_liquidity`:

```python
def _unlock_days(raw: dict) -> float | None:
    """Days until the next unlock, from the absolute date the store carries.

    Stored absolute and converted at read time: a stored "days remaining" would
    silently age between the collector's poll and the decision.
    """
    value = raw.get("next_unlock_at")
    if not value:
        return None
    try:
        at = datetime.fromisoformat(str(value))
    except ValueError:
        # One unparseable field must not kill the consumer loop. The schedule
        # flag still stands, so the axis degrades to "nothing pending" rather
        # than to a fabricated urgency.
        logger.warning("unparseable next_unlock_at: %r", value)
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return max(0.0, (at - datetime.now(tz=UTC)).total_seconds() / 86400.0)
```

Then extend the `Features(...)` construction in `_on_analysis`:

```python
        features = Features(
            price_change_pct_24h=event.price_change_pct_24h,
            volume_spike_ratio=event.volume_spike_ratio,
            liquidity_usd=_liquidity(raw),
            sentiment_score=event.sentiment_score,
            social_growth=event.social_growth,
            news_impact=1.0 if raw.get("has_news") else None,
            market_sentiment=self._market_sentiment(),
            funding_rate_8h=raw.get("funding_rate_8h"),
            long_short_account_ratio=raw.get("long_short_account_ratio"),
            open_interest_change_pct_24h=raw.get("open_interest_change_pct_24h"),
            tvl_change_pct_7d=raw.get("tvl_change_pct_7d"),
            fees_change_pct_7d=raw.get("fees_change_pct_7d"),
            next_unlock_pct_supply=raw.get("next_unlock_pct_supply"),
            next_unlock_days=_unlock_days(raw),
            has_unlock_schedule=bool(raw.get("has_unlock_schedule")),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_decision_engine_context_features.py tests/test_decision_engine_rejection.py -q`
Expected: PASS, no regression

- [ ] **Step 5: Commit**

```bash
git add services/decision-engine tests/test_decision_engine_context_features.py
git commit -m "feat(decision-engine): score the derivatives and fundamentals features"
```

---

## Task 14: Deployment wiring

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.vps.yml`
- Modify: `.github/workflows/deploy.yml`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add both services to `docker-compose.yml`**

Insert after the `collector-kraken` block (which ends at line 243), following its exact shape:

```yaml
  collector-defillama:
    <<: *service-defaults
    build: { context: ., dockerfile: docker/Dockerfile, args: { SERVICE_PATH: services/collector-defillama } }
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
      kafka:
        condition: service_healthy
    environment:
      <<: *common-env
      DEFILLAMA_POLL_INTERVAL: ${DEFILLAMA_POLL_INTERVAL:-600}
      DEFILLAMA_MAX_UNLOCK_FETCHES: ${DEFILLAMA_MAX_UNLOCK_FETCHES:-3}

  collector-binance-futures:
    <<: *service-defaults
    build: { context: ., dockerfile: docker/Dockerfile, args: { SERVICE_PATH: services/collector-binance-futures } }
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
      kafka:
        condition: service_healthy
    environment:
      <<: *common-env
      BINANCE_FUTURES_POLL_INTERVAL: ${BINANCE_FUTURES_POLL_INTERVAL:-300}
      KRAKEN_MAJOR_MIN_MENTIONS_7D: ${KRAKEN_MAJOR_MIN_MENTIONS_7D:-10}
```

Check the `kafka` service's actual name and healthcheck condition in the file first and match what `collector-coingecko` uses — copy its `depends_on` block verbatim rather than the one above if they differ.

No Traefik labels: neither service serves anything a browser should reach.

- [ ] **Step 2: Add both to `docker-compose.vps.yml`**

Copy the `collector-kraken` block from that file and adapt it for each service: it uses a prebuilt GHCR `image:` rather than `build:`. The image names are `ghcr.io/<owner>/bottrading-collector-defillama:latest` and `ghcr.io/<owner>/bottrading-collector-binance-futures:latest`, matching the tag pattern in `deploy.yml`.

- [ ] **Step 3: Add both to the build matrix**

In `.github/workflows/deploy.yml`, after the `collector-kraken` line (line 62), add:

```yaml
          - { name: collector-defillama,   dockerfile: docker/Dockerfile,     path: services/collector-defillama }
          - { name: collector-binance-futures, dockerfile: docker/Dockerfile, path: services/collector-binance-futures }
```

- [ ] **Step 4: Verify the compose files parse**

Run: `docker compose -f docker-compose.yml config --quiet && docker compose -f docker-compose.vps.yml config --quiet`
Expected: no output, exit 0

- [ ] **Step 5: Update `CLAUDE.md`**

In the Pipeline section, add the two collectors to the diagram's producer list. Then add a paragraph after the pipeline-graph one:

```markdown
**Positioning & fundamentals (`collector-binance-futures`, `collector-defillama`).** Two
keyless sources feed two scoring axes: `positioning` (funding, open interest, long/short —
contrarian on crowding) and `fundamentals` (TVL, fees, token unlocks). Both republish every
cycle because `FeatureStore` expires at 900s while funding moves every 8h. **Unlocks are not
on DefiLlama's free API** (`/emissions` answers 402); they come from the
`defillama-datasets.llama.fi` CDN at ~2.25 MB per protocol, so the collector fetches at most 3
per cycle round-robin and caches the extraction for 24h.

The scoring model is now **seven axes renormalised over present weight** — an absent axis is
excluded, not scored 0.0. That was the `RISK_MIN_SCORE=70` / max-score-68 deadlock: absent
data was priced as worst-case data. `confidence` is the sum of present weights, which makes
`score_v2 = score_v1 / confidence_v1` an identity on legacy features and is how
`DECISION_THRESHOLD` was re-derived.
```

- [ ] **Step 6: Run the whole suite and the linter**

Run: `pytest tests/ -q && make lint`
Expected: PASS, no lint errors

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml docker-compose.vps.yml .github/workflows/deploy.yml CLAUDE.md
git commit -m "chore(deploy): wire the derivatives and fundamentals collectors"
```

---

## Deployment procedure

Not a code task — the order matters, and step 3 is the one that changes trading behaviour.

1. Deploy both collectors. Confirm from the logs that DefiLlama publishes events and that the Binance broad tier is not empty (a geo-block would show as the WARNING from Task 9).
2. Wait ~30 minutes, then confirm the axes are populated: pick a major from `decision_journal.features` and check `funding_rate_8h` is present.
3. Run the two threshold queries from the spec against production, set `DECISION_THRESHOLD` to the result, and deploy the scoring change.
4. Watch the decision rate for 24h. If it moved sharply, retune `DECISION_THRESHOLD` — that is an env change and a restart, not a redeploy.

```sql
-- current decision rate
SELECT count(*) FILTER (WHERE score >= 70)::float / count(*)
FROM decision_journal WHERE time > now() - interval '7 days';

-- the v2 threshold preserving it (substitute <rate> above)
SELECT percentile_disc(1 - <rate>) WITHIN GROUP (ORDER BY score::float / confidence)
FROM decision_journal WHERE time > now() - interval '7 days' AND confidence > 0;
```
