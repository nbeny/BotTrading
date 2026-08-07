# Market Data Unifiée sur Kafka — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persister et diffuser les topics `derivatives`/`fundamentals`/`developer` (chantier B), puis faire publier les bougies de collector-kraken sur Kafka (chantier A), pour que chaque événement de marché soit persisté en hypertable et poussé au front.

**Architecture:** Le persister existant de l'api-gateway gagne 3 handlers + 3 topics (puis un 4ᵉ pour les bougies, en upsert) ; une migration 0011 crée 3 hypertables d'instantanés ; `BROADCAST_TOPICS` du websocket-gateway passe de 11 à 14 ; le front étend l'union `CmiEvent`, le `LiveEventStream` et le mock, et le `RegimeStrip` invalide sa query sur événement dérivés (débounce, poll conservé).

**Tech Stack:** SQLAlchemy 2 async + Alembic + TimescaleDB, aiokafka, Pydantic v2, Next 15 + TanStack Query 5 + vitest.

**Spec :** `docs/superpowers/specs/2026-08-07-market-data-kafka-unification-design.md`

---

## Faits vérifiés dans le code (à respecter, corrige la spec sur 3 points)

1. **Champs réels des événements** (la spec listait des colonnes approximatives) :
   `DerivativesEvent` = symbol, funding_rate_8h, funding_annualized_pct, open_interest_usd, open_interest_change_pct_24h, long_short_account_ratio (pas de `venue` aujourd'hui).
   `FundamentalsEvent` = symbol, **coin_id**, tvl_usd, tvl_change_pct_7d, **fees_24h_usd**, fees_change_pct_7d, next_unlock_at, next_unlock_pct_supply, **has_unlock_schedule**.
   `DeveloperEvent` = symbol, **coin_id**, **repo_count**, commit_ratio_4w, pr_ratio_4w, days_since_push, star_growth_pct_7d, all_repos_archived. Les tables reprennent CES champs.
2. **La table `candles` est upsertée** (bougie en formation réécrite à chaque sweep — docstring du modèle) : le handler persister des bougies fait `ON CONFLICT DO UPDATE`, pas `do_nothing`, et `CandleEvent` porte aussi `vwap`.
3. **Le sweeper kraken lit son curseur en DB** (`last_candle_epoch`) : après bascule Kafka il continue de le lire (le persister écrit, l'upsert absorbe les chevauchements dus au lag). `repository.last_candle_epoch` reste ; `store.save_candles`/`repository.upsert_candles` deviennent morts et sont supprimés.

**Patrons de référence** : handler persister = `persister.py::_save_price` (EVENTS_CONSUMED + insert + on_conflict + commit, session par appel) ; migration hypertable = `migrations/alembic/versions/0010_events_market.py` (PK avec time, create_hypertable, add_retention_policy, downgrade avec remove_retention_policy if_exists) ; test persister = `tests/test_rejection_persister.py` (FakeSession/FakeDb, `load_service_module("api-gateway", "persister")`) ; producer = `services/collector-binance-futures/app/application/collector.py:75`.

**Règles transverses** : colonne absente = NULL, jamais 0 ; tests au root `tests/` via `load_service_module` ; ruff/black/mypy strict sur les fichiers touchés ; ne pas corriger le bruit pré-existant.

---

### Task 1 (B) : Modèles + migration 0011

**Files:**
- Modify: `libs/cmi_common/cmi_common/db/models.py` (après `MarketDepth`)
- Modify: `libs/cmi_common/cmi_common/db/__init__.py` (exports)
- Create: `migrations/alembic/versions/0011_market_snapshots.py`

- [ ] **Step 1 : Modèles** — ajouter à `models.py` (mêmes conventions que `Candle`/`MarketDepth`) :

```python
class DerivativesSnapshot(Base):
    """Perp positioning snapshot, one row per DerivativesEvent republication.

    Every measurement column is nullable: a partial event is the normal case
    (funding for the broad tier, OI/ratio for majors only). History is the
    point — successive identical snapshots are two dated rows, not a bug.
    """

    __tablename__ = "derivatives_snapshots"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: The event carries no venue yet; the persister stamps its source. When
    #: collector-kraken-futures lands (2026-08-06 spec), the event's own venue
    #: flows through here unchanged.
    venue: Mapped[str] = mapped_column(String(16), default="binance")
    funding_rate_8h: Mapped[float | None] = mapped_column(Float, default=None)
    funding_annualized_pct: Mapped[float | None] = mapped_column(Float, default=None)
    open_interest_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 2), default=None
    )
    open_interest_change_pct_24h: Mapped[float | None] = mapped_column(
        Float, default=None
    )
    long_short_account_ratio: Mapped[float | None] = mapped_column(Float, default=None)


class FundamentalsSnapshot(Base):
    """Protocol fundamentals snapshot, one row per FundamentalsEvent."""

    __tablename__ = "fundamentals_snapshots"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    coin_id: Mapped[str] = mapped_column(String(64))
    tvl_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 2), default=None)
    tvl_change_pct_7d: Mapped[float | None] = mapped_column(Float, default=None)
    fees_24h_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 2), default=None)
    fees_change_pct_7d: Mapped[float | None] = mapped_column(Float, default=None)
    next_unlock_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    next_unlock_pct_supply: Mapped[float | None] = mapped_column(Float, default=None)
    #: Keeps "no unlock coming" distinct from "DefiLlama does not track this".
    has_unlock_schedule: Mapped[bool] = mapped_column(Boolean, default=False)


class DeveloperSnapshot(Base):
    """Developer activity snapshot, one row per DeveloperEvent."""

    __tablename__ = "developer_snapshots"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    coin_id: Mapped[str] = mapped_column(String(64))
    repo_count: Mapped[int] = mapped_column(Integer)
    commit_ratio_4w: Mapped[float | None] = mapped_column(Float, default=None)
    pr_ratio_4w: Mapped[float | None] = mapped_column(Float, default=None)
    days_since_push: Mapped[int | None] = mapped_column(Integer, default=None)
    star_growth_pct_7d: Mapped[float | None] = mapped_column(Float, default=None)
    all_repos_archived: Mapped[bool] = mapped_column(Boolean, default=False)
```

Vérifier que `Boolean`/`Float`/`Integer`/`Numeric` sont déjà importés dans models.py (ils le sont pour les modèles existants). Ajouter les trois classes à `db/__init__.py` (même liste d'export que `Candle`).

- [ ] **Step 2 : Migration** — `0011_market_snapshots.py`, miroir exact du patron 0010 pour CHAQUE table : `op.create_table` avec PK `(time, symbol)`, index `ix_<table>_symbol` sur `["symbol", "time"]`, `create_hypertable(..., if_not_exists => TRUE, migrate_data => TRUE)`, `add_retention_policy('<table>', INTERVAL '90 days', if_not_exists => TRUE)`. `revision = "0011"`, `down_revision = "0010"`. Downgrade : `remove_retention_policy(..., if_exists => TRUE)` puis `drop_table`, pour les trois.

- [ ] **Step 3 : Vérifier statiquement** — `python -c "from cmi_common.db import DerivativesSnapshot, FundamentalsSnapshot, DeveloperSnapshot; print('ok')"` (depuis la racine, PYTHONPATH libs si besoin : `python -c "import sys; sys.path.insert(0,'libs/cmi_common'); ..."`) ; `python -m py_compile migrations/alembic/versions/0011_market_snapshots.py` ; ruff/black/mypy sur models.py. `pytest -q` → pas de régression.

- [ ] **Step 4 : Commit**

```bash
git add libs/cmi_common/cmi_common/db/models.py libs/cmi_common/cmi_common/db/__init__.py migrations/alembic/versions/0011_market_snapshots.py
git commit -m "feat(db): hypertables derivatives/fundamentals/developer snapshots"
```

---

### Task 2 (B) : Handlers persister + topics consumer

**Files:**
- Modify: `services/api-gateway/app/persister.py`
- Modify: `services/api-gateway/app/main.py` (liste de topics du consumer persister, ~l.30-38)
- Test: `tests/test_snapshot_persister.py`

- [ ] **Step 1 : Test qui échoue** — `tests/test_snapshot_persister.py`, patron de `test_rejection_persister.py` (réutiliser FakeSession/FakeDb en les redéfinissant localement — ils sont à 15 lignes, la duplication de fixtures de test est le style du repo) :

```python
"""Persister routes derivatives/fundamentals/developer events into their
snapshot hypertables. A partial event persists with NULLs, never zeros."""

from __future__ import annotations

from decimal import Decimal

from service_modules import load_service_module

from cmi_common.events.base import Source
from cmi_common.events.market import (
    DerivativesEvent,
    DeveloperEvent,
    FundamentalsEvent,
)

persister_mod = load_service_module("api-gateway", "persister")


class FakeSession:
    def __init__(self) -> None:
        self.executed: list = []
        self.committed = False

    async def execute(self, stmt) -> None:
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


async def test_partial_derivatives_event_persists_with_nulls() -> None:
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    await p.handle(
        DerivativesEvent(source=Source.BINANCE_FUTURES, symbol="BTC",
                         funding_rate_8h=0.0001)
    )
    assert session.committed is True
    assert len(session.executed) == 1
    values = session.executed[0].compile().params
    assert values["funding_rate_8h"] == 0.0001
    assert values["open_interest_usd"] is None          # absent ≠ 0
    assert values["venue"] == "binance"


async def test_fundamentals_event_persists() -> None:
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    await p.handle(
        FundamentalsEvent(source=Source.DEFILLAMA, symbol="AAVE", coin_id="aave",
                          tvl_usd=Decimal("123.45"), has_unlock_schedule=True)
    )
    assert session.committed and len(session.executed) == 1
    values = session.executed[0].compile().params
    assert values["coin_id"] == "aave"
    assert values["fees_24h_usd"] is None
    assert values["has_unlock_schedule"] is True


async def test_developer_event_persists() -> None:
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    await p.handle(
        DeveloperEvent(source=Source.GITHUB, symbol="LINK", coin_id="chainlink",
                       repo_count=3, commit_ratio_4w=1.2)
    )
    assert session.committed and len(session.executed) == 1
    values = session.executed[0].compile().params
    assert values["repo_count"] == 3
    assert values["days_since_push"] is None
```

Vérifier les membres réels de `Source` (`grep "BINANCE_FUTURES\|DEFILLAMA\|GITHUB" libs/cmi_common/cmi_common/events/base.py`) — adapter si les noms diffèrent. Run → FAIL (handlers absents, `handle` ignore ces événements sans erreur : les asserts `committed` échouent).

- [ ] **Step 2 : Implémenter** — dans `persister.py` : imports (`DerivativesEvent, DeveloperEvent, FundamentalsEvent` depuis `cmi_common.events.market` ; `DerivativesSnapshot, DeveloperSnapshot, FundamentalsSnapshot` depuis `cmi_common.db`), trois branches dans `handle()` (avant le fallback implicite), trois handlers sur le patron `_save_price` :

```python
    async def _save_derivatives(self, e: DerivativesEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.DERIVATIVES.value, event_type_of(e)).inc()
        async with self._db._sessionmaker() as s:
            await s.execute(
                insert(DerivativesSnapshot)
                .values(
                    time=e.occurred_at,
                    symbol=e.symbol,
                    venue="binance",
                    funding_rate_8h=e.funding_rate_8h,
                    funding_annualized_pct=e.funding_annualized_pct,
                    open_interest_usd=e.open_interest_usd,
                    open_interest_change_pct_24h=e.open_interest_change_pct_24h,
                    long_short_account_ratio=e.long_short_account_ratio,
                )
                .on_conflict_do_nothing()
            )
            await s.commit()
```

(idem `_save_fundamentals` — tous les champs de FundamentalsEvent — et `_save_developer` ; un commentaire sur `venue="binance"` : l'événement ne porte pas encore de venue, la spec Kraken Futures l'ajoutera). Dans `main.py`, ajouter `Topic.DERIVATIVES, Topic.FUNDAMENTALS, Topic.DEVELOPER` à la liste du consumer **persister** (group `api-gateway-persister`) — PAS à l'archiver (les snapshots ont leurs tables, même logique que le commentaire existant sur Topic.JOURNAL).

- [ ] **Step 3 : Vérifier** — `pytest tests/test_snapshot_persister.py tests/test_rejection_persister.py -q` → PASS ; `pytest -q` complet ; ruff/black sur les fichiers touchés ; mypy : pas de nouvelle erreur attribuable.

- [ ] **Step 4 : Commit**

```bash
git add services/api-gateway/app/persister.py services/api-gateway/app/main.py tests/test_snapshot_persister.py
git commit -m "feat(api-gateway): persister les snapshots derivatives/fundamentals/developer"
```

---

### Task 3 (B) : as_of réels pour les drivers funding/ΔOI

**Files:**
- Modify: `services/api-gateway/app/regime_api.py`
- Modify: `tests/test_read_contract.py` (si le compte de requêtes du fake change)

- [ ] **Step 1 : Ajouter le gather** — dans `regime_api.py`, un helper à côté de `_market_sentiment` :

```python
async def _derivatives_as_of(session: AsyncSession) -> str | None:
    """max(time) of derivatives_snapshots — real freshness for funding/ΔOI,
    replacing 'fraîcheur inconnue' once the table has rows."""
    stmt = select(func.max(DerivativesSnapshot.time))
    row = (await session.execute(stmt)).first()
    return _iso(row[0]) if row and row[0] else None
```

(import `DerivativesSnapshot` depuis `cmi_common.db`). Dans la route : un bloc try/except supplémentaire (avec `_rollback_quietly` comme les autres) autour de `deriv_as_of = await _derivatives_as_of(session)`, défaut `None` ; passer `as_of=deriv_as_of` aux appels `regime.funding_driver(...)` et `regime.oi_delta_driver(...)`.

- [ ] **Step 2 : Ajuster le test de contrat si besoin** — `test_market_regime_contract` utilise `_FakeSession(8)` ; la nouvelle requête en consomme une de plus — vérifier que le budget de la fake session suffit (sinon passer à `_FakeSession(9)`). Les shapes ne changent pas (as_of était déjà dans le contrat).

- [ ] **Step 3 : Vérifier** — `pytest tests/test_read_contract.py tests/test_regime_rules.py tests/test_regime_gathering.py -q` → PASS ; lint.

- [ ] **Step 4 : Commit**

```bash
git add services/api-gateway/app/regime_api.py tests/test_read_contract.py
git commit -m "feat(api-gateway): as_of reels pour les drivers funding et delta OI"
```

---

### Task 4 (B) : websocket-gateway — 3 topics de plus

**Files:**
- Modify: `services/websocket-gateway/app/consumer.py:34-45` (`BROADCAST_TOPICS`)
- Test: `tests/test_broadcast_topics.py` (créer ; vérifier d'abord par grep qu'aucun test websocket-gateway n'existe déjà sous `tests/` — si un existe, étendre celui-là)

- [ ] **Step 1 : Test qui échoue**

```python
"""BROADCAST_TOPICS must carry every market topic the terminal subscribes to.

The list is an explicit subscription, not a wildcard — a topic missing here is
a family of events that silently never reaches the front."""

from service_modules import load_service_module

from cmi_common.kafka import Topic

consumer_mod = load_service_module("websocket-gateway", "consumer")


def test_derived_market_topics_are_broadcast() -> None:
    topics = set(consumer_mod.BROADCAST_TOPICS)
    assert Topic.DERIVATIVES in topics
    assert Topic.FUNDAMENTALS in topics
    assert Topic.DEVELOPER in topics
```

Vérifier que `load_service_module("websocket-gateway", "consumer")` fonctionne (le helper transforme le nom en `websocket_gateway_app.consumer`) ; si l'import échoue sur une dépendance manquante du module, adapter en important la constante autrement et le signaler. Run → FAIL.

- [ ] **Step 2 : Implémenter** — ajouter `Topic.DERIVATIVES, Topic.FUNDAMENTALS, Topic.DEVELOPER` à `BROADCAST_TOPICS` (fin de tuple).

- [ ] **Step 3 : Vérifier** — `pytest tests/test_broadcast_topics.py -q` → PASS ; `pytest -q` complet ; lint.

- [ ] **Step 4 : Commit**

```bash
git add services/websocket-gateway/app/consumer.py tests/test_broadcast_topics.py
git commit -m "feat(websocket-gateway): diffuser derivatives/fundamentals/developer"
```

---

### Task 5 (B) : Frontend — union CmiEvent, LiveEventStream, mock

**Files:**
- Modify: `frontend/src/lib/types/events.ts`
- Modify: `frontend/src/components/command/LiveEventStream.tsx`
- Modify: `frontend/src/lib/mock/sim.ts`

- [ ] **Step 1 : Vérifier les valeurs sérialisées** — `grep -n "DERIVATIVES\|FUNDAMENTALS\|DEVELOPER" libs/cmi_common/cmi_common/events/base.py` : les littéraux TS doivent reprendre la VALEUR de l'enum (`EventType.PRICE = "PriceEvent"` ⇒ attendu `"DerivativesEvent"` etc. — confirmer avant d'écrire).

- [ ] **Step 2 : Types** — dans `events.ts`, trois interfaces sur le modèle des existantes (mêmes champs de base : `event_id`, `event_type` littéral, `source`, `occurred_at`, `symbol`, `correlation_id?`) + les champs métier des événements Python (mêmes noms, `number | null` pour les nullables, `boolean` pour les bools) ; les ajouter à l'union `CmiEvent`.

- [ ] **Step 3 : LiveEventStream** — ajouter trois entrées à `TYPE_META` (libellés : `Dérivés` couleur `info`, `Fondamentaux` couleur `default`, `Dev` couleur `default` — suivre le format exact des entrées existantes) et les trois types à `UNTRACEABLE_TYPES` (pas de lignée par correlation id : ces événements ne sont pas archivés dans `events_market`, un clic trace ne trouverait rien). Vérifier le `switch (e.event_type)` (~l.127) : s'il exige un case par type pour le résumé, ajouter des cases rendant les valeurs clés (`funding_rate_8h`, `tvl_usd`, `commit_ratio_4w`) avec `?? '—'` pour les nulls.

- [ ] **Step 4 : Mock sim** — dans `sim.ts`, via le helper `base(type, source, symbol, atMs)` existant, émettre périodiquement (cadence lente, ~1 événement sur 15 ticks, symbole via `pick(...)`) un `DerivativesEvent` (funding_rate_8h aléatoire ±0.0003, open_interest_change_pct_24h parfois null), un `FundamentalsEvent` (tvl_usd, fees null la moitié du temps), un `DeveloperEvent` (commit_ratio_4w, repo_count>0). Respecter le style déterministe-ish du fichier existant (il utilise déjà ses propres helpers de random — suivre le fichier, pas la règle des mocks journal).

- [ ] **Step 5 : Vérifier** — `cd frontend && npm run typecheck && npm run test:run` → verts (le test de /command rend LiveEventStream mocké — vérifier qu'aucun test ne casse) ; contrôle visuel `npm run dev` → /command affiche les nouveaux types dans le flux.

- [ ] **Step 6 : Commit**

```bash
git add frontend/src/lib/types/events.ts frontend/src/components/command/LiveEventStream.tsx frontend/src/lib/mock/sim.ts
git commit -m "feat(frontend): evenements derives/fondamentaux/dev dans le flux live"
```

---

### Task 6 (B) : RegimeStrip — invalidation sur événement

**Files:**
- Modify: `frontend/src/components/layout/RegimeStrip.tsx`
- Modify: `frontend/src/components/layout/__tests__/RegimeStrip.test.tsx`

- [ ] **Step 1 : Test qui échoue** — les tests existants mockent `@/lib/api/endpoints` ; le composant va maintenant importer `useEventSubscription` → ajouter à TOUS les tests du fichier un mock du module WS qui capture le handler :

```tsx
const subscriptionHandlers: Array<(e: unknown, m: unknown) => void> = [];
vi.mock('@/lib/ws/WebSocketProvider', () => ({
  useEventSubscription: vi.fn((_types: string[], handler: (e: unknown, m: unknown) => void) => {
    subscriptionHandlers.push(handler);
  }),
}));
```

(et `subscriptionHandlers.length = 0` dans le `afterEach`). Nouveau test :

```tsx
it('invalide la query regime à la réception d’un événement dérivés (débounce)', async () => {
  vi.useFakeTimers();
  regimeGet.mockResolvedValue(getRegime());
  statusGet.mockResolvedValue({ mode: 'dry_run', trading_enabled: true, auto_trading_enabled: false });
  renderStrip();
  await vi.waitFor(() => expect(regimeGet).toHaveBeenCalledTimes(1));
  // deux événements en rafale → une seule invalidation après le débounce
  subscriptionHandlers.forEach((h) => h({ event_type: 'DerivativesEvent' }, {}));
  subscriptionHandlers.forEach((h) => h({ event_type: 'DerivativesEvent' }, {}));
  await vi.advanceTimersByTimeAsync(2100);
  await vi.waitFor(() => expect(regimeGet).toHaveBeenCalledTimes(2));
  vi.useRealTimers();
});
```

(Si `vi.waitFor` + fake timers se battent avec TanStack Query dans ce setup, remplacer l'assertion par un spy sur `queryClient.invalidateQueries` : créer le QueryClient dans le test, `const spy = vi.spyOn(qc, 'invalidateQueries')`, et asserter `spy` appelé une fois avec `{ queryKey: ['market','regime'] }` — garder l'INTENT : rafale → une invalidation.) Run → FAIL.

- [ ] **Step 2 : Implémenter** — dans `RegimeStrip.tsx` :

```tsx
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEventSubscription } from '@/lib/ws/WebSocketProvider';
```

et dans le composant :

```tsx
  const queryClient = useQueryClient();
  // Push accélère, le poll 30s reste le filet : une rafale de republication
  // (~200 symboles d'un coup) ne doit produire qu'une invalidation.
  const invalidateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEventSubscription(['DerivativesEvent'], () => {
    if (invalidateTimer.current) return;
    invalidateTimer.current = setTimeout(() => {
      invalidateTimer.current = null;
      queryClient.invalidateQueries({ queryKey: ['market', 'regime'] });
    }, 2000);
  });
  useEffect(
    () => () => {
      if (invalidateTimer.current) clearTimeout(invalidateTimer.current);
    },
    [],
  );
```

Vérifier le littéral de type accepté par `useEventSubscription` (`CmiEvent['event_type'][]`) — `'DerivativesEvent'` doit exister dans l'union (Task 5).

- [ ] **Step 3 : Vérifier** — `npx vitest run src/components/layout/__tests__/RegimeStrip.test.tsx` → 7/7 ; `npm run test:run` complet ; `npm run typecheck`.

- [ ] **Step 4 : Commit**

```bash
git add frontend/src/components/layout/RegimeStrip.tsx frontend/src/components/layout/__tests__/RegimeStrip.test.tsx
git commit -m "feat(frontend): regime strip pousse par les evenements derives"
```

---

### Task 7 (A) : CandleEvent + topic

**Files:**
- Modify: `libs/cmi_common/cmi_common/events/base.py` (EventType)
- Modify: `libs/cmi_common/cmi_common/events/market.py` (CandleEvent)
- Modify: `libs/cmi_common/cmi_common/events/__init__.py` (registre parse_event — `grep -n "DerivativesEvent" libs/cmi_common/cmi_common/events/__init__.py` et refléter chaque occurrence)
- Modify: `libs/cmi_common/cmi_common/kafka/topics.py` (`CANDLES = "market.candle.events"` + partitions : suivre la ligne DERIVATIVES)
- Modify: `scripts/create-topics.sh` (si les topics y sont listés — `grep derivatives scripts/create-topics.sh` et refléter)
- Test: `tests/test_candle_event.py`

- [ ] **Step 1 : Test qui échoue**

```python
"""CandleEvent round-trips through the shared registry like every event."""

from decimal import Decimal

from cmi_common.events import parse_event
from cmi_common.events.base import Source
from cmi_common.events.market import CandleEvent
from cmi_common.kafka import Topic


def test_candle_event_roundtrip() -> None:
    e = CandleEvent(
        source=Source.KRAKEN, symbol="BTC", interval="1h",
        open=Decimal("100"), high=Decimal("110"), low=Decimal("90"),
        close=Decimal("105"), volume=Decimal("12.5"),
    )
    decoded = parse_event(e.model_dump_json())
    assert isinstance(decoded, CandleEvent)
    assert decoded.vwap is None            # absent ≠ 0
    assert decoded.trades is None
    assert decoded.venue == "kraken"
    assert e.partition_key() == "BTC"
    assert Topic.CANDLES.value == "market.candle.events"
```

Vérifier `Source.KRAKEN` existe (sinon l'ajouter à l'enum Source, même style). Vérifier la signature réelle de `parse_event` (elle peut prendre bytes/str/dict — adapter l'appel). Run → FAIL.

- [ ] **Step 2 : Implémenter** — `EventType.CANDLE = "CandleEvent"` ; dans market.py :

```python
class CandleEvent(BaseEvent):
    """One OHLC candle from the execution venue, on ``market.candle.events``.

    The forming candle is republished on every sweep with the same
    (occurred_at, symbol, interval) key — consumers must upsert, not insert
    (the ``candles`` table's writer contract, see its model docstring).
    """

    event_type: Literal[EventType.CANDLE] = EventType.CANDLE
    symbol: str
    venue: str = "kraken"
    interval: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    vwap: Decimal | None = None
    volume: Decimal = Decimal(0)
    trades: int | None = Field(default=None, ge=0)

    def partition_key(self) -> str:
        return self.symbol
```

`occurred_at` (hérité de BaseEvent) porte le début de bougie — le producteur (Task 8) le pose explicitement. Registre + Topic + create-topics.sh.

- [ ] **Step 3 : Vérifier** — `pytest tests/test_candle_event.py -q` → PASS ; `pytest -q` ; lint.

- [ ] **Step 4 : Commit**

```bash
git add libs/cmi_common/cmi_common/events scripts/create-topics.sh libs/cmi_common/cmi_common/kafka/topics.py tests/test_candle_event.py
git commit -m "feat(events): CandleEvent et topic market.candle.events"
```

---

### Task 8 (A) : Bascule collector-kraken + persistance + diffusion

**Files:**
- Modify: `services/collector-kraken/app/main.py` (EventProducer — patron `services/collector-binance-futures/app/main.py`)
- Modify: `services/collector-kraken/app/application/sweeper.py` + `store.py` (publier au lieu d'écrire ; supprimer `save_candles`)
- Modify: `services/collector-kraken/app/domain/mapper.py` (pure : `OhlcCandle` → `CandleEvent`)
- Modify: `services/collector-kraken/app/infrastructure/repository.py` (supprimer `upsert_candles` ; garder `last_candle_epoch`, depth, pairs)
- Modify: `services/api-gateway/app/persister.py` + `main.py` (handler upsert + Topic.CANDLES au consumer persister)
- Modify: `services/websocket-gateway/app/consumer.py` (14ᵉ topic) + `tests/test_broadcast_topics.py`
- Modify: `frontend/src/lib/types/events.ts` + `LiveEventStream.tsx` (CandleEvent, libellé `Bougie`, UNTRACEABLE)
- Test: `tests/test_snapshot_persister.py` (cas candle), tests collector-kraken existants (les localiser : `grep -rl "collector-kraken\|kraken" tests/ | head`)

- [ ] **Step 1 : Lire d'abord** — `sweeper.py` en entier (comment `save_candles` est appelé, ce que contient `OhlcCandle`), `main.py` du collector, les tests kraken existants. Adapter les steps suivants à la réalité et signaler tout écart.

- [ ] **Step 2 : Mapper pur + test qui échoue** — fonction dans `domain/mapper.py` :

```python
def candle_event(c: OhlcCandle, *, interval: str) -> CandleEvent:
    """Un OhlcCandle → l'événement publié. occurred_at = début de bougie."""
    return CandleEvent(
        source=Source.KRAKEN,
        occurred_at=datetime.fromtimestamp(c.epoch, tz=UTC),
        symbol=c.symbol,
        interval=interval,
        open=c.open, high=c.high, low=c.low, close=c.close,
        vwap=c.vwap, volume=c.volume, trades=c.trades,
    )
```

(adapter les noms d'attributs à la vraie dataclass `OhlcCandle` — les lire, ne pas deviner ; si `OhlcCandle` porte déjà l'interval, simplifier). Test unitaire pur dans le fichier de tests kraken existant (ou `tests/test_kraken_candle_event.py`), y compris : vwap absent → None.

- [ ] **Step 3 : Bascule du sweeper** — injecter un `EventProducer` (créé/démarré/arrêté dans le `main.py` du collector, patron binance-futures) jusqu'au point où `store.save_candles(...)` est appelé ; remplacer par la publication de chaque `candle_event(...)` sur `Topic.CANDLES`. Supprimer `KrakenStore.save_candles` et `KrakenRepository.upsert_candles` (le curseur `last_candle_epoch` RESTE — il lira désormais ce que le persister écrit ; les chevauchements dus au lag sont absorbés par l'upsert). La profondeur (`save_depth`) ne change pas. Adapter les tests kraken existants (fake producer au lieu de fake store pour les bougies).

- [ ] **Step 4 : Handler persister upsert + test** — dans `tests/test_snapshot_persister.py`, ajouter :

```python
async def test_candle_event_upserts() -> None:
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    from cmi_common.events.market import CandleEvent
    await p.handle(
        CandleEvent(source=Source.KRAKEN, symbol="BTC", interval="1h",
                    open=Decimal("1"), high=Decimal("2"), low=Decimal("1"),
                    close=Decimal("2"), volume=Decimal("3"))
    )
    assert session.committed and len(session.executed) == 1
    sql = str(session.executed[0])
    assert "ON CONFLICT" in sql and "DO UPDATE" in sql   # bougie en formation réécrite
```

Handler dans `persister.py` :

```python
    async def _save_candle(self, e: CandleEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.CANDLES.value, event_type_of(e)).inc()
        async with self._db._sessionmaker() as s:
            stmt = insert(Candle).values(
                time=e.occurred_at, symbol=e.symbol, interval=e.interval,
                open=e.open, high=e.high, low=e.low, close=e.close,
                vwap=e.vwap, volume=e.volume, trades=e.trades or 0,
                source=e.venue,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["time", "symbol", "interval"],
                set_={c: getattr(stmt.excluded, c)
                      for c in ("open", "high", "low", "close", "vwap",
                                "volume", "trades")},
            )
            await s.execute(stmt)
            await s.commit()
```

(+ branche dans `handle()`, import `Candle`, `Topic.CANDLES` au consumer persister de main.py). Note : `trades or 0` est ici légitime — la colonne est NOT NULL default 0 et un compte de trades absent vaut « inconnu », mais la table existante ne le distingue pas ; ne pas changer le schéma de `candles` dans ce chantier.

- [ ] **Step 5 : Diffusion + front** — `Topic.CANDLES` dans `BROADCAST_TOPICS` + assert dans `tests/test_broadcast_topics.py` ; interface `CandleWsEvent` dans `events.ts` + entrée `TYPE_META` (`Bougie`, `default`) + `UNTRACEABLE_TYPES`.

- [ ] **Step 6 : Vérifier** — `pytest -q` complet backend ; `cd frontend && npm run typecheck && npm run test:run` ; lint partout.

- [ ] **Step 7 : Commit**

```bash
git add services/collector-kraken services/api-gateway/app/persister.py services/api-gateway/app/main.py services/websocket-gateway/app/consumer.py tests/ frontend/src/lib/types/events.ts frontend/src/components/command/LiveEventStream.tsx
git commit -m "feat(collector-kraken): publier les bougies sur kafka, persistance et diffusion"
```

---

### Task 9 : Vérification finale + docs

- [ ] **Step 1 : Gates** — racine : `python -m pytest -q` (0 échec) ; `python -m ruff check libs services` et `python -m black --check libs services` (aucune NOUVELLE erreur vs baseline — comparer par `git stash` si doute). Frontend : `npm run typecheck && npm run test:run && npm run build`.

- [ ] **Step 2 : Revue null-vs-zéro dédiée** — sur `git diff <base>..HEAD` : chercher `?? 0`, `|| 0`, `or 0`, coercitions sur nullables, un « absent » devenu 0/False confiant. Le seul `or 0` toléré est celui documenté du handler candle (`trades`).

- [ ] **Step 3 : Docs** — README : table des topics §4 (+ `market.candle.events` | collector-kraken | api-gateway persister, websocket-gateway) ; ligne du graph §1 `collector-kraken` (remplacer « Postgres candles/depth (pas de Kafka) » par « market.candle.events (carnet : Postgres direct) ») ; §5 ligne websocket-gateway « 14 topics ». CLAUDE.md : mettre à jour la ligne du bandeau (« pipeline » : ai-worker-haiku consomme…) si besoin et la mention websocket-gateway « broadcast 10 topics » → 14. Commit `docs: refleter la market data unifiee sur kafka`.

- [ ] **Step 4 : Récap final** — rappeler l'ordre de déploiement : `make migrate` (0011) AVANT le déploiement du persister ; `scripts/create-topics.sh` (ou équivalent) pour `market.candle.events` avant le déploiement du collector.
