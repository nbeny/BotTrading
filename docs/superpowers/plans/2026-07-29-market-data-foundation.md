# Market Data Foundation (Lot 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put real data behind every market read the terminal already claims to show, and ingest true Kraken OHLC candles + order-book depth as the foundation Lot 2's fusion engine will compute on.

**Architecture:** A new `collector-kraken` service polls Kraken's public API and writes three Postgres tables (`candles`, `market_depth`, `venue_pairs`) — no Kafka, since a candle is reference data to query, not an event to react to. A shared `SqlCandleReader` in `cmi_common` exposes them. The api-gateway persister starts populating `tokens`, `read_api.py` is rewired off two dead tables onto the live ones, and those dead tables are dropped.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, PostgreSQL 16 + TimescaleDB, Alembic, httpx, pytest.

**Spec:** `docs/superpowers/specs/2026-07-29-market-data-foundation-design.md`

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `services/collector-kraken/app/__init__.py` | package marker |
| `services/collector-kraken/app/domain/__init__.py` | package marker |
| `services/collector-kraken/app/domain/pairs.py` | pure: AssetPairs payload → tradable USD pairs, Kraken ticker normalization |
| `services/collector-kraken/app/domain/mapper.py` | pure: OHLC payload → candles, Depth payload → spread/notional |
| `services/collector-kraken/app/infrastructure/__init__.py` | package marker |
| `services/collector-kraken/app/infrastructure/kraken.py` | httpx client for the three public endpoints |
| `services/collector-kraken/app/infrastructure/repository.py` | upserts into candles / market_depth / venue_pairs |
| `services/collector-kraken/app/application/__init__.py` | package marker |
| `services/collector-kraken/app/application/universe.py` | pure + SQL: resolve universe and the majors subset |
| `services/collector-kraken/app/application/sweeper.py` | the two poll loops |
| `services/collector-kraken/app/main.py` | service assembly |
| `libs/cmi_common/cmi_common/sources/candles.py` | `SqlCandleReader` + pure interval helpers |
| `migrations/alembic/versions/0013_market_data_foundation.py` | create the three tables |
| `migrations/alembic/versions/0014_drop_dead_tables.py` | drop `news` and `sentiments` |
| `tests/test_kraken_pairs.py` | pure pair tests |
| `tests/test_kraken_mapper.py` | pure OHLC/depth tests |
| `tests/test_candle_reader.py` | reader pure-function tests |
| `tests/test_kraken_universe.py` | universe/majors selection tests |
| `tests/test_tokens_persistence.py` | persister token upsert tests |
| `tests/fixtures/kraken/*.json` | recorded Kraken payloads |

**Modified:**

| Path | Change |
|---|---|
| `libs/cmi_common/cmi_common/db/models.py` | add `Candle`, `MarketDepth`, `VenuePair`; remove `News`, `Sentiment` |
| `libs/cmi_common/cmi_common/db/__init__.py` | export the new models, drop the removed ones |
| `libs/cmi_common/cmi_common/sources/__init__.py` | export `SqlCandleReader`, `Candle`, `Depth` |
| `libs/cmi_common/cmi_common/events/market.py` | add `PriceEvent.name` |
| `services/collector-coingecko/app/domain/mapper.py` | pass `name` through |
| `services/api-gateway/app/persister.py` | upsert `tokens` from `PriceEvent` |
| `services/api-gateway/app/read_api.py` | four read rewirings |
| `scripts/verify_read_live.py` | plausibility assertions |
| `docker-compose.yml`, `docker-compose.vps.yml` | `collector-kraken` service |
| `.github/workflows/deploy.yml` | build/push `collector-kraken` |

**Naming contract (used across tasks — do not drift):**
- Domain dataclasses in `collector-kraken`: `VenuePairSpec`, `OhlcCandle`, `DepthSnapshot`.
- ORM models in `cmi_common.db.models`: `Candle`, `MarketDepth`, `VenuePair`.
- Reader dataclasses in `cmi_common.sources.candles`: `Candle` (read shape), `Depth`.
- Intervals are the literal strings `"1h"` and `"15m"` everywhere.

---

### Task 1: Kraken pair reference (pure domain)

**Files:**
- Create: `services/collector-kraken/app/__init__.py` (empty)
- Create: `services/collector-kraken/app/domain/__init__.py` (empty)
- Create: `services/collector-kraken/app/domain/pairs.py`
- Test: `tests/test_kraken_pairs.py`

- [ ] **Step 1: Write the failing test**

```python
"""Pure tests for Kraken AssetPairs parsing and ticker normalization."""

from __future__ import annotations

from decimal import Decimal

from service_modules import load_service_module

pairs = load_service_module("collector-kraken", "domain.pairs")
normalize_base = pairs.normalize_base
parse_asset_pairs = pairs.parse_asset_pairs


def _payload(**entries):
    return {"error": [], "result": entries}


def test_normalize_base_maps_kraken_legacy_tickers():
    assert normalize_base("XBT") == "BTC"
    assert normalize_base("XDG") == "DOGE"


def test_normalize_base_upcases_and_passes_through():
    assert normalize_base("sol") == "SOL"


def test_parse_asset_pairs_keeps_online_usd_pairs():
    payload = _payload(
        XXBTZUSD={
            "wsname": "XBT/USD", "status": "online", "ordermin": "0.0001",
        },
    )
    specs = parse_asset_pairs(payload)
    assert len(specs) == 1
    assert specs[0].symbol == "BTC"
    assert specs[0].pair == "XXBTZUSD"
    assert specs[0].ordermin == Decimal("0.0001")


def test_parse_asset_pairs_drops_non_usd_quotes():
    payload = _payload(
        XXBTZEUR={"wsname": "XBT/EUR", "status": "online", "ordermin": "0.0001"},
    )
    assert parse_asset_pairs(payload) == []


def test_parse_asset_pairs_drops_offline_pairs():
    payload = _payload(
        SOLUSD={"wsname": "SOL/USD", "status": "cancel_only", "ordermin": "0.1"},
    )
    assert parse_asset_pairs(payload) == []


def test_parse_asset_pairs_skips_entries_without_wsname():
    """Kraken ships a handful of legacy entries with no wsname; skip, never crash."""
    payload = _payload(WEIRD={"status": "online", "ordermin": "1"})
    assert parse_asset_pairs(payload) == []


def test_parse_asset_pairs_defaults_missing_ordermin_to_none():
    payload = _payload(SOLUSD={"wsname": "SOL/USD", "status": "online"})
    assert parse_asset_pairs(payload)[0].ordermin is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kraken_pairs.py -v`
Expected: FAIL — `FileNotFoundError` / `ModuleNotFoundError` for `domain.pairs`.

- [ ] **Step 3: Write minimal implementation**

Create `services/collector-kraken/app/__init__.py` and `services/collector-kraken/app/domain/__init__.py` as empty files.

Create `services/collector-kraken/app/domain/pairs.py`:

```python
"""Pure Kraken pair reference — AssetPairs payload in, tradable specs out.

Kraken is the venue this bot actually executes on, so its pair list is also the
tradability filter: a symbol with no Kraken pair has no candles, no book, and no
business producing a trade signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

#: Kraken spells two assets the way nobody else does.
KRAKEN_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}

QUOTE = "USD"


@dataclass(frozen=True, slots=True)
class VenuePairSpec:
    symbol: str            # normalized ticker, e.g. "BTC"
    pair: str              # Kraken pair id used in API calls, e.g. "XXBTZUSD"
    ordermin: Decimal | None


def normalize_base(base: str) -> str:
    up = base.upper()
    return KRAKEN_ALIASES.get(up, up)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def parse_asset_pairs(payload: dict[str, Any]) -> list[VenuePairSpec]:
    """Online, USD-quoted pairs from a ``/0/public/AssetPairs`` response."""
    out: list[VenuePairSpec] = []
    for pair_id, entry in (payload.get("result") or {}).items():
        wsname = entry.get("wsname")
        if not wsname or "/" not in wsname:
            continue
        base, _, quote = wsname.partition("/")
        if quote.upper() != QUOTE:
            continue
        if entry.get("status", "online") != "online":
            continue
        out.append(
            VenuePairSpec(
                symbol=normalize_base(base),
                pair=pair_id,
                ordermin=_decimal(entry.get("ordermin")),
            )
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_kraken_pairs.py -v`
Expected: PASS, 7 passed.

- [ ] **Step 5: Commit**

```bash
git add services/collector-kraken/app tests/test_kraken_pairs.py
git commit -m "feat(collector-kraken): pure Kraken pair reference parsing"
```

---

### Task 2: OHLC and depth mappers (pure domain)

**Files:**
- Create: `services/collector-kraken/app/domain/mapper.py`
- Test: `tests/test_kraken_mapper.py`

Kraken OHLC rows are `[time, open, high, low, close, vwap, volume, count]` with every
number as a string. Depth entries are `[price, volume, timestamp]`.

- [ ] **Step 1: Write the failing test**

