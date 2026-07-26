# Persistance et pagination du flux — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Le flux d'événements du Command Center survit à un changement de page — il est archivé en base, rechargeable et paginé, au lieu de vivre uniquement dans un tampon mémoire de 200 entrées.

**Architecture:** Un `EventArchiver` distinct du `Persister` archive le flux brut dans deux hypertables séparées par leur rétention (7 j pour le marché, 90 j pour le signal). Un endpoint `GET /events` les fusionne derrière une pagination par curseur composite. Le frontend combine cet historique avec le flux WebSocket temps réel, dédupliqué par `event_id`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async, aiokafka, Alembic, TimescaleDB, FastAPI, Next.js 14 + react-query.

**Spec:** `docs/superpowers/specs/2026-07-25-command-center-pipeline-kraken-events-design.md` — section « Phase 3 »

---

## Pourquoi ce chantier

C'est la demande d'origine de l'opérateur, formulée avant tout le reste :

> *« j'ai le flux d'événements en temps réel qui est génial mais aucune data ne semble persister, si je quitte la page j'ai plus rien, faudrait avoir une pagination et stocker cela en db »*

Diagnostic confirmé : `frontend/src/lib/ws/WebSocketProvider.tsx:29-44` garde le flux
dans un `useState` plafonné à `MAX_FEED = 200`. Rien n'est écrit. En mode mock un
backfill (`/api/mock/stream/recent?since=0`) donne l'illusion de persistance ; en
live il n'existe pas.

Côté base, `persister.py` ne conserve que cinq types projetés vers des tables
**métier**. Sentiment, Volume et Dex ne sont persistés nulle part.

---

## Contexte indispensable

### Faits vérifiés contre la production (2026-07-26)

- TimescaleDB **2.15.3**, session en UTC, compression désactivée sur toutes les hypertables.
- **`UNIQUE (event_id)` seul est impossible** sur une hypertable. Vérifié :
  `ERROR: cannot create a unique index without the column "time" (used in partitioning)`.
  D'où `PRIMARY KEY (time, event_id)`, comme `decision_journal` et
  `pipeline_rejections`. C'est équivalent pour l'idempotence Kafka : un message
  redélivré porte un événement sérialisé identique, donc les deux colonnes le sont.
- Toutes les hypertables du repo sont en **`TIMESTAMPTZ`**, malgré ce que
  prétendent certains commentaires. Le persister écrit via `_naive_utc` et cela
  fonctionne parce que la session est en UTC — suivre cette convention.
- `alembic_version` est à **0009**. Ce plan pose **0010** et **0011**.
- Volume actuel : ~12 000 analyses / 24 h, 211 symboles dans `prices`, un point
  de prix toutes les ~64 s par symbole.

### Conventions du projet

- Événements Pydantic v2 dans `libs/cmi_common/cmi_common/events/`, `BaseEvent`
  en `extra="forbid"` et `frozen=True`.
- Topics : `Topic` + `TOPIC_EVENT` + `TOPIC_PARTITIONS`, **les trois ensemble** —
  `tests/test_journal_topic.py::test_every_topic_appears_in_both_tables` le vérifie.
- Tout ajout au plan de lecture entre dans `read_contract.py` **et** reçoit une
  assertion dans `tests/test_read_contract.py`, faute de quoi le test de
  couverture du manifeste échoue.
- Tests à plat dans `tests/`. **Jamais** de module de service chargé sous un nom
  commençant par `app.` — `tests/conftest.py` fait échouer la collecte. Utiliser
  `from service_modules import load_service_module` (deux arguments).
- `asyncio_mode = "auto"`.

### Échecs pré-existants — ne pas corriger, ne pas signaler comme nouveaux

`tests/test_bluesky_provider.py` (×2) et `tests/test_raw_content_model.py` (×1).
Référence de départ : **3 failed, 357 passed, 2 skipped**.

---

## Structure des fichiers

**Créés**

| Fichier | Responsabilité |
|---|---|
| `migrations/alembic/versions/0010_events_market.py` | hypertable marché, rétention 7 j |
| `migrations/alembic/versions/0011_events_signal.py` | hypertable signal, rétention 90 j |
| `services/api-gateway/app/archiver.py` | routage + écriture, **distinct du Persister** |
| `services/api-gateway/app/events_cursor.py` | curseur composite — **pur, sans base** |
| `services/api-gateway/app/events_api.py` | routeur `GET /events` |
| `frontend/src/lib/hooks/useEventFeed.ts` | fusion historique + live |
| `frontend/src/app/api/mock/events/route.ts` | route mock |

**Modifiés**

| Fichier | Changement |
|---|---|
| `libs/cmi_common/cmi_common/db/models.py` | `EventMarket`, `EventSignal` |
| `libs/cmi_common/cmi_common/db/__init__.py` | exports |
| `services/api-gateway/app/main.py` | consumer de l'archiveur + montage du routeur |
| `services/api-gateway/app/read_contract.py` | contrat de `events` |
| `tests/test_read_contract.py` | assertion |
| `frontend/src/lib/types/events.ts` | `EventPage` |
| `frontend/src/lib/api/endpoints.ts` | `eventsApi.page` |
| `frontend/src/components/command/LiveEventStream.tsx` | branché sur le hook, filtres repris de `LiveFeed` |
| `frontend/src/components/realtime/LiveFeed.tsx` | supprimé (fusionné) |

**Décision retenue : `journal.entries` est exclu de l'archivage.** Le journal a
déjà sa table et 180 jours de rétention ; l'archiver doublerait le stockage de la
table la plus volumineuse du système sans rien apporter.