```python
"""Pure tests for Kraken OHLC / Depth payload mapping."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from service_modules import load_service_module

mapper = load_service_module("collector-kraken", "domain.mapper")
parse_depth = mapper.parse_depth
parse_ohlc = mapper.parse_ohlc


OHLC_ROW = [1690000000, "29000.0", "29100.0", "28900.0", "29050.0",
            "29010.0", "123.45", 42]


def test_parse_ohlc_maps_every_field_as_decimal():
    payload = {"error": [], "result": {"XXBTZUSD": [OHLC_ROW], "last": 1690003600}}
    candles = parse_ohlc(payload, symbol="BTC", interval="1h")
    assert len(candles) == 1
    c = candles[0]
    assert c.time == datetime(2023, 7, 22, 4, 26, 40, tzinfo=UTC)
    assert c.symbol == "BTC"
    assert c.interval == "1h"
    assert c.open == Decimal("29000.0")
    assert c.high == Decimal("29100.0")
    assert c.low == Decimal("28900.0")
    assert c.close == Decimal("29050.0")
    assert c.vwap == Decimal("29010.0")
    assert c.volume == Decimal("123.45")
    assert c.trades == 42


def test_parse_ohlc_ignores_the_last_cursor_key():
    """`last` sits beside the pair key in `result` and is an int, not a series."""
    payload = {"error": [], "result": {"XXBTZUSD": [OHLC_ROW], "last": 1690003600}}
    assert len(parse_ohlc(payload, symbol="BTC", interval="1h")) == 1


def test_parse_ohlc_returns_empty_on_missing_result():
    assert parse_ohlc({"error": [], "result": {}}, symbol="BTC", interval="1h") == []


def test_parse_ohlc_skips_malformed_rows():
    payload = {"error": [], "result": {"X": [OHLC_ROW, [1, "2"]]}}
    assert len(parse_ohlc(payload, symbol="BTC", interval="1h")) == 1


DEPTH = {
    "error": [],
    "result": {
        "XXBTZUSD": {
            "asks": [["100.0", "2.0", 1], ["101.0", "3.0", 1], ["200.0", "9.0", 1]],
            "bids": [["99.0", "4.0", 1], ["98.5", "1.0", 1], ["10.0", "9.0", 1]],
        }
    },
}


def test_parse_depth_computes_mid_and_spread():
    snap = parse_depth(DEPTH, symbol="BTC")
    assert snap.symbol == "BTC"
    assert snap.mid_price == Decimal("99.5")
    # (100 - 99) / 99.5 * 100
    assert round(snap.spread_pct, 4) == round(float(Decimal("1") / Decimal("99.5") * 100), 4)


def test_parse_depth_sums_only_the_band_around_mid():
    """±1% of a 99.5 mid is [98.505, 100.495]: the 200 ask and 98.5/10 bids fall out."""
    snap = parse_depth(DEPTH, symbol="BTC", band_pct=1.0)
    assert snap.ask_depth_usd == Decimal("100.0") * Decimal("2.0")
    assert snap.bid_depth_usd == Decimal("99.0") * Decimal("4.0")


def test_parse_depth_returns_none_on_one_sided_book():
    payload = {"error": [], "result": {"X": {"asks": [], "bids": [["1", "1", 1]]}}}
    assert parse_depth(payload, symbol="BTC") is None


def test_parse_depth_returns_none_on_missing_result():
    assert parse_depth({"error": [], "result": {}}, symbol="BTC") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kraken_mapper.py -v`
Expected: FAIL — module `domain.mapper` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `services/collector-kraken/app/domain/mapper.py`:

```python
"""Pure mapping: Kraken public payloads -> typed candles and depth snapshots.

Every number Kraken returns is a string; all of them are parsed to Decimal and
never to float, matching the Numeric(38,12) columns they land in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

#: `result` carries the series under the pair id plus this scalar cursor.
_CURSOR_KEY = "last"


@dataclass(frozen=True, slots=True)
class OhlcCandle:
    time: datetime
    symbol: str
    interval: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    vwap: Decimal
    volume: Decimal
    trades: int


@dataclass(frozen=True, slots=True)
class DepthSnapshot:
    symbol: str
    mid_price: Decimal
    spread_pct: float
    bid_depth_usd: Decimal
    ask_depth_usd: Decimal


def _series(payload: dict[str, Any]) -> Any:
    """The single non-cursor value in `result`, or None."""
    result = payload.get("result") or {}
    for key, value in result.items():
        if key != _CURSOR_KEY:
            return value
    return None


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"not a decimal: {value!r}") from exc


def parse_ohlc(
    payload: dict[str, Any], *, symbol: str, interval: str
) -> list[OhlcCandle]:
    rows = _series(payload)
    if not rows:
        return []
    out: list[OhlcCandle] = []
    for row in rows:
        try:
            out.append(
                OhlcCandle(
                    time=datetime.fromtimestamp(int(row[0]), tz=UTC),
                    symbol=symbol,
                    interval=interval,
                    open=_dec(row[1]),
                    high=_dec(row[2]),
                    low=_dec(row[3]),
                    close=_dec(row[4]),
                    vwap=_dec(row[5]),
                    volume=_dec(row[6]),
                    trades=int(row[7]),
                )
            )
        except (IndexError, TypeError, ValueError):
            # A malformed row is dropped, never allowed to kill the sweep: one
            # bad candle must not cost the other 719.
            continue
    return out


def parse_depth(
    payload: dict[str, Any], *, symbol: str, band_pct: float = 1.0
) -> DepthSnapshot | None:
    book = _series(payload)
    if not isinstance(book, dict):
        return None
    asks = book.get("asks") or []
    bids = book.get("bids") or []
    if not asks or not bids:
        return None
    try:
        best_ask = _dec(asks[0][0])
        best_bid = _dec(bids[0][0])
    except (IndexError, ValueError):
        return None
    mid = (best_ask + best_bid) / 2
    if mid <= 0:
        return None
    band = Decimal(str(band_pct)) / Decimal("100")
    floor, ceiling = mid * (1 - band), mid * (1 + band)

    def _notional(levels: list, keep) -> Decimal:
        total = Decimal("0")
        for level in levels:
            try:
                price, qty = _dec(level[0]), _dec(level[1])
            except (IndexError, ValueError):
                continue
            if keep(price):
                total += price * qty
        return total

    return DepthSnapshot(
        symbol=symbol,
        mid_price=mid,
        spread_pct=float((best_ask - best_bid) / mid * 100),
        bid_depth_usd=_notional(bids, lambda p: p >= floor),
        ask_depth_usd=_notional(asks, lambda p: p <= ceiling),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_kraken_mapper.py -v`
Expected: PASS, 8 passed.

- [ ] **Step 5: Commit**

```bash
git add services/collector-kraken/app/domain/mapper.py tests/test_kraken_mapper.py
git commit -m "feat(collector-kraken): pure OHLC and order-book mappers"
```

---

### Task 3: Schema — models and migration 0013

**Files:**
- Modify: `libs/cmi_common/cmi_common/db/models.py`
- Modify: `libs/cmi_common/cmi_common/db/__init__.py`
- Create: `migrations/alembic/versions/0013_market_data_foundation.py`

- [ ] **Step 1: Add the ORM models**

Append to `libs/cmi_common/cmi_common/db/models.py` (after `ContentSentimentAggDaily`):

```python
class Candle(Base):
    """OHLC candles from the execution venue (Kraken spot).

    `interval` is part of the key so one table serves both granularities; the
    forming candle is rewritten on every sweep, so writers upsert with
    ON CONFLICT DO UPDATE. Whether a candle is closed is derived from its
    timestamp, never stored — a boolean column lies the moment a writer forgets it.
    """

    __tablename__ = "candles"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(38, 12))
    high: Mapped[Decimal] = mapped_column(Numeric(38, 12))
    low: Mapped[Decimal] = mapped_column(Numeric(38, 12))
    close: Mapped[Decimal] = mapped_column(Numeric(38, 12))
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
    volume: Mapped[Decimal] = mapped_column(Numeric(38, 12), default=0)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32), default="kraken")


class MarketDepth(Base):
    """Order-book snapshot: the measured liquidity that replaces the volume proxy."""

    __tablename__ = "market_depth"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    mid_price: Mapped[Decimal] = mapped_column(Numeric(38, 12))
    spread_pct: Mapped[float] = mapped_column(Float)
    bid_depth_usd: Mapped[Decimal] = mapped_column(Numeric(38, 2))
    ask_depth_usd: Mapped[Decimal] = mapped_column(Numeric(38, 2))
    source: Mapped[str] = mapped_column(String(32), default="kraken")


class VenuePair(Base):
    """Which symbols are actually tradable on which venue, and at what minimum.

    Reference data, not a time series. `ambiguous` records that the CoinGecko
    ticker resolved to more than one coin: tickers are not unique there, and
    attaching a real Kraken pair's candles to a worthless homonym is a silent
    correctness bug, so the ambiguity is stored rather than swallowed.
    """

    __tablename__ = "venue_pairs"

    venue: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    pair: Mapped[str] = mapped_column(String(64))
    ordermin: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
    tradable: Mapped[bool] = mapped_column(Boolean, default=True)
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

If `Numeric`, `DateTime`, `Float`, `func` are not already imported at the top of
`models.py`, add them to the existing `sqlalchemy` import line.

- [ ] **Step 2: Export them**

In `libs/cmi_common/cmi_common/db/__init__.py`, add `Candle`, `MarketDepth`, `VenuePair`
to both the import from `.models` and the `__all__` list. Leave `News` and `Sentiment`
alone for now — Task 11 removes them, after the readers are gone.

- [ ] **Step 3: Write the migration**

Create `migrations/alembic/versions/0013_market_data_foundation.py`:

```python
"""candles / market_depth / venue_pairs

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candles",
        # `time` leads the primary key: TimescaleDB refuses any unique index
        # that omits the partitioning column (same constraint as events_market).
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("interval", sa.String(8), primary_key=True),
        sa.Column("open", sa.Numeric(38, 12), nullable=False),
        sa.Column("high", sa.Numeric(38, 12), nullable=False),
        sa.Column("low", sa.Numeric(38, 12), nullable=False),
        sa.Column("close", sa.Numeric(38, 12), nullable=False),
        sa.Column("vwap", sa.Numeric(38, 12)),
        sa.Column("volume", sa.Numeric(38, 12), server_default="0"),
        sa.Column("trades", sa.Integer, server_default="0"),
        sa.Column("source", sa.String(32), server_default="kraken"),
    )
    # Serves "last N candles of symbol X at interval Y", the reader's only shape.
    op.create_index(
        "ix_candles_symbol_interval", "candles", ["symbol", "interval", "time"]
    )
    op.execute(
        "SELECT create_hypertable('candles', 'time', "
        "chunk_time_interval => INTERVAL '7 days', migrate_data => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('candles', INTERVAL '90 days', "
        "if_not_exists => TRUE)"
    )

    op.create_table(
        "market_depth",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("mid_price", sa.Numeric(38, 12), nullable=False),
        sa.Column("spread_pct", sa.Float, nullable=False),
        sa.Column("bid_depth_usd", sa.Numeric(38, 2), nullable=False),
        sa.Column("ask_depth_usd", sa.Numeric(38, 2), nullable=False),
        sa.Column("source", sa.String(32), server_default="kraken"),
    )
    op.create_index("ix_market_depth_symbol", "market_depth", ["symbol", "time"])
    op.execute(
        "SELECT create_hypertable('market_depth', 'time', "
        "chunk_time_interval => INTERVAL '1 day', migrate_data => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('market_depth', INTERVAL '7 days', "
        "if_not_exists => TRUE)"
    )

    # Reference data, not a time series: a plain table, no hypertable.
    op.create_table(
        "venue_pairs",
        sa.Column("venue", sa.String(32), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("pair", sa.String(64), nullable=False),
        sa.Column("ordermin", sa.Numeric(38, 12)),
        sa.Column("tradable", sa.Boolean, server_default=sa.true()),
        sa.Column("ambiguous", sa.Boolean, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("venue_pairs")
    # Retention policies must go before the table; remove_retention_policy takes
    # `if_exists`, and the wrong keyword raises before the DROP TABLE runs.
    op.execute("SELECT remove_retention_policy('market_depth', if_exists => TRUE)")
    op.drop_index("ix_market_depth_symbol", table_name="market_depth")
    op.drop_table("market_depth")
    op.execute("SELECT remove_retention_policy('candles', if_exists => TRUE)")
    op.drop_index("ix_candles_symbol_interval", table_name="candles")
    op.drop_table("candles")
```

- [ ] **Step 4: Verify the migration applies and rolls back**

Run:
```bash
docker compose up -d postgres
docker compose run --rm api-gateway alembic upgrade head
docker compose run --rm api-gateway alembic downgrade 0012
docker compose run --rm api-gateway alembic upgrade head
```
Expected: three clean runs, no error. Confirm with:
```bash
docker compose exec postgres psql -U cmi -d cmi -c "\d candles"
```
Expected: the table exists with a `(time, symbol, interval)` primary key.

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/db migrations/alembic/versions/0013_market_data_foundation.py
git commit -m "feat(db): candles, market_depth and venue_pairs tables"
```

---

### Task 4: SqlCandleReader

**Files:**
- Create: `libs/cmi_common/cmi_common/sources/candles.py`
- Modify: `libs/cmi_common/cmi_common/sources/__init__.py`
- Test: `tests/test_candle_reader.py`

- [ ] **Step 1: Write the failing test**

```python
"""Pure-function tests for the candle reader.

The SQL methods need Postgres and are covered by scripts/verify_read_live.py;
the closedness and interval maths are pure and tested here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cmi_common.sources.candles import interval_delta, is_closed

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def test_interval_delta_known_intervals():
    assert interval_delta("1h") == timedelta(hours=1)
    assert interval_delta("15m") == timedelta(minutes=15)


def test_interval_delta_rejects_unknown():
    with pytest.raises(ValueError, match="unknown interval"):
        interval_delta("4h")


def test_is_closed_true_for_a_fully_elapsed_bucket():
    assert is_closed(datetime(2026, 7, 29, 11, 0, tzinfo=UTC), "1h", NOW) is True


def test_is_closed_false_for_the_forming_bucket():
    """The 12:00 candle closes at 13:00; at 12:00 it holds one tick."""
    assert is_closed(NOW, "1h", NOW) is False