---

## Task 1 : migrations et modèles

**Files:**
- Create: `migrations/alembic/versions/0010_events_market.py`
- Create: `migrations/alembic/versions/0011_events_signal.py`
- Modify: `libs/cmi_common/cmi_common/db/models.py`
- Modify: `libs/cmi_common/cmi_common/db/__init__.py`

- [ ] **Step 1: Write `0010_events_market.py`**

```python
"""events_market hypertable

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events_market",
        # `time` is in the primary key because TimescaleDB refuses any unique
        # index that omits the partitioning column -- verified against the
        # production database. (time, event_id) is equally effective for Kafka
        # idempotence: a redelivered message carries an identical serialized
        # event, so both columns match.
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("topic", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32)),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_events_market_page", "events_market", ["time", "event_id"],
        postgresql_using="btree",
    )
    op.create_index("ix_events_market_symbol", "events_market", ["symbol", "time"])
    op.create_index(
        "ix_events_market_correlation", "events_market", ["correlation_id"],
        postgresql_where=sa.text("correlation_id IS NOT NULL"),
    )
    op.execute(
        "SELECT create_hypertable('events_market', 'time', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('events_market', INTERVAL '7 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    # `if_exists`, not `if_not_exists`: the latter belongs to
    # add_retention_policy, and the wrong keyword raises before the DROP TABLE.
    op.execute("SELECT remove_retention_policy('events_market', if_exists => TRUE)")
    op.drop_table("events_market")
```

- [ ] **Step 2: Write `0011_events_signal.py`**

Identical, with these substitutions: `revision = "0011"`, `down_revision = "0010"`,
table `events_signal`, index prefixes `ix_events_signal_*`, retention
`INTERVAL '90 days'`.

The two tables share a schema on purpose — they are separated **only** by
retention, because `add_retention_policy` drops whole chunks by time and cannot
filter by event type. A single table with differentiated retention would need a
scheduled `DELETE ... WHERE event_type IN (...)`, a costly scan on a hot table
where a chunk drop is nearly free.

- [ ] **Step 3: Add the ORM models**

In `libs/cmi_common/cmi_common/db/models.py`, after `DecisionJournal`:

```python
class _EventArchiveMixin:
    """Raw broadcast-stream archive. Two tables share this shape and differ only
    in retention: TimescaleDB drops whole chunks by time and cannot filter by
    event type, so differentiated retention requires separate hypertables."""

    time: Mapped[datetime] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32))
    topic: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str | None] = mapped_column(String(32), default=None)
    correlation_id: Mapped[str | None] = mapped_column(String(64), default=None)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class EventMarket(Base, _EventArchiveMixin):
    """Price, volume and dex events -- high volume, 7-day retention."""

    __tablename__ = "events_market"


class EventSignal(Base, _EventArchiveMixin):
    """Sentiment, analysis, decision, risk and execution events -- low volume,
    90-day retention, because these are the ones worth looking back at."""

    __tablename__ = "events_signal"
```