def test_is_closed_is_exact_at_the_boundary():
    """A bucket ending exactly at `now` is closed: its last second has elapsed."""
    assert is_closed(datetime(2026, 7, 29, 11, 45, tzinfo=UTC), "15m", NOW) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_candle_reader.py -v`
Expected: FAIL — `ModuleNotFoundError: cmi_common.sources.candles`.

- [ ] **Step 3: Write minimal implementation**

Create `libs/cmi_common/cmi_common/sources/candles.py`:

```python
"""Read-side access to `candles` and `market_depth`.

Mirrors SqlSentimentAggReader: the interval/closedness maths are pure functions
tested without a database, and the class only runs range queries.

This module deliberately computes no indicator. RSI, EMA and ATR belong to the
scoring layer; putting them here would make the collector's storage format and
the strategy's maths impossible to change independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Candle as CandleRow
from ..db.models import MarketDepth

INTERVALS: tuple[str, ...] = ("15m", "1h")

_INTERVAL_DELTAS: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
}


@dataclass(slots=True)
class Candle:
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(slots=True)
class Depth:
    time: datetime
    mid_price: Decimal
    spread_pct: float
    bid_depth_usd: Decimal
    ask_depth_usd: Decimal

    @property
    def total_depth_usd(self) -> Decimal:
        return self.bid_depth_usd + self.ask_depth_usd


def interval_delta(interval: str) -> timedelta:
    try:
        return _INTERVAL_DELTAS[interval]
    except KeyError as exc:
        raise ValueError(f"unknown interval {interval!r}") from exc


def is_closed(bucket_start: datetime, interval: str, now: datetime) -> bool:
    """True once the whole bucket has elapsed.

    Derived rather than stored: an indicator computed over a still-forming
    candle silently reads a partial bar, and a `closed` column would only be as
    honest as the last writer to remember it.
    """
    return bucket_start + interval_delta(interval) <= now


class SqlCandleReader:
    """AsyncSession-backed reader over candles / market_depth."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def series(
        self,
        *,
        symbol: str,
        interval: str,
        points: int,
        closed_only: bool = True,
        now: datetime | None = None,
    ) -> list[Candle]:
        """Up to `points` candles, oldest first, current bucket excluded by default."""
        now = now or datetime.now(tz=UTC)
        delta = interval_delta(interval)
        since = now - delta * points
        stmt = (
            select(CandleRow)
            .where(CandleRow.symbol == symbol)
            .where(CandleRow.interval == interval)
            .where(CandleRow.time >= since)
            .order_by(CandleRow.time.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            Candle(
                time=r.time, open=r.open, high=r.high, low=r.low,
                close=r.close, volume=r.volume,
            )
            for r in rows
            if not closed_only or is_closed(r.time, interval, now)
        ][-points:]

    async def latest(
        self,
        *,
        symbols: list[str],
        interval: str,
        closed_only: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Candle]:
        """Most recent candle per symbol, in one query."""
        now = now or datetime.now(tz=UTC)
        if not symbols:
            return {}
        stmt = (
            select(CandleRow)
            .where(CandleRow.symbol.in_(symbols))
            .where(CandleRow.interval == interval)
            .distinct(CandleRow.symbol)
            .order_by(CandleRow.symbol, CandleRow.time.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return {
            r.symbol: Candle(
                time=r.time, open=r.open, high=r.high, low=r.low,
                close=r.close, volume=r.volume,
            )
            for r in rows
            if not closed_only or is_closed(r.time, interval, now)
        }

    async def latest_depth(self, *, symbols: list[str]) -> dict[str, Depth]:
        """Most recent book snapshot per symbol, in one query.

        A symbol absent from the result was never measured. Callers must keep
        that distinct from "measured and thin" — an unmeasured symbol coerced to
        0.0 would read downstream as a dead market.
        """
        if not symbols:
            return {}
        stmt = (
            select(MarketDepth)
            .where(MarketDepth.symbol.in_(symbols))
            .distinct(MarketDepth.symbol)
            .order_by(MarketDepth.symbol, MarketDepth.time.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return {
            r.symbol: Depth(
                time=r.time,
                mid_price=r.mid_price,
                spread_pct=r.spread_pct,
                bid_depth_usd=r.bid_depth_usd,
                ask_depth_usd=r.ask_depth_usd,
            )
            for r in rows
        }
```

In `libs/cmi_common/cmi_common/sources/__init__.py`, add to the imports and `__all__`:
`from .candles import INTERVALS, Candle, Depth, SqlCandleReader, interval_delta, is_closed`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_candle_reader.py -v`
Expected: PASS, 5 passed.

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/sources tests/test_candle_reader.py
git commit -m "feat(cmi-common): SqlCandleReader over candles and market_depth"
```

---

### Task 5: Kraken public HTTP client

**Files:**
- Create: `services/collector-kraken/app/infrastructure/__init__.py` (empty)
- Create: `services/collector-kraken/app/infrastructure/kraken.py`
- Create: `tests/fixtures/kraken/asset_pairs.json`, `tests/fixtures/kraken/ohlc.json`, `tests/fixtures/kraken/depth.json`
- Test: extend `tests/test_kraken_mapper.py`

Kraken's decisive quirk: it answers **HTTP 200 with a non-empty `error` array** when
rate-limited. Checking only the status code silently treats a rejection as an empty
series, so candles would stop arriving with no error anywhere.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kraken_mapper.py`:

```python
import httpx
import pytest
from cmi_common.sources import RateLimitedError

kraken = load_service_module("collector-kraken", "infrastructure.kraken")
KrakenPublicClient = kraken.KrakenPublicClient


def _client(handler) -> "kraken.KrakenPublicClient":
    c = KrakenPublicClient()
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_ohlc_returns_the_payload_on_success():
    def handler(request):
        assert request.url.params["interval"] == "60"
        return httpx.Response(200, json={"error": [], "result": {"X": [OHLC_ROW]}})

    client = _client(handler)
    payload = await client.ohlc("XXBTZUSD", interval_minutes=60)
    assert payload["result"]["X"] == [OHLC_ROW]
    await client.close()


@pytest.mark.asyncio
async def test_ohlc_raises_rate_limited_on_error_array_despite_http_200():
    """Kraken signals throttling in the body, not the status code."""
    def handler(request):
        return httpx.Response(
            200, json={"error": ["EAPI:Rate limit exceeded"], "result": {}}
        )

    client = _client(handler)
    with pytest.raises(RateLimitedError):
        await client.ohlc("XXBTZUSD", interval_minutes=60)
    await client.close()


@pytest.mark.asyncio
async def test_ohlc_raises_on_a_non_ratelimit_error_array():
    def handler(request):
        return httpx.Response(200, json={"error": ["EQuery:Unknown asset pair"]})

    client = _client(handler)
    with pytest.raises(ValueError, match="Unknown asset pair"):
        await client.ohlc("NOPE", interval_minutes=60)
    await client.close()


@pytest.mark.asyncio
async def test_ohlc_raises_rate_limited_on_http_429():
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "17"}, json={})

    client = _client(handler)
    with pytest.raises(RateLimitedError) as exc:
        await client.ohlc("XXBTZUSD", interval_minutes=60)
    assert exc.value.retry_after == 17
    await client.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kraken_mapper.py -k kraken_or_ohlc -v`
Expected: FAIL — module `infrastructure.kraken` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `services/collector-kraken/app/infrastructure/__init__.py` (empty) and
`services/collector-kraken/app/infrastructure/kraken.py`:

```python
"""Kraken public REST client — AssetPairs, OHLC, Depth. No authentication.

Kraken reports throttling as HTTP 200 with a non-empty `error` array, so the
body is checked on every call: trusting the status code alone would turn a
rejection into an empty series and stop ingestion silently.
"""

from __future__ import annotations

from typing import Any

import httpx

from cmi_common.sources import RateLimitedError, parse_retry_after

BASE_URL = "https://api.kraken.com/0/public"
_RATE_LIMIT_MARKER = "rate limit"


class KrakenPublicClient:
    name = "kraken"
    #: Kraken tolerates roughly 1 public request/second sustained.
    rate_limit = (60, 60)

    def __init__(
        self, base_url: str = BASE_URL, *, timeout: float = 20.0
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "cmi-collector/0.1"}, timeout=timeout
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.get(f"{self._base}/{path}", params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError(
                    parse_retry_after(exc.response, default=60)
                ) from exc
            raise
        payload = resp.json()
        errors = payload.get("error") or []
        if errors:
            joined = "; ".join(str(e) for e in errors)
            if _RATE_LIMIT_MARKER in joined.lower():
                raise RateLimitedError(60)
            raise ValueError(f"kraken error: {joined}")
        return payload

    async def asset_pairs(self) -> dict[str, Any]:
        return await self._get("AssetPairs", {})

    async def ohlc(
        self, pair: str, *, interval_minutes: int, since: int | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"pair": pair, "interval": interval_minutes}
        if since is not None:
            params["since"] = since
        return await self._get("OHLC", params)

    async def depth(self, pair: str, *, count: int = 20) -> dict[str, Any]:
        return await self._get("Depth", {"pair": pair, "count": count})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_kraken_mapper.py -v`
Expected: PASS, 12 passed.

- [ ] **Step 5: Commit**

```bash
git add services/collector-kraken/app/infrastructure tests/test_kraken_mapper.py
git commit -m "feat(collector-kraken): public REST client with body-level rate-limit detection"
```

---

### Task 6: Universe and majors selection

**Files:**
- Create: `services/collector-kraken/app/application/__init__.py` (empty)
- Create: `services/collector-kraken/app/application/universe.py`
- Test: `tests/test_kraken_universe.py`

- [ ] **Step 1: Write the failing test**

```python
"""Universe intersection and the majors split — the pure half."""

from __future__ import annotations

from decimal import Decimal

from service_modules import load_service_module

pairs = load_service_module("collector-kraken", "domain.pairs")
universe = load_service_module("collector-kraken", "application.universe")
VenuePairSpec = pairs.VenuePairSpec
intersect = universe.intersect
split_regimes = universe.split_regimes


def _spec(symbol):
    return VenuePairSpec(symbol=symbol, pair=f"{symbol}USD", ordermin=Decimal("1"))


def test_intersect_keeps_only_symbols_priced_in_both_places():
    specs = [_spec("BTC"), _spec("ETH"), _spec("XCP")]
    assert [s.symbol for s in intersect(specs, {"BTC", "ETH", "DEXE"})] == ["BTC", "ETH"]


def test_intersect_marks_nothing_when_prices_are_empty():
    assert intersect([_spec("BTC")], set()) == []


def test_split_regimes_promotes_symbols_above_the_mention_floor():
    majors, alts = split_regimes(
        [_spec("BTC"), _spec("ETH"), _spec("DEXE")],
        mentions={"BTC": 421, "ETH": 163, "DEXE": 0},
        min_mentions=10,
    )
    assert [s.symbol for s in majors] == ["BTC", "ETH"]
    assert [s.symbol for s in alts] == ["DEXE"]


def test_split_regimes_treats_an_unknown_symbol_as_zero_mentions():
    majors, alts = split_regimes([_spec("XYZ")], mentions={}, min_mentions=10)
    assert majors == []
    assert [s.symbol for s in alts] == ["XYZ"]


def test_split_regimes_is_inclusive_at_the_floor():
    majors, _ = split_regimes([_spec("LINK")], mentions={"LINK": 10}, min_mentions=10)
    assert [s.symbol for s in majors] == ["LINK"]


def test_ambiguous_symbols_flags_tickers_claimed_by_several_coins():
    """CoinGecko tickers are not unique; attaching a real Kraken pair's candles
    to a worthless homonym is a silent correctness bug."""
    rows = [("SOL", "solana", 5), ("SOL", "solana-ai", 4100), ("BTC", "bitcoin", 1)]
    assert universe.ambiguous_symbols(rows) == {"SOL"}


def test_ambiguous_symbols_is_empty_when_every_ticker_is_unique():
    assert universe.ambiguous_symbols([("BTC", "bitcoin", 1)]) == set()


def test_untradable_returns_priced_symbols_kraken_does_not_list():
    assert universe.untradable([_spec("BTC")], {"BTC", "DEXE"}) == {"DEXE"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kraken_universe.py -v`
Expected: FAIL — module `application.universe` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `services/collector-kraken/app/application/__init__.py` (empty) and
`services/collector-kraken/app/application/universe.py`:

```python
"""Which symbols we sweep, and which of them are majors.

Both are derived from data on every cycle, never hard-coded: a token that starts
being talked about joins the majors set on its own, and one that goes quiet
leaves it. The pure selection functions are unit-tested; the two SQL helpers
supply their inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cmi_common.db.models import ContentSentimentAgg, Price

from ..domain.pairs import VenuePairSpec

#: Knee of the observed mention distribution (measured 2026-07-29: 11 symbols
#: clear 10 over 7 days, the next tier sits in single digits). See the spec.
DEFAULT_MIN_MENTIONS = 10


def intersect(
    specs: list[VenuePairSpec], priced_symbols: set[str]
) -> list[VenuePairSpec]:
    """Kraken-tradable pairs we also have prices for."""
    return [s for s in specs if s.symbol in priced_symbols]


def split_regimes(
    specs: list[VenuePairSpec],
    *,
    mentions: dict[str, int],
    min_mentions: int = DEFAULT_MIN_MENTIONS,
) -> tuple[list[VenuePairSpec], list[VenuePairSpec]]:
    """(majors, alts) — majors are sentiment-covered enough to fuse on."""
    majors = [s for s in specs if mentions.get(s.symbol, 0) >= min_mentions]
    major_symbols = {s.symbol for s in majors}
    return majors, [s for s in specs if s.symbol not in major_symbols]


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


def ambiguous_symbols(rows: list[tuple[str, str, int | None]]) -> set[str]:
    """Tickers claimed by more than one coin, from (symbol, coin_id, rank) rows.

    Recorded rather than silently resolved: `venue_pairs.ambiguous` leaves a
    trace an operator can query, instead of a mapping that looks certain.
    """
    seen: dict[str, set[str]] = {}
    for symbol, coin_id, _rank in rows:
        seen.setdefault(symbol, set()).add(coin_id)
    return {symbol for symbol, coins in seen.items() if len(coins) > 1}


def untradable(specs: list[VenuePairSpec], priced: set[str]) -> set[str]:
    """Symbols we price but Kraken does not list — the "cannot trade this" set."""
    return priced - {s.symbol for s in specs}


async def token_symbol_ranks(
    session: AsyncSession,
) -> list[tuple[str, str, int | None]]:
    """(symbol, coin_id, market_cap_rank) for every known token."""
    stmt = select(Token.symbol, Token.coin_id, Token.metadata_)
    return [
        (symbol, coin_id or "", (meta or {}).get("market_cap_rank"))
        for symbol, coin_id, meta in (await session.execute(stmt)).all()
    ]
```

Add `Token` to the `cmi_common.db.models` import at the top of this module.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_kraken_universe.py -v`
Expected: PASS, 8 passed.

- [ ] **Step 5: Commit**

```bash
git add services/collector-kraken/app/application tests/test_kraken_universe.py
git commit -m "feat(collector-kraken): data-derived universe and majors split"
```

---

### Task 7: Repository and the sweep loops

**Files:**
- Create: `services/collector-kraken/app/infrastructure/repository.py`
- Create: `services/collector-kraken/app/application/sweeper.py`
- Create: `services/collector-kraken/app/main.py`
- Modify: `docker-compose.yml`, `docker-compose.vps.yml`, `.github/workflows/deploy.yml`

- [ ] **Step 1: Write the repository**

Create `services/collector-kraken/app/infrastructure/repository.py`:

```python
"""Upserts for candles, depth snapshots and the venue pair reference."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cmi_common.db.models import Candle, MarketDepth, ServiceHealth, VenuePair

from ..domain.mapper import DepthSnapshot, OhlcCandle
from ..domain.pairs import VenuePairSpec

VENUE = "kraken"
SERVICE = "collector-kraken"


class KrakenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_candles(self, candles: list[OhlcCandle]) -> int:
        if not candles:
            return 0
        rows = [
            {
                "time": c.time, "symbol": c.symbol, "interval": c.interval,
                "open": c.open, "high": c.high, "low": c.low, "close": c.close,
                "vwap": c.vwap, "volume": c.volume, "trades": c.trades,
                "source": VENUE,
            }
            for c in candles
        ]
        stmt = insert(Candle).values(rows)
        # DO UPDATE, not DO NOTHING: the newest candle is still forming and is
        # refetched with new high/low/close/volume on every sweep until it closes.
        stmt = stmt.on_conflict_do_update(
            index_elements=["time", "symbol", "interval"],
            set_={
                "open": stmt.excluded.open, "high": stmt.excluded.high,
                "low": stmt.excluded.low, "close": stmt.excluded.close,
                "vwap": stmt.excluded.vwap, "volume": stmt.excluded.volume,
                "trades": stmt.excluded.trades,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()
        return len(rows)

    async def insert_depth(self, snapshots: list[DepthSnapshot]) -> int:
        if not snapshots:
            return 0
        now = datetime.now(tz=UTC)
        stmt = insert(MarketDepth).values([
            {
                "time": now, "symbol": s.symbol, "mid_price": s.mid_price,
                "spread_pct": s.spread_pct, "bid_depth_usd": s.bid_depth_usd,
                "ask_depth_usd": s.ask_depth_usd, "source": VENUE,
            }
            for s in snapshots
        ]).on_conflict_do_nothing(index_elements=["time", "symbol"])
        await self._session.execute(stmt)
        await self._session.commit()
        return len(snapshots)

    async def upsert_pairs(
        self, specs: list[VenuePairSpec], *, ambiguous: set[str]
    ) -> int:
        if not specs:
            return 0
        now = datetime.now(tz=UTC)
        stmt = insert(VenuePair).values([
            {
                "venue": VENUE, "symbol": s.symbol, "pair": s.pair,
                "ordermin": s.ordermin, "tradable": True,
                "ambiguous": s.symbol in ambiguous, "updated_at": now,
            }
            for s in specs
        ])
        stmt = stmt.on_conflict_do_update(
            index_elements=["venue", "symbol"],
            set_={
                "pair": stmt.excluded.pair, "ordermin": stmt.excluded.ordermin,
                "tradable": stmt.excluded.tradable,
                "ambiguous": stmt.excluded.ambiguous,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()
        return len(specs)

    async def mark_untradable(self, symbols: set[str]) -> int:
        """Record symbols we price but the venue does not list.

        Stored once as a fact rather than re-derived (and re-logged) every cycle:
        "no Kraken pair" is the answer to "why does this symbol never signal?",
        and Lot 3 reads it as the tradability filter.
        """
        if not symbols:
            return 0
        now = datetime.now(tz=UTC)
        stmt = insert(VenuePair).values([
            {
                "venue": VENUE, "symbol": s, "pair": "", "ordermin": None,
                "tradable": False, "ambiguous": False, "updated_at": now,
            }
            for s in sorted(symbols)
        ])
        stmt = stmt.on_conflict_do_update(
            index_elements=["venue", "symbol"],
            set_={"tradable": stmt.excluded.tradable,
                  "updated_at": stmt.excluded.updated_at},
        )
        await self._session.execute(stmt)
        await self._session.commit()
        return len(symbols)

    async def report_health(self, *, interval: str, candles: int) -> None:
        """Publish sweep freshness to service_health, which /systems/overview reads.

        The two sweepers share one row, so `detail` is merged with the JSONB `||`
        operator rather than replaced — otherwise each loop would erase the
        other's timestamp and both would look stale half the time.
        """
        now = datetime.now(tz=UTC)
        detail = {f"last_sweep_{interval}": now.isoformat(), f"candles_{interval}": candles}
        stmt = insert(ServiceHealth).values(
            service=SERVICE, status="healthy", healthy=True,
            latency_ms=0.0, detail=detail, checked_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["service"],
            set_={
                "status": stmt.excluded.status,
                "healthy": stmt.excluded.healthy,
                "checked_at": stmt.excluded.checked_at,
                "detail": ServiceHealth.detail.op("||")(stmt.excluded.detail),
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def last_candle_epoch(self, symbol: str, interval: str) -> int | None:
        """Unix seconds of the newest stored candle — the OHLC `since` cursor."""
        from sqlalchemy import select

        stmt = (
            select(Candle.time)
            .where(Candle.symbol == symbol, Candle.interval == interval)
            .order_by(Candle.time.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return int(row.timestamp()) if row else None
```

- [ ] **Step 2: Write the sweeper**

Create `services/collector-kraken/app/application/sweeper.py`:

```python
"""The two poll loops: 1h over the whole universe, 15m over the majors.

Both share one Kraken quota through Cache.allow(), the same token bucket the
content collectors use. Depth is measured on the broad loop only — the book is
about tradability, which does not need a 5-minute refresh.
"""

from __future__ import annotations

import asyncio
import logging

from cmi_common.cache import Cache
from cmi_common.db.session import Database
from cmi_common.observability import UPSTREAM_REQUESTS

from ..domain.mapper import parse_depth, parse_ohlc
from ..domain.pairs import parse_asset_pairs
from ..infrastructure.repository import KrakenRepository
from .universe import (
    ambiguous_symbols,
    intersect,
    mention_counts,
    priced_symbols,
    split_regimes,
    token_symbol_ranks,
    untradable,
)

logger = logging.getLogger(__name__)
SERVICE = "collector-kraken"

_INTERVAL_MINUTES = {"1h": 60, "15m": 15}


class CandleSweeper:
    def __init__(
        self,
        client,
        db: Database,
        cache: Cache,
        *,
        interval: str,
        cadence: float,
        majors_only: bool,
        with_depth: bool,
        min_mentions: int,
        sleep=asyncio.sleep,
    ) -> None:
        self._client = client
        self._db = db
        self._cache = cache
        self._interval = interval
        self._cadence = cadence
        self._majors_only = majors_only
        self._with_depth = with_depth
        self._min_mentions = min_mentions
        self._sleep = sleep

    async def _resolve(self) -> list:
        async with self._db.sessionmaker() as session:
            priced = await priced_symbols(session)
            listed = parse_asset_pairs(await self._client.asset_pairs())
            specs = intersect(listed, priced)
            majors, _ = split_regimes(
                specs,
                mentions=await mention_counts(session),
                min_mentions=self._min_mentions,
            )
            repo = KrakenRepository(session)
            await repo.upsert_pairs(
                specs, ambiguous=ambiguous_symbols(await token_symbol_ranks(session))
            )
            await repo.mark_untradable(untradable(listed, priced))
        return majors if self._majors_only else specs

    async def run(self) -> None:
        max_calls, window = self._client.rate_limit
        minutes = _INTERVAL_MINUTES[self._interval]
        while True:
            try:
                specs = await self._resolve()
                fetched = 0
                for spec in specs:
                    if not await self._cache.allow(self._client.name, max_calls, window):
                        await self._sleep(window)
                    async with self._db.sessionmaker() as session:
                        repo = KrakenRepository(session)
                        since = await repo.last_candle_epoch(spec.symbol, self._interval)
                        payload = await self._client.ohlc(
                            spec.pair, interval_minutes=minutes, since=since
                        )
                        fetched += await repo.upsert_candles(
                            parse_ohlc(
                                payload, symbol=spec.symbol, interval=self._interval
                            )
                        )
                        if self._with_depth:
                            snap = parse_depth(
                                await self._client.depth(spec.pair), symbol=spec.symbol
                            )
                            if snap is not None:
                                await repo.insert_depth([snap])
            except Exception:
                UPSTREAM_REQUESTS.labels(SERVICE, "kraken", "error").inc()
                logger.warning("%s sweep failed; backing off", self._interval,
                               exc_info=True)
                await self._sleep(60.0)
                continue
            UPSTREAM_REQUESTS.labels(SERVICE, "kraken", "ok").inc()
            # Freshness goes to service_health, which /systems/overview already
            # reads: a dead collector must show up red in the terminal rather
            # than let every reader keep computing on frozen candles.
            async with self._db.sessionmaker() as session:
                await KrakenRepository(session).report_health(
                    interval=self._interval, candles=fetched
                )
            logger.info("%s sweep stored %d candles", self._interval, fetched)
            await self._sleep(self._cadence)
```

- [ ] **Step 3: Write the service entrypoint**

Create `services/collector-kraken/app/main.py`:

```python
"""collector-kraken: OHLC candles + order-book depth -> Postgres. No Kafka."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from cmi_common import Settings, create_app
from cmi_common.cache import Cache
from cmi_common.db.session import Database

from .application.sweeper import CandleSweeper
from .application.universe import DEFAULT_MIN_MENTIONS
from .infrastructure.kraken import KrakenPublicClient

BROAD_CADENCE = float(os.getenv("KRAKEN_BROAD_CADENCE", "900"))
MAJORS_CADENCE = float(os.getenv("KRAKEN_MAJORS_CADENCE", "300"))
MIN_MENTIONS = int(
    os.getenv("KRAKEN_MAJOR_MIN_MENTIONS_7D", str(DEFAULT_MIN_MENTIONS))
)


async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    db = Database(settings.db)
    client = KrakenPublicClient()
    sweepers = [
        CandleSweeper(
            client, db, cache, interval="1h", cadence=BROAD_CADENCE,
            majors_only=False, with_depth=True, min_mentions=MIN_MENTIONS,
        ),
        CandleSweeper(
            client, db, cache, interval="15m", cadence=MAJORS_CADENCE,
            majors_only=True, with_depth=False, min_mentions=MIN_MENTIONS,
        ),
    ]
    app.state.cache = cache
    app.state.db = db
    app.state.client = client
    app.state.tasks = [asyncio.create_task(s.run()) for s in sweepers]


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    for task in app.state.tasks:
        task.cancel()
    await asyncio.gather(*app.state.tasks, return_exceptions=True)
    await app.state.client.close()
    await app.state.db.dispose()
    await app.state.cache.close()


app = create_app("collector-kraken", on_startup=_startup, on_shutdown=_shutdown)
```

- [ ] **Step 4: Add the service to both compose files**

In `docker-compose.yml`, after the `collector-news` block, add:

```yaml
  collector-kraken:
    <<: *service-defaults
    build: { context: ., dockerfile: docker/Dockerfile, args: { SERVICE_PATH: services/collector-kraken } }
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    environment:
      <<: *common-env
      KRAKEN_BROAD_CADENCE: ${KRAKEN_BROAD_CADENCE:-900}
      KRAKEN_MAJORS_CADENCE: ${KRAKEN_MAJORS_CADENCE:-300}
      KRAKEN_MAJOR_MIN_MENTIONS_7D: ${KRAKEN_MAJOR_MIN_MENTIONS_7D:-10}
    labels:
      - traefik.enable=true
      - traefik.http.routers.krakencol.rule=Host(`kraken.cmi.localhost`)
      - traefik.http.routers.krakencol.entrypoints=websecure
      - traefik.http.routers.krakencol.tls=true
      - traefik.http.services.krakencol.loadbalancer.server.port=8000
```

Mirror the same service block in `docker-compose.vps.yml`, following the image/labels
convention already used there for `collector-news`. Add `collector-kraken` to the service
matrix in `.github/workflows/deploy.yml` alongside the other collectors.

- [ ] **Step 5: Verify the service boots and ingests**

Run:
```bash
docker compose up -d --build collector-kraken
docker compose logs -f collector-kraken | head -40
```
Expected: within ~5 minutes, lines of the form `1h sweep stored N candles` with N > 0.

Then:
```bash
docker compose exec postgres psql -U cmi -d cmi \
  -c "select interval, count(*), count(distinct symbol) from candles group by 1;" \
  -c "select tradable, count(*) from venue_pairs group by 1;" \
  -c "select service, healthy, detail from service_health where service='collector-kraken';"
```
Expected: non-zero rows for `1h`; `venue_pairs` holding **both** a `true` group (Kraken
pairs) and a `false` group (priced symbols Kraken does not list); and a `service_health`
row whose `detail` carries `last_sweep_1h`.

- [ ] **Step 6: Commit**

```bash
git add services/collector-kraken docker-compose.yml docker-compose.vps.yml .github/workflows/deploy.yml
git commit -m "feat(collector-kraken): OHLC and depth sweep loops"
```

---

### Task 8: Populate `tokens` from PriceEvent

**Files:**
- Modify: `libs/cmi_common/cmi_common/events/market.py`
- Modify: `services/collector-coingecko/app/domain/mapper.py:23-36`
- Modify: `services/api-gateway/app/persister.py:104-117`
- Test: `tests/test_tokens_persistence.py`

- [ ] **Step 1: Write the failing test**

```python
"""Token metadata upsert: what the persister writes, and how rarely."""

from __future__ import annotations

from service_modules import load_service_module

persister = load_service_module("api-gateway", "persister")
TokenMetaCache = persister.TokenMetaCache


def test_first_sighting_is_always_written():
    cache = TokenMetaCache(min_interval_s=3600)
    assert cache.should_write("bitcoin", ("Bitcoin", 1, False), now=0.0) is True


def test_unchanged_metadata_is_not_rewritten_within_the_interval():
    cache = TokenMetaCache(min_interval_s=3600)
    cache.should_write("bitcoin", ("Bitcoin", 1, False), now=0.0)
    assert cache.should_write("bitcoin", ("Bitcoin", 1, False), now=60.0) is False


def test_changed_metadata_is_written_immediately():
    """A token entering the trending set must not wait out the interval."""
    cache = TokenMetaCache(min_interval_s=3600)
    cache.should_write("bitcoin", ("Bitcoin", 1, False), now=0.0)
    assert cache.should_write("bitcoin", ("Bitcoin", 1, True), now=60.0) is True


def test_unchanged_metadata_is_refreshed_after_the_interval():
    cache = TokenMetaCache(min_interval_s=3600)
    cache.should_write("bitcoin", ("Bitcoin", 1, False), now=0.0)
    assert cache.should_write("bitcoin", ("Bitcoin", 1, False), now=3601.0) is True


def test_tokens_are_tracked_independently():
    cache = TokenMetaCache(min_interval_s=3600)
    cache.should_write("bitcoin", ("Bitcoin", 1, False), now=0.0)
    assert cache.should_write("ethereum", ("Ethereum", 2, False), now=1.0) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tokens_persistence.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'TokenMetaCache'`.

- [ ] **Step 3: Add the `name` field to PriceEvent**

In `libs/cmi_common/cmi_common/events/market.py`, add to `PriceEvent` right after `coin_id`:

```python
    name: str | None = Field(default=None, description="Display name, e.g. 'Solana'")
```

In `services/collector-coingecko/app/domain/mapper.py`, add to the `to_price_event` call
right after `coin_id=row["id"],`:

```python
        name=str(row.get("name") or "") or None,
```

- [ ] **Step 4: Write the persister change**

In `services/api-gateway/app/persister.py`, add `Token` to the `cmi_common.db` import, add
`time` to the stdlib imports, and insert this class above the persister class:

```python
class TokenMetaCache:
    """Throttle for token reference writes.

    221 price events land every minute carrying metadata that changes about once
    a day. Writing on each one is pure write amplification, so a write happens
    only when the metadata actually changed, or once per interval to heal drift.
    """

    def __init__(self, min_interval_s: float = 3600.0) -> None:
        self._min_interval = min_interval_s
        self._seen: dict[str, tuple[tuple, float]] = {}

    def should_write(self, coin_id: str, meta: tuple, *, now: float) -> bool:
        previous = self._seen.get(coin_id)
        if previous is not None:
            last_meta, last_at = previous
            if last_meta == meta and now - last_at < self._min_interval:
                return False
        self._seen[coin_id] = (meta, now)
        return True
```

Instantiate it in the persister's `__init__` (`self._token_meta = TokenMetaCache()`), and
add this method, calling it from the end of `_save_price`:

```python
    async def _save_token(self, e: PriceEvent) -> None:
        meta = (e.name, e.market_cap_rank, e.is_trending)
        if not self._token_meta.should_write(e.coin_id, meta, now=time.monotonic()):
            return
        async with self._db._sessionmaker() as s:
            stmt = insert(Token).values(
                symbol=e.symbol,
                coin_id=e.coin_id,
                name=e.name,
                metadata_={
                    "market_cap_rank": e.market_cap_rank,
                    "is_trending": e.is_trending,
                },
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["coin_id"],
                set_={
                    "symbol": stmt.excluded.symbol,
                    "name": stmt.excluded.name,
                    "metadata": stmt.excluded.metadata,
                },
            )
            await s.execute(stmt)
            await s.commit()
```

At the end of `_save_price`, after `await s.commit()`, add `await self._save_token(e)`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_tokens_persistence.py -v`
Expected: PASS, 5 passed.

- [ ] **Step 6: Verify against a running stack**

Run:
```bash
docker compose up -d --build api-gateway collector-coingecko
sleep 120
docker compose exec postgres psql -U cmi -d cmi -c "select symbol, name, metadata from tokens limit 5;"
```
Expected: rows with real names (`Bitcoin`, not `BTC`) and a populated `metadata` JSONB.

- [ ] **Step 7: Commit**

```bash
git add libs/cmi_common/cmi_common/events/market.py services/collector-coingecko services/api-gateway/app/persister.py tests/test_tokens_persistence.py
git commit -m "feat(api-gateway): populate tokens from PriceEvent metadata"
```

---

### Task 9: Rewire `/market/news` onto `raw_content`

**Files:**
- Modify: `services/api-gateway/app/read_api.py:159-171` (`map_news`), `:336-343` (`market_news`)
- Test: `tests/test_api_gateway_read.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_gateway_read.py`:

```python
def test_map_news_reads_raw_content_columns():
    """raw_content.published_at is timestamptz, unlike the old epoch BigInteger."""
    row = SimpleNamespace(
        id=42,
        title="ETF approved",
        url="https://example.com/a",
        source="rss",
        symbols=["BTC"],
        sentiment_score=0.42,
        published_at=datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
    )
    out = map_news(row)
    assert out["id"] == "42"
    assert out["title"] == "ETF approved"
    assert out["source"] == "rss"
    assert out["symbols"] == ["BTC"]
    assert out["sentiment"] == 0.42
    assert out["published_at"] == "2026-07-29T10:00:00+00:00"


def test_map_news_tolerates_unscored_and_untagged_rows():
    row = SimpleNamespace(
        id=7, title="t", url="u", source="gdelt",
        symbols=None, sentiment_score=None, published_at=None,
    )
    out = map_news(row)
    assert out["symbols"] == []
    assert out["sentiment"] == 0.0
    assert out["published_at"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_gateway_read.py -k map_news -v`
Expected: FAIL — `AttributeError: 'SimpleNamespace' object has no attribute 'source_name'`.

- [ ] **Step 3: Rewrite the mapper and the query**

Replace `map_news` in `services/api-gateway/app/read_api.py` with:

```python
def map_news(row: Any) -> dict:
    """A raw_content news row as the terminal's NewsItem.

    Sourced from raw_content, not the `news` table: collector-news has always
    written the former, and the latter has never had a writer at all.
    """
    return {
        "id": str(row.id),
        "title": row.title,
        "url": row.url,
        "source": row.source,
        "symbols": list(row.symbols or []),
        "sentiment": _num(row.sentiment_score),
        "published_at": _iso(row.published_at),
    }
```

Replace the body of `market_news` with:

```python
@router.get("/market/news")
async def market_news(
    limit: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(get_session_dep),
) -> list[dict]:
    stmt = (
        select(RawContent)
        .where(RawContent.kind == "news")
        .order_by(RawContent.published_at.desc().nullslast())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [map_news(r) for r in rows]
```

Remove `News` from the `cmi_common.db` import at the top of the file.

- [ ] **Step 4: Run the full read test module**

Run: `python -m pytest tests/test_api_gateway_read.py tests/test_read_contract.py -v`
Expected: PASS — including the untouched contract parity test.

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/read_api.py tests/test_api_gateway_read.py
git commit -m "fix(read-api): serve /market/news from raw_content"
```

---

### Task 10: Rewire token sentiment, liquidity and metadata

**Files:**
- Modify: `services/api-gateway/app/read_api.py:126-148` (`map_token`), `:294-317`
- Test: `tests/test_api_gateway_read.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_gateway_read.py`:

```python
def _price_row(symbol="BTC", change=1.0):
    return SimpleNamespace(
        symbol=symbol, price_usd=100.0, market_cap_usd=1000.0,
        volume_24h_usd=500.0, price_change_pct_24h=change,
        time=datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
    )


def test_map_token_uses_measured_liquidity_when_present():
    out = map_token(_price_row(), liquidity_usd=250_000.0)
    assert out["liquidity_usd"] == 250_000.0


def test_map_token_reports_zero_liquidity_when_unmeasured():
    """The TS contract requires the key; None must not leak to the wire."""
    out = map_token(_price_row(), liquidity_usd=None)
    assert out["liquidity_usd"] == 0.0


def test_map_token_carries_sentiment_through():
    out = map_token(_price_row(), sentiment_score=0.383)
    assert out["sentiment_score"] == 0.38


def test_map_token_prefers_the_real_trending_flag_over_the_heuristic():
    meta = SimpleNamespace(
        coin_id="bitcoin", name="Bitcoin", metadata_={"is_trending": True}
    )
    out = map_token(_price_row(change=0.1), meta=meta)
    assert out["name"] == "Bitcoin"
    assert out["coin_id"] == "bitcoin"
    assert out["is_trending"] is True


def test_map_token_falls_back_to_the_change_heuristic_without_metadata():
    assert map_token(_price_row(change=7.0))["is_trending"] is True
    assert map_token(_price_row(change=1.0))["is_trending"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_gateway_read.py -k map_token -v`
Expected: FAIL — `map_token` takes no `liquidity_usd` argument.

- [ ] **Step 3: Rewrite `map_token` and the query**

Replace `map_token` in `read_api.py` with:

```python
def map_token(
    price: Any,
    *,
    meta: Any | None = None,
    opportunity_score: float | None = None,
    sentiment_score: float | None = None,
    liquidity_usd: float | None = None,
) -> dict:
    """Build a MarketToken from the latest price row (+ optional enrichments).

    `liquidity_usd` is None when the book was never measured — a symbol Kraken
    does not list, or one the sweep has not reached yet. The TS contract requires
    the key, so it is coerced to 0.0 here, at the HTTP edge and nowhere else:
    downstream scoring must keep "unmeasured" distinct from "thin".
    """
    change = price.price_change_pct_24h
    meta_extra = getattr(meta, "metadata_", None) or {}
    trending = meta_extra.get("is_trending")
    return {
        "symbol": price.symbol,
        "coin_id": getattr(meta, "coin_id", None) or price.symbol.lower(),
        "name": getattr(meta, "name", None) or price.symbol,
        "price_usd": _num(price.price_usd),
        "price_change_pct_24h": _num(change),
        "volume_24h_usd": _num(price.volume_24h_usd),
        "liquidity_usd": _num(liquidity_usd),
        "market_cap_usd": _num(price.market_cap_usd),
        "sentiment_score": round(sentiment_score, 2) if sentiment_score is not None else 0.0,
        "opportunity_score": round(_num(opportunity_score) / 100, 2) if opportunity_score else 0.0,
        "is_trending": bool(trending) if trending is not None
        else bool(change is not None and change >= 5),
        "updated_at": _iso(price.time),
    }
```

Add this helper above `market_tokens`:

```python
async def _sentiment_by_symbol(
    session: AsyncSession, *, hours: int = 24
) -> dict[str, float]:
    """Confidence-weighted mean sentiment per symbol over the window, in one query.

    Reads content_sentiment_agg, which sentiment-service actually maintains. The
    former source (`sentiments`) had no writer and returned 0.0 for every token.
    """
    since = _utcnow() - timedelta(hours=hours)
    stmt = (
        select(
            ContentSentimentAgg.symbol,
            func.sum(ContentSentimentAgg.weighted_score_sum),
            func.sum(ContentSentimentAgg.confidence_sum),
        )
        .where(ContentSentimentAgg.bucket_start >= since)
        .group_by(ContentSentimentAgg.symbol)
    )
    out: dict[str, float] = {}
    for symbol, weighted, confidence in (await session.execute(stmt)).all():
        if confidence:
            out[symbol] = float(weighted) / float(confidence)
    return out
```

Replace `market_tokens` with:

```python
@router.get("/market/tokens")
async def market_tokens(session: AsyncSession = Depends(get_session_dep)) -> list[dict]:
    prices = (await session.execute(_latest_per_symbol(Price, Price.price_usd, Price.time))).scalars().all()
    tokens = (await session.execute(select(Token))).scalars().all()
    meta = {t.symbol: t for t in tokens}
    sigs = (await session.execute(_latest_per_symbol(Signal, Signal.opportunity_score, Signal.time))).scalars().all()
    opp = {s.symbol: s.opportunity_score for s in sigs}
    sent = await _sentiment_by_symbol(session)
    depth = await SqlCandleReader(session).latest_depth(
        symbols=[p.symbol for p in prices]
    )
    return [
        map_token(
            p,
            meta=meta.get(p.symbol),
            opportunity_score=opp.get(p.symbol),
            sentiment_score=sent.get(p.symbol),
            liquidity_usd=(
                float(depth[p.symbol].total_depth_usd) if p.symbol in depth else None
            ),
        )
        for p in prices
    ]
```

Replace `market_token` (the single-symbol endpoint) so it carries the same enrichments:

```python
@router.get("/market/tokens/{symbol}")
async def market_token(symbol: str, session: AsyncSession = Depends(get_session_dep)) -> dict:
    sym = symbol.upper()
    stmt = select(Price).where(Price.symbol == sym).order_by(Price.time.desc()).limit(1)
    price = (await session.execute(stmt)).scalars().first()
    if price is None:
        return {}
    meta = (await session.execute(select(Token).where(Token.symbol == sym))).scalars().first()
    sent = await _sentiment_by_symbol(session)
    depth = await SqlCandleReader(session).latest_depth(symbols=[sym])
    return map_token(
        price,
        meta=meta,
        sentiment_score=sent.get(sym),
        liquidity_usd=float(depth[sym].total_depth_usd) if sym in depth else None,
    )
```

Update the imports at the top of `read_api.py`: remove `Sentiment`, add
`from cmi_common.db.models import ContentSentimentAgg, PipelineRejection, RawContent` and
`from cmi_common.sources import SqlCandleReader, SqlSentimentAggReader`.

- [ ] **Step 4: Run the read and contract tests**

Run: `python -m pytest tests/test_api_gateway_read.py tests/test_read_contract.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/read_api.py tests/test_api_gateway_read.py
git commit -m "fix(read-api): real sentiment, measured liquidity and token metadata"
```

---

### Task 11: Drop the dead tables

**Files:**
- Modify: `libs/cmi_common/cmi_common/db/models.py`, `libs/cmi_common/cmi_common/db/__init__.py`
- Create: `migrations/alembic/versions/0014_drop_dead_tables.py`

- [ ] **Step 1: Confirm nothing references them**

Run: `grep -rn "News\b\|Sentiment(" --include=*.py services libs | grep -v "SentimentEvent\|ContentSentiment\|SqlSentiment\|NewsItem\|collector-news"`
Expected: only the model definitions in `models.py` and the exports in `db/__init__.py`.
If anything else appears, stop and rewire it first.

- [ ] **Step 2: Remove the models**

Delete the `News` and `Sentiment` classes from `libs/cmi_common/cmi_common/db/models.py`,
and remove both names from the imports and `__all__` in
`libs/cmi_common/cmi_common/db/__init__.py`.

- [ ] **Step 3: Write the migration**

Create `migrations/alembic/versions/0014_drop_dead_tables.py`:

```python
"""drop the writer-less `news` and `sentiments` tables

Both were declared, imported and read, but no code ever wrote either. Their only
effect was to make /market/news and every token's sentiment_score look like real,
empty data — a shape-conformant lie the contract test could not catch.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("news")
    op.drop_table("sentiments")


def downgrade() -> None:
    op.create_table(
        "sentiments",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("input_kind", sa.String(16), primary_key=True),
        sa.Column("sentiment_score", sa.Float, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
    )
    op.create_table(
        "news",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("article_id", sa.String(128), unique=True, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("source_name", sa.String(128), nullable=False),
        sa.Column("published_at", sa.BigInteger, nullable=False),
        sa.Column("symbols", postgresql.JSONB, server_default="[]"),
        sa.Column("provider_sentiment", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
```

- [ ] **Step 4: Verify the whole suite and the migration**

Run:
```bash
python -m pytest tests/ -q
docker compose run --rm api-gateway alembic upgrade head
docker compose exec postgres psql -U cmi -d cmi -c "\dt news"
```
Expected: suite green; `\dt news` reports `Did not find any relation named "news"`.

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/db migrations/alembic/versions/0014_drop_dead_tables.py
git commit -m "chore(db): drop the writer-less news and sentiments tables"
```

---

### Task 12: Plausibility assertions in the live harness

**Files:**
- Modify: `scripts/verify_read_live.py`

`_check` currently returns `[]` for an empty list, with the comment "empty collection is
shape-less but reachable; OK". That single line is why `/market/news` could return `[]`
in production for weeks while every test passed. Shape conformance is necessary and not
sufficient.

- [ ] **Step 1: Add the plausibility layer**

In `scripts/verify_read_live.py`, add above `main()`:

```python
#: Endpoints that must not merely conform — they must carry data. Each entry is
#: (endpoint name, predicate over the response, failure message). A shape-only
#: check passed happily on two empty tables for weeks; this is the layer that
#: would have caught it.
def _has_rows(resp) -> bool:
    return bool(resp)


def _any_sentiment(resp) -> bool:
    return any(t.get("sentiment_score") for t in resp or [])


def _any_liquidity(resp) -> bool:
    return any(t.get("liquidity_usd") for t in resp or [])


def _real_names(resp) -> bool:
    """A name equal to its own symbol means the tokens table is still empty."""
    return any(t.get("name") != t.get("symbol") for t in resp or [])


PLAUSIBILITY = {
    "market/news": [(_has_rows, "no news rows — is collector-news writing raw_content?")],
    "market/tokens": [
        (_has_rows, "no tokens"),
        (_any_sentiment, "every sentiment_score is 0 — check content_sentiment_agg"),
        (_any_liquidity, "every liquidity_usd is 0 — is collector-kraken running?"),
        (_real_names, "every name equals its symbol — tokens table not populated"),
    ],
}
```

Replace the loop body in `main()` so plausibility runs after the shape check:

```python
            missing = _check(name, res)
            if missing:
                print(f"FAIL {name}  ->  missing {missing}")
                failures.append(name)
                continue
            broken = [msg for pred, msg in PLAUSIBILITY.get(name, []) if not pred(res)]
            if broken:
                for msg in broken:
                    print(f"THIN {name}  ->  {msg}")
                failures.append(name)
                continue
            print(f"OK   {name}  ->  {str(res)[:120]}")
```

Also fix the misleading comment on line 26 of `_check`: replace
`return []  # empty collection is shape-less but reachable; OK` with
`return []  # shape cannot be checked on an empty list; PLAUSIBILITY covers it`.

- [ ] **Step 2: Run the harness against the local stack**

Run:
```bash
docker compose up -d
docker compose exec api-gateway python /app/scripts/verify_read_live.py
```
Expected: every line `OK`, exit 0. A `THIN` line means the shape is right but the data is
absent — which is a real failure, not a warning.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_read_live.py
git commit -m "test(read-plane): assert data plausibility, not just response shape"
```

---

### Task 13: Deploy and verify against production

- [ ] **Step 1: Run the local gate**

`make lint` cannot be the gate here: it runs `black --check libs services`, and ~40 files
on `master` were already unformatted before this lot began (measured 2026-07-31). Gating on
it would either block on unrelated debt or tempt a reformat-everything commit buried inside
a feature branch. Gate on the surface this lot actually touches, plus the whole suite:

```bash
python -m ruff check libs services
python -m black --check services/collector-kraken libs/cmi_common/cmi_common/sources/candles.py \
  services/api-gateway/app/read_api.py services/api-gateway/app/persister.py \
  scripts/verify_read_live.py tests/test_kraken_*.py tests/test_candle_reader.py \
  tests/test_tokens_persistence.py
python -m pytest tests/ -q
```

Expected: ruff clean, black clean on the listed files, suite green with no regression against
the 558-test baseline this branch started from.

Reformatting the pre-existing ~40 files is legitimate work, but it belongs in its own commit
on its own branch — never mixed into a feature diff, where it would bury the real changes.

- [ ] **Step 2: Deploy**

```bash
git push origin <branch>
```
Merging to `master` triggers `.github/workflows/deploy.yml`, which builds every service to
GHCR and redeploys the VPS.

- [ ] **Step 3: Apply the migrations on the VPS**

```bash
ssh <VPS_USER>@<VPS_HOST> "cd /opt/bottrading && docker compose -f docker-compose.vps.yml run --rm api-gateway alembic upgrade head"
```
Expected: `Running upgrade 0012 -> 0013`, then `0013 -> 0014`.

- [ ] **Step 4: Verify the eight spec success criteria**

```bash
ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-api-gateway-1 python /app/scripts/verify_read_live.py"
ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-postgres-1 psql -U cmi -d cmi \
  -c \"select interval, count(*), count(distinct symbol) from candles group by 1;\" \
  -c \"select count(*), count(*) filter (where tradable) from venue_pairs;\" \
  -c \"select service, checked_at from service_health where service='collector-kraken';\" \
  -c \"\\dt news\""
```
Expected: harness all-`OK`; non-zero candle counts for both intervals; `venue_pairs`
populated; `collector-kraken` health fresh; `news` relation absent.

- [ ] **Step 5: Commit any fixes and close out**

If verification exposes a gap, fix it, commit, and re-run Step 4 before declaring the lot
done. Do not mark it complete on a partial pass.