Register both in `HYPERTABLES`: `"events_market": "time"`, `"events_signal": "time"`.
Export `EventMarket` and `EventSignal` from `db/__init__.py` (both the import list
and `__all__`, following the file's existing ordering).

**Verify the mixin actually works with SQLAlchemy's declarative mapping** before
proceeding — `Mapped` columns on a non-`Base` mixin require the columns to be
declared with `declared_attr` in some SQLAlchemy versions. Run:

```
python -c "from cmi_common.db import EventMarket, EventSignal; print([c.name for c in EventMarket.__table__.columns]); print([c.name for c in EventSignal.__table__.primary_key])"
```

If the mixin does not produce independent columns per table, drop it and declare
both classes in full — duplication is cheaper than a subtle mapping bug, and say
so in your report.

- [ ] **Step 4: Validate offline**

```
cd migrations && python -m alembic upgrade head --sql
```

Confirm for **both** steps: `PRIMARY KEY (time, event_id)`, three indexes,
`create_hypertable` after the CREATE TABLE, `add_retention_policy` with the right
interval, and the `alembic_version` bump. Paste the emitted SQL for `0010` and
`0011` verbatim.

Then `python -m alembic downgrade 0009 --sql` and confirm both drops round-trip.

- [ ] **Step 5: Run the suite**

`python -m pytest tests/ -rN --tb=no` → 3 failed (known), rest passing.
`python -m ruff check libs/cmi_common migrations` → no new finding category.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(db): events_market and events_signal archive hypertables

Two tables sharing one schema, separated only by retention: TimescaleDB drops
whole chunks by time and cannot filter by event type, so differentiated
retention on one table would mean a scheduled DELETE scan on a hot table where a
chunk drop is nearly free.

PRIMARY KEY (time, event_id) rather than UNIQUE(event_id), which TimescaleDB
rejects on a hypertable -- verified against production. Equally effective for
Kafka idempotence, since a redelivered message carries an identical event.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" -- migrations/alembic/versions/0010_events_market.py migrations/alembic/versions/0011_events_signal.py libs/cmi_common/cmi_common/db/models.py libs/cmi_common/cmi_common/db/__init__.py
```

---

## Task 2 : routage de l'archiveur (pur)

**Files:**
- Create: `services/api-gateway/app/archiver.py`
- Test: `tests/test_archiver_routing.py`

- [ ] **Step 1: Write the failing test**

```python
"""Routage des événements vers l'une des deux tables d'archive."""

from __future__ import annotations

from cmi_common.events import (
    AnalysisEvent, DecisionEvent, DexEvent, PriceEvent, SentimentEvent, VolumeEvent,
)
from cmi_common.events.base import Source
from cmi_common.events.execution import ExecutionEvent, ExecutionKind
from cmi_common.events.journal import JournalEntryEvent
from cmi_common.events.risk import RiskRejectedEvent

from service_modules import load_service_module

arch = load_service_module("api-gateway", "archiver")


def test_market_events_route_to_the_market_table() -> None:
    """Prix, volume et dex : gros volume, rétention courte."""
    for ev in (
        PriceEvent(source=Source.COINGECKO, symbol="BTC", coin_id="bitcoin",
                   price_usd=100.0),
        VolumeEvent(source=Source.COINGECKO, symbol="BTC", coin_id="bitcoin",
                    volume_24h_usd=1.0, volume_spike_ratio=3.0),
    ):
        assert arch.table_for(ev) is arch.MARKET, type(ev).__name__


def test_signal_events_route_to_the_signal_table() -> None:
    """Sentiment, analyse, décision, risque, exécution : faible volume, mais ce
    sont ceux qu'on relit."""
    for ev in (
        AnalysisEvent(symbol="BTC", opportunity_score=1, confidence=0.5, reason="r"),
        DecisionEvent(symbol="BTC", opportunity_score=1, confidence=0.5, rationale="r"),
        RiskRejectedEvent(source=Source.RISK_ENGINE, symbol="BTC", reason="x"),
        ExecutionEvent(kind=ExecutionKind.FILLED, symbol="BTC", risk_event_id="r1"),
    ):
        assert arch.table_for(ev) is arch.SIGNAL, type(ev).__name__


def test_journal_entries_are_not_archived() -> None:
    """Le journal a déjà sa table et 180 jours de rétention ; l'archiver
    doublerait le stockage de la table la plus volumineuse sans rien apporter."""
    ev = JournalEntryEvent(symbol="BTC", signal_event_id="s1", score=1,
                           confidence=0.5, factors_present=1)
    assert arch.table_for(ev) is None


def test_an_unknown_event_lands_in_signal_rather_than_being_dropped() -> None:
    """Un type non prévu doit rester visible. Le jeter ferait disparaître des
    événements sans trace, et la rétention longue est le choix prudent."""

    class Surprise(SentimentEvent):
        pass

    ev = Surprise(symbol="BTC", sentiment_score=0.0, confidence=0.5,
                  model_name="m", input_kind="news", sample_size=1)
    assert arch.table_for(ev) is arch.SIGNAL


def test_row_carries_the_full_payload_and_the_indexed_columns() -> None:
    """Les colonnes indexées sont extraites pour la requête ; le payload garde
    l'événement entier pour que rien ne soit perdu."""
    ev = PriceEvent(source=Source.COINGECKO, symbol="BTC", coin_id="bitcoin",
                    price_usd=100.0)
    row = arch.to_row(ev, topic="market.price.events")
    assert row["event_id"] == ev.event_id
    assert row["event_type"] == "PriceEvent"
    assert row["symbol"] == "BTC"
    assert row["topic"] == "market.price.events"
    assert row["payload"]["price_usd"] == 100.0
    assert row["time"].tzinfo is None  # naive UTC, convention du persister


def test_an_event_without_a_symbol_is_archived_with_a_null_symbol() -> None:
    """Tous les événements ne portent pas de symbole ; l'absence ne doit pas
    empêcher l'archivage."""
    ev = ExecutionEvent(kind=ExecutionKind.FILLED, symbol="BTC", risk_event_id="r1")
    row = arch.to_row(ev, topic="execution.events")
    assert row["symbol"] == "BTC"
```

- [ ] **Step 2: Run it, confirm the failure**

`python -m pytest tests/test_archiver_routing.py -v` → `FileNotFoundError` on `archiver.py`.

- [ ] **Step 3: Implement**

```python
"""Archive the raw broadcast stream, without interpreting it.

Deliberately separate from ``Persister``: that projects events into *business*
tables with a lifecycle (a RiskApprovedEvent becomes a Trade row that later gets
a fill price). This one stores events as they arrived so the Command Center feed
survives a page reload.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cmi_common.db import EventMarket, EventSignal
from cmi_common.events import BaseEvent, DexEvent, PriceEvent, VolumeEvent
from cmi_common.events.journal import JournalEntryEvent

MARKET = EventMarket
SIGNAL = EventSignal

# High-volume, short-retention. Everything else worth keeping goes to SIGNAL.
_MARKET_TYPES = (PriceEvent, VolumeEvent, DexEvent)


def table_for(event: BaseEvent) -> type | None:
    """Which archive table, or None when the event must not be archived.

    The journal has its own table and a 180-day retention; archiving it too
    would duplicate the largest table in the system for no gain.

    An unrecognised type lands in SIGNAL rather than being dropped: an event we
    did not anticipate must stay visible, and the longer retention is the
    prudent default.
    """
    if isinstance(event, JournalEntryEvent):
        return None
    if isinstance(event, _MARKET_TYPES):
        return MARKET
    return SIGNAL


def _naive_utc(dt: datetime) -> datetime:
    """Naive UTC, matching persister._naive_utc. The columns are TIMESTAMPTZ and
    the session runs in UTC, so this is the repo-wide convention rather than a
    property of the column."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def to_row(event: BaseEvent, *, topic: str) -> dict[str, Any]:
    """Indexed columns are lifted out for querying; the whole event is kept in
    `payload` so nothing is lost to a schema that did not anticipate it."""
    return {
        "time": _naive_utc(event.occurred_at),
        "event_id": event.event_id,
        "event_type": event.event_type,
        "topic": topic,
        "symbol": getattr(event, "symbol", None),
        "correlation_id": event.correlation_id,
        "payload": event.model_dump(mode="json"),
    }
```

- [ ] **Step 4: Verify**

`python -m pytest tests/test_archiver_routing.py -v` → 6 passed.
`python -m ruff check services/api-gateway/app/archiver.py tests/test_archiver_routing.py` → clean.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(api-gateway): event archive routing, pure

Separate from Persister on purpose: that projects events into business tables
with a lifecycle; this stores them as they arrived so the feed survives a reload.

An unrecognised event type lands in the signal table rather than being dropped --
something we did not anticipate must stay visible, and the longer retention is
the prudent default.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" -- services/api-gateway/app/archiver.py tests/test_archiver_routing.py
```

---

## Task 3 : écriture et branchement de l'archiveur

**Files:**
- Modify: `services/api-gateway/app/archiver.py`
- Modify: `services/api-gateway/app/main.py`
- Test: `tests/test_archiver_writes.py`

- [ ] **Step 1: Write the failing test**

```python
"""L'archiveur écrit, sans jamais gêner le reste du système."""

from __future__ import annotations

from cmi_common.events import PriceEvent
from cmi_common.events.base import Source
from cmi_common.events.journal import JournalEntryEvent

from service_modules import load_service_module

arch = load_service_module("api-gateway", "archiver")


class FakeSession:
    def __init__(self, explode: bool = False) -> None:
        self.executed: list = []
        self.committed = False
        self._explode = explode

    async def execute(self, stmt) -> None:
        if self._explode:
            raise RuntimeError("db down")
        self.executed.append(stmt)

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class FakeDb:
    def __init__(self, session) -> None:
        self._session = session

    def _sessionmaker(self):
        return self._session


def _price() -> PriceEvent:
    return PriceEvent(source=Source.COINGECKO, symbol="BTC", coin_id="bitcoin",
                      price_usd=100.0)


async def test_market_event_is_written_to_the_market_table() -> None:
    s = FakeSession()
    a = arch.EventArchiver(FakeDb(s))
    await a.handle(_price())
    assert s.committed is True
    assert s.executed[0].table.name == "events_market"


async def test_journal_entry_is_skipped_without_touching_the_database() -> None:
    """Pas seulement « non archivé » : aucune session ne doit être ouverte."""
    s = FakeSession()
    a = arch.EventArchiver(FakeDb(s))
    await a.handle(JournalEntryEvent(symbol="BTC", signal_event_id="s1", score=1,
                                     confidence=0.5, factors_present=1))
    assert s.executed == []
    assert s.committed is False


async def test_a_write_failure_never_propagates() -> None:
    """L'archive est de l'observabilité. Une panne d'écriture ne doit pas tuer
    le consommateur Kafka partagé et faire perdre des événements métier."""
    s = FakeSession(explode=True)
    a = arch.EventArchiver(FakeDb(s))
    await a.handle(_price())  # ne doit pas lever


async def test_redelivery_is_idempotent() -> None:
    """Kafka est at-least-once : le même événement peut arriver deux fois et ne
    doit pas produire deux lignes."""
    s = FakeSession()
    a = arch.EventArchiver(FakeDb(s))
    ev = _price()
    await a.handle(ev)
    await a.handle(ev)
    assert len(s.executed) == 2
    assert all("ON CONFLICT" in str(st).upper() for st in s.executed)
```

- [ ] **Step 2: Run it, confirm the failure**

Expect `AttributeError: module ... has no attribute 'EventArchiver'`.

- [ ] **Step 3: Implement**

Append to `services/api-gateway/app/archiver.py`:

```python
import logging

from sqlalchemy.dialects.postgresql import insert

from cmi_common.db import Database
from cmi_common.kafka import TOPIC_EVENT, Topic
from cmi_common.observability import EVENTS_CONSUMED

logger = logging.getLogger(__name__)
SERVICE = "api-gateway"

# Reverse lookup so a row records which topic carried it. Built once.
_TOPIC_BY_TYPE = {cls: topic.value for topic, cls in TOPIC_EVENT.items()}


class EventArchiver:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def handle(self, event: BaseEvent) -> None:
        table = table_for(event)
        if table is None:
            return
        topic = _TOPIC_BY_TYPE.get(type(event), "")
        try:
            async with self._db._sessionmaker() as s:  # noqa: SLF001
                await s.execute(
                    insert(table).values(**to_row(event, topic=topic))
                    .on_conflict_do_nothing()
                )
                await s.commit()
            EVENTS_CONSUMED.labels(SERVICE, topic, event.event_type).inc()
        except Exception:  # noqa: BLE001
            # The archive is observability. A write failure must not kill the
            # shared Kafka consumer and lose business events with it.
            logger.warning("archive write failed for %s", event.event_type,
                           exc_info=True)
```

Adjust the imports at the top of the file to include what this needs.
**Check whether `# noqa: BLE001` is flagged as `RUF100`** in this repo (BLE is not
in the select list) — if so, drop the directive rather than leaving a dead one.

- [ ] **Step 4: Wire it in**

In `services/api-gateway/app/main.py`, add a **second** consumer with its own
group id, consuming the broadcast topics:

```python
    archiver = EventArchiver(db)
    archive_consumer = EventConsumer(
        settings.kafka,
        [
            Topic.PRICE, Topic.VOLUME, Topic.DEX, Topic.SENTIMENT,
            Topic.ANALYSIS, Topic.DECISION, Topic.RISK_APPROVED, Topic.EXECUTION,
        ],
        archiver.handle,
        # Its own group: the archive must not compete with the persister for
        # partitions, and a lagging archive must not delay business persistence.
        group_id="api-gateway-archiver",
    )
    await archive_consumer.start()
    app.state.archive_consumer = archive_consumer
    app.state.archive_task = asyncio.create_task(archive_consumer.run())
```

Stop it in `_shutdown` alongside the existing consumer, and add its task to the
`asyncio.gather`.

`Topic.JOURNAL` is deliberately absent — see the routing decision in Task 2.

- [ ] **Step 5: Verify**

`python -m pytest tests/test_archiver_writes.py tests/test_archiver_routing.py -v` → 10 passed.
`python -m pytest tests/ -rN --tb=no` → 3 failed (known), rest passing.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(api-gateway): archive broadcast events on their own consumer group

A separate group so the archive never competes with the persister for partitions
and a lagging archive never delays business persistence. Write failures are
swallowed: the archive is observability, and taking down a shared Kafka consumer
would lose business events with it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" -- services/api-gateway/app/archiver.py services/api-gateway/app/main.py tests/test_archiver_writes.py
```

---

## Task 4 : curseur composite (pur)

**Files:**
- Create: `services/api-gateway/app/events_cursor.py`
- Test: `tests/test_events_cursor.py`

- [ ] **Step 1: Write the failing test**

```python
"""Curseur de pagination composite.

Un curseur sur le seul horodatage saute ou répète des lignes dès que deux
événements partagent la même milliseconde — ce qui arrive constamment : 120
analyses par heure pour un seul symbole ont été mesurées en production.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from service_modules import load_service_module

cur = load_service_module("api-gateway", "events_cursor")

T = datetime(2026, 7, 26, 12, 0, 0, 123456, tzinfo=timezone.utc)


def test_round_trips() -> None:
    encoded = cur.encode(T, "evt-1")
    assert cur.decode(encoded) == (T, "evt-1")


def test_microseconds_survive() -> None:
    """Tronquer à la seconde ferait sauter des lignes entre deux pages."""
    a, b = cur.encode(T, "x"), cur.encode(T.replace(microsecond=123457), "x")
    assert a != b


def test_an_event_id_containing_the_separator_still_round_trips() -> None:
    """Le séparateur ne doit pas dépendre de l'absence de ce caractère dans les
    identifiants — sinon un id inhabituel corrompt la pagination en silence."""
    encoded = cur.encode(T, "evt_with_underscores_1")
    assert cur.decode(encoded) == (T, "evt_with_underscores_1")


def test_a_malformed_cursor_is_rejected_not_guessed() -> None:
    """Mieux vaut une erreur qu'une page arbitraire."""
    for bad in ("", "pasdedate", "2026-07-26T12:00:00", "___"):
        with pytest.raises(ValueError):
            cur.decode(bad)


def test_decoded_time_is_timezone_aware() -> None:
    """Comparé à une colonne timestamptz : un datetime naïf ici serait une
    source de décalage silencieux."""
    assert cur.decode(cur.encode(T, "e"))[0].tzinfo is not None
```

- [ ] **Step 2: Run it, confirm the failure**

- [ ] **Step 3: Implement**

```python
"""Composite pagination cursor: (time, event_id).

A cursor on the timestamp alone skips or repeats rows as soon as two events
share a millisecond, which happens constantly -- 120 analyses per hour for a
single symbol were measured in production.

The two parts are separated by "|" rather than "_" because event ids routinely
contain underscores; a separator that depends on the id's alphabet corrupts
pagination silently the first time that assumption breaks.
"""

from __future__ import annotations

from datetime import datetime

SEP = "|"


def encode(time: datetime, event_id: str) -> str:
    return f"{time.isoformat()}{SEP}{event_id}"


def decode(cursor: str) -> tuple[datetime, str]:
    """Raises ValueError on anything malformed -- an error beats an arbitrary
    page, which would look like data rather than a bug."""
    raw_time, sep, event_id = cursor.partition(SEP)
    if not sep or not event_id:
        raise ValueError(f"malformed cursor: {cursor!r}")
    parsed = datetime.fromisoformat(raw_time)
    if parsed.tzinfo is None:
        raise ValueError(f"cursor time must be timezone-aware: {cursor!r}")
    return parsed, event_id
```

- [ ] **Step 4: Verify and commit**

`python -m pytest tests/test_events_cursor.py -v` → 5 passed.

```bash
git commit -m "feat(api-gateway): composite pagination cursor

A cursor on the timestamp alone skips or repeats rows whenever two events share
a millisecond, which production does constantly: 120 analyses per hour were
measured for a single symbol.

Separator is '|' rather than '_' because event ids routinely contain
underscores, and a separator that depends on the id alphabet corrupts pagination
silently the first time that assumption breaks.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" -- services/api-gateway/app/events_cursor.py tests/test_events_cursor.py
```

---

## Task 5 : endpoint `GET /events`

**Files:**
- Create: `services/api-gateway/app/events_api.py`
- Modify: `services/api-gateway/app/main.py`
- Modify: `services/api-gateway/app/read_contract.py`
- Modify: `tests/test_read_contract.py`
- Test: `tests/test_events_api.py`

- [ ] **Step 1: Add the contract entry, watch the manifest test fail**

In `read_contract.py`, inside `CONTRACT`:

```python
    "events": {"items", "next_cursor"},
```

`python -m pytest tests/test_read_contract.py -v` → the manifest-coverage test
must fail, naming `events`. If it passes, the guard is broken — stop and report.

- [ ] **Step 2: Implement the route**

Create `services/api-gateway/app/events_api.py`:

```python
"""Paginated read over the archived event stream.

Answers the question the Command Center could not: what happened before I opened
this page. The in-memory feed holds 200 entries and dies on reload.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .events_cursor import decode, encode
from .routers import get_session_dep

router = APIRouter(tags=["events"])

MAX_LIMIT = 200


@router.get("/events")
async def list_events(
    limit: int = Query(100, ge=1, le=MAX_LIMIT),
    types: str | None = Query(None, description="comma-separated event_type list"),
    symbol: str | None = Query(None),
    before: str | None = Query(None, description="cursor from a previous page"),
    session: AsyncSession = Depends(get_session_dep),
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit + 1}
    where = ["TRUE"]

    if before:
        try:
            cursor_time, cursor_id = decode(before)
        except ValueError as exc:
            # A malformed cursor is a client error, not an empty page: silently
            # returning the newest page would look like the end of history.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        where.append("(time, event_id) < (:ct, :cid)")
        params |= {"ct": cursor_time, "cid": cursor_id}
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol.upper()
    if types:
        wanted = [t.strip() for t in types.split(",") if t.strip()]
        if wanted:
            where.append("event_type = ANY(:types)")
            params["types"] = wanted

    clause = " AND ".join(where)
    # UNION ALL across the two retention tiers, ordered by the same composite key
    # the cursor uses, then one extra row to detect whether a next page exists.
    rows = (
        await session.execute(
            text(
                f"SELECT time, event_id, event_type, topic, symbol, correlation_id, "
                f"       payload FROM events_market WHERE {clause} "
                f"UNION ALL "
                f"SELECT time, event_id, event_type, topic, symbol, correlation_id, "
                f"       payload FROM events_signal WHERE {clause} "
                f"ORDER BY time DESC, event_id DESC LIMIT :limit"
            ),
            params,
        )
    ).all()

    items = [dict(r._mapping) for r in rows[:limit]]
    # The extra row is the existence proof of a next page -- counting instead
    # would cost a second scan of both hypertables on every request.
    has_more = len(rows) > limit
    next_cursor = (
        encode(items[-1]["time"], items[-1]["event_id"]) if has_more and items else None
    )
    return {"items": items, "next_cursor": next_cursor}
```

Mount it in `main.py` (`app.include_router(events_api.router)`) and extend the
module import.

- [ ] **Step 3: Add the contract assertion**

In `tests/test_read_contract.py`, load `events_api` next to the others and add,
before the manifest-coverage test:

```python
async def test_events_contract() -> None:
    resp = await events_api.list_events(
        limit=10, types=None, symbol=None, before=None, session=_FakeSession(4)
    )
    _assert_keys("events", resp)
```

- [ ] **Step 4: Behaviour tests**

Create `tests/test_events_api.py`:

```python
"""Pagination du flux archivé."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from service_modules import load_service_module

api = load_service_module("api-gateway", "events_api")

T = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


class _Row:
    def __init__(self, m: dict) -> None:
        self._mapping = m


class FakeSession:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.params: dict | None = None

    async def execute(self, _stmt, params=None):
        self.params = params

        class R:
            def __init__(self, rows):
                self._rows = [_Row(r) for r in rows]

            def all(self):
                return self._rows

        return R(self.rows)


def _row(i: int) -> dict:
    return {"time": T, "event_id": f"e{i}", "event_type": "PriceEvent",
            "topic": "market.price.events", "symbol": "BTC",
            "correlation_id": None, "payload": {}}


async def test_empty_archive_returns_an_empty_page_not_an_error() -> None:
    resp = await api.list_events(limit=10, types=None, symbol=None, before=None,
                                 session=FakeSession())
    assert resp["items"] == []
    assert resp["next_cursor"] is None


async def test_next_cursor_is_null_on_the_last_page() -> None:
    """Sans plus de lignes que demandé, il n'y a pas de page suivante — renvoyer
    un curseur ferait boucler le client sur une page vide."""
    resp = await api.list_events(limit=10, types=None, symbol=None, before=None,
                                 session=FakeSession([_row(i) for i in range(10)]))
    assert len(resp["items"]) == 10
    assert resp["next_cursor"] is None


async def test_extra_row_signals_a_next_page_and_is_not_returned() -> None:
    """On demande limit+1 pour savoir s'il reste quelque chose ; la ligne
    supplémentaire ne doit jamais apparaître dans les résultats."""
    resp = await api.list_events(limit=10, types=None, symbol=None, before=None,
                                 session=FakeSession([_row(i) for i in range(11)]))
    assert len(resp["items"]) == 10
    assert resp["next_cursor"] is not None
    assert "e9" in resp["next_cursor"]


async def test_malformed_cursor_is_a_400_not_an_empty_page() -> None:
    """Renvoyer la page la plus récente ressemblerait à la fin de l'historique."""
    with pytest.raises(HTTPException) as exc:
        await api.list_events(limit=10, types=None, symbol=None,
                              before="n'importe quoi", session=FakeSession())
    assert exc.value.status_code == 400


async def test_symbol_filter_is_upper_cased() -> None:
    """Les symboles sont stockés en majuscules ; un filtre sensible à la casse
    renverrait vide sans rien signaler."""
    s = FakeSession()
    await api.list_events(limit=10, types=None, symbol="btc", before=None, session=s)
    assert s.params["symbol"] == "BTC"


async def test_types_filter_is_split_and_trimmed() -> None:
    s = FakeSession()
    await api.list_events(limit=10, types=" PriceEvent , DecisionEvent ",
                          symbol=None, before=None, session=s)
    assert s.params["types"] == ["PriceEvent", "DecisionEvent"]
```

- [ ] **Step 5: Verify**

`python -m pytest tests/test_events_api.py tests/test_read_contract.py -v` → all pass.
`python -m pytest tests/ -rN --tb=no` → 3 failed (known).

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(api-gateway): GET /events with composite-cursor pagination

Answers what the Command Center could not: what happened before the page was
opened. Fetches limit+1 rows to prove a next page exists rather than counting,
which would cost a second scan of both hypertables per request.

A malformed cursor is a 400, not an empty page -- silently returning the newest
page would look like the end of history.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" -- services/api-gateway/app/events_api.py services/api-gateway/app/main.py services/api-gateway/app/read_contract.py tests/test_read_contract.py tests/test_events_api.py
```

---

## Task 6 : frontend — types, endpoint et hook de fusion

**Files:**
- Modify: `frontend/src/lib/types/events.ts`
- Modify: `frontend/src/lib/api/endpoints.ts`
- Create: `frontend/src/lib/hooks/useEventFeed.ts`
- Create: `frontend/src/lib/mock/eventPage.ts`
- Create: `frontend/src/app/api/mock/events/route.ts`

- [ ] **Step 1: Types**

Append to `frontend/src/lib/types/events.ts`:

```typescript
export interface ArchivedEvent {
  time: string;
  event_id: string;
  event_type: string;
  topic: string;
  symbol: string | null;
  correlation_id: string | null;
  payload: Record<string, unknown>;
}

export interface EventPage {
  items: ArchivedEvent[];
  next_cursor: string | null;
}
```

- [ ] **Step 2: Endpoint**

In `frontend/src/lib/api/endpoints.ts`, following the existing `.then((r) => r.data)`
style:

```typescript
export const eventsApi = {
  page: (params: { limit?: number; before?: string | null; types?: string; symbol?: string }) =>
    api
      .get<EventPage>('/events', {
        params: {
          limit: params.limit ?? 100,
          ...(params.before ? { before: params.before } : {}),
          ...(params.types ? { types: params.types } : {}),
          ...(params.symbol ? { symbol: params.symbol } : {}),
        },
      })
      .then((r) => r.data),
};
```

Import `EventPage` from the types module.

- [ ] **Step 3: The merge hook**

Create `frontend/src/lib/hooks/useEventFeed.ts`:

```typescript
'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { eventsApi } from '@/lib/api/endpoints';
import { useEventSubscription } from '@/lib/ws/WebSocketProvider';
import type { ArchivedEvent, CmiEvent } from '@/lib/types/events';

/**
 * Combines the archived history with the live WebSocket stream.
 *
 * Deduplicated by `event_id`: an event can legitimately arrive twice — once on
 * the socket and once in a history page fetched moments later — and showing it
 * twice would make the feed look like it was double-counting.
 *
 * The WebSocketProvider stays a pure transport; nothing here is pushed back
 * into it.
 */
export function useEventFeed(opts: { types?: string; symbol?: string } = {}) {
  const query = useInfiniteQuery({
    queryKey: ['events', opts.types ?? null, opts.symbol ?? null],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      eventsApi.page({ limit: 100, before: pageParam, types: opts.types, symbol: opts.symbol }),
    getNextPageParam: (last) => last.next_cursor,
  });

  const [live, setLive] = useState<ArchivedEvent[]>([]);
  const seen = useRef<Set<string>>(new Set());

  useEventSubscription([], (event: CmiEvent) => {
    if (!event.event_id || seen.current.has(event.event_id)) return;
    seen.current.add(event.event_id);
    setLive((prev) => [
      {
        time: event.occurred_at ?? new Date().toISOString(),
        event_id: event.event_id,
        event_type: event.event_type,
        topic: '',
        symbol: (event as { symbol?: string }).symbol ?? null,
        correlation_id: event.correlation_id ?? null,
        payload: event as unknown as Record<string, unknown>,
      },
      ...prev,
    ]);
  });

  const items = useMemo(() => {
    const merged = new Map<string, ArchivedEvent>();
    for (const e of live) merged.set(e.event_id, e);
    for (const page of query.data?.pages ?? []) {
      for (const e of page.items) if (!merged.has(e.event_id)) merged.set(e.event_id, e);
    }
    return [...merged.values()].sort((a, b) => b.time.localeCompare(a.time));
  }, [live, query.data]);

  // A history page can contain an event the socket already delivered; register
  // those ids so a later frame for the same event is not appended again.
  useEffect(() => {
    for (const page of query.data?.pages ?? []) {
      for (const e of page.items) seen.current.add(e.event_id);
    }
  }, [query.data]);

  return {
    items,
    fetchNextPage: query.fetchNextPage,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    isLoading: query.isLoading,
  };
}
```

- [ ] **Step 4: Mock route**

Create `frontend/src/lib/mock/eventPage.ts` returning a page of ~40 plausible
archived events (mixed `PriceEvent`, `SentimentEvent`, `AnalysisEvent`,
`DecisionEvent`) with descending timestamps and a `next_cursor`, plus
`frontend/src/app/api/mock/events/route.ts` following the exact pattern of
`frontend/src/app/api/mock/systems/funnel/route.ts` (read it first).

The mock must honour the `before` query parameter by returning a **second, older**
page and then `next_cursor: null`, so infinite scroll is exercisable without a
backend — otherwise the pagination path is never tested in mock mode.

- [ ] **Step 5: Verify**

```
cd frontend && npx tsc --noEmit && npm run build
```

Both must pass.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(frontend): useEventFeed merging archived history with the live socket

Deduplicated by event_id: an event legitimately arrives twice -- once on the
socket, once in a history page fetched moments later -- and showing it twice
would make the feed look like it was double-counting.

The mock route honours the cursor so infinite scroll is exercisable without a
backend; otherwise the pagination path would never be tested in mock mode.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" -- frontend/src/lib/hooks/useEventFeed.ts frontend/src/lib/types/events.ts frontend/src/lib/api/endpoints.ts frontend/src/lib/mock/eventPage.ts "frontend/src/app/api/mock/events/route.ts"
```

---

## Task 7 : fusion des deux composants de flux

**Files:**
- Modify: `frontend/src/components/command/LiveEventStream.tsx`
- Delete: `frontend/src/components/realtime/LiveFeed.tsx`
- Modify: any importer of `LiveFeed`

- [ ] **Step 1: Find every importer**

```
cd frontend && grep -rn "LiveFeed" src/
```

Handle each. If `LiveFeed` is used on a page other than the Command Center, that
page must switch to the merged component — **do not leave a dangling import**.

- [ ] **Step 2: Rewrite `LiveEventStream`**

Keep its current strengths — the click-to-trace and the compact row layout — and
take from `LiveFeed` its category filters (`Tout / IA / Exécution / Marché`) and
relative timestamps. Drive it from `useEventFeed`, and add a "charger plus"
control wired to `fetchNextPage`, disabled when `hasNextPage` is false.

Remove `PositionChangedEvent` and `PortfolioChangedEvent` from the label maps:
**nothing produces them** — verified, they exist only in the frontend types and
are inherited from the mock. Leaving them can only mislead.

- [ ] **Step 3: Delete `LiveFeed.tsx`** once no importer remains.

- [ ] **Step 4: Verify**

```
cd frontend && npx tsc --noEmit && npm run build && npx next lint --file src/components/command/LiveEventStream.tsx
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(frontend): merge the two feed components, backed by the archive

LiveFeed and LiveEventStream had already diverged -- different labels for the
same event types -- and wiring both to the new hook would have doubled the work.
The merged component keeps LiveEventStream's click-to-trace and LiveFeed's
category filters, and gains pagination.

Drops PositionChangedEvent and PortfolioChangedEvent from the labels: nothing
produces them, they exist only in the frontend types as mock leftovers, and they
can only mislead.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8 : vérification

- [ ] **Step 1** — `python -m pytest tests/ -rN --tb=no` → 3 known failures only.
- [ ] **Step 2** — lint delta vs `master`, as in the previous plans; report any new category.
- [ ] **Step 3** — `cd migrations && python -m alembic upgrade head --sql` then
  `downgrade 0009 --sql`; confirm both new steps and their round trip.
- [ ] **Step 4** — `cd frontend && npx tsc --noEmit && npm run build`.
- [ ] **Step 5 — post-deploy acceptance (VPS, after merge):**

```bash
ssh <VPS_USER>@<VPS_HOST> 'cd /opt/bottrading && docker compose logs migrate --tail 20'
# expect: 0009 -> 0010 -> 0011

ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-postgres-1 psql -U cmi -d cmi -c \
  'SELECT count(*) AS marche FROM events_market;' -c 'SELECT count(*) AS signal FROM events_signal;'"
# expect after a few minutes: marche in the thousands, signal far fewer

ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-api-gateway-1 python -c \"
import urllib.request, json
d = json.loads(urllib.request.urlopen('http://localhost:8000/events?limit=5').read())
print(len(d['items']), 'items, curseur:', d['next_cursor'])\""
```

**The ratio is the acceptance test.** If `events_signal` is empty while
`events_market` fills, the routing is sending everything one way and the
long-retention tier — the one worth reading back — is not being populated.

---

## Self-review

**Couverture de la spec**

| Exigence | Tâche |
|---|---|
| Deux hypertables séparées par la rétention | 1 |
| `PRIMARY KEY (time, event_id)` au lieu de `UNIQUE(event_id)` | 1 |
| `EventArchiver` distinct du `Persister` | 2, 3 |
| Curseur composite | 4 |
| `GET /events` + contrat | 5 |
| Fusion historique + live côté frontend | 6 |
| Fusion des deux composants de flux | 7 |
| Retrait de `PositionChangedEvent` / `PortfolioChangedEvent` | 7 |
| Pas de rejeu Kafka | hors périmètre, conforme à la spec |

**Écarts assumés par rapport à la spec**

- `journal.entries` **exclu** de l'archivage — il a déjà sa table et sa rétention.
- `AccountSnapshotEvent` n'existe pas encore (phase 2) ; le routage par défaut
  l'enverra vers `events_signal` sans modification quand il arrivera.

**Cohérence des types :** `to_row` produit les clés que les modèles `EventMarket`
et `EventSignal` déclarent (tâche 1) et que la migration crée. `encode`/`decode`
partagent le séparateur `|`. `EventPage` côté TypeScript correspond au contrat
`{items, next_cursor}` de la tâche 5.
