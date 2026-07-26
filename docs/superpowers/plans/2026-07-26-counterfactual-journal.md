# Journal contrefactuel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enregistrer chaque analyse avec son contexte décisionnel complet, et permettre de mesurer hors ligne — sans passer un seul ordre — si les appels IA et les seuils de risque créent de la valeur.

**Architecture:** `ai-worker-sonnet` publie un `JournalEntryEvent` sur un nouveau topic Kafka ; l'api-gateway le persiste dans une hypertable `decision_journal`, puis la complète à réception des événements risque et exécution. Les prix futurs et le P&L simulé ne sont pas stockés : ils sont calculés à la demande depuis l'hypertable `prices`.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2.0 async, aiokafka, Alembic, TimescaleDB, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-07-26-counterfactual-journal-design.md`

---

## Correction d'architecture par rapport à la spec

La spec (§9) dit : *« Le journal est écrit par ai-worker-sonnet »*. **C'est
infaisable tel quel.**

`services/ai-worker-sonnet/app/main.py` construit le worker avec un cache Redis,
un producteur Kafka et un client Claude — **aucune connexion base**. Et
`CLAUDE.md` pose que l'api-gateway est le persister unique :

> *api-gateway never writes. It's a Kafka→Postgres persister with GET endpoints only.*

Donner une base à `ai-worker-sonnet` introduirait un second plan d'écriture et un
nouveau mode de panne dans un service dont la seule raison d'être est de dépenser
du quota LLM avec parcimonie.

**Le plan retient donc :** le worker Sonnet *produit* l'événement de journal ;
l'api-gateway l'*écrit*. La spec sera corrigée à l'issue de l'implémentation.

---

## Découpage : moitié A maintenant, moitié B avec la déduplication

Le journal a deux moitiés, et une seule est implémentable aujourd'hui.

| Moitié | Contenu | Statut |
|---|---|---|
| **A** | contexte du signal, verdict Sonnet, verdict risque, résultats marché, P&L simulé, cohortes | **ce plan** |
| **B** | discriminants de dérive, verdicts d'ombre, `dedup_trigger`, rejeu de politiques | plan de déduplication |

Les colonnes de la moitié B sont créées dès maintenant (nullables, non
alimentées). C'est précisément pourquoi le journal passe en premier : il établit
la table et le chemin d'écriture, et le plan de déduplication n'a plus qu'à
remplir ses colonnes.

`scripts/replay_policies.py` est **hors de ce plan** : il appelle
`dedup.should_call`, qui n'existe pas encore.

---

## Contexte indispensable

### Conventions du projet

- Événements : Pydantic v2 dans `libs/cmi_common/cmi_common/events/`,
  `BaseEvent` avec `extra="forbid"` et `frozen=True`. Tout champ non déclaré est
  rejeté à la construction.
- Topics : `libs/cmi_common/cmi_common/kafka/topics.py`, enum `Topic` +
  `TOPIC_EVENT` + `TOPIC_PARTITIONS` — **les trois doivent être mis à jour ensemble**.
- Persistance : `services/api-gateway/app/persister.py`, dispatch par `isinstance`.
- Datetimes en base : UTC **avec** fuseau. Vérifié en production : `signals.time`,
  `prices.time`, `decisions.created_at` et `trades.created_at` sont tous
  `timestamp with time zone`, et la session tourne en UTC. Le docstring de
  `read_api._utcnow_naive` prétend le contraire et a déjà été corrigé.
- Tests : à plat dans `tests/`. **Ne jamais charger un module de service sous un
  nom commençant par `app.`** — `tests/conftest.py` fait échouer la collecte.
  Utiliser `from service_modules import load_service_module` (deux arguments,
  l'alias est dérivé).
- `pyproject.toml` fixe `asyncio_mode = "auto"` : pas de marqueur nécessaire.
- Commandes : `make lint` (ruff + black + mypy), `make test`.

### État de la production (mesuré 2026-07-26)

Utile pour dimensionner et pour ne pas s'étonner des volumes :

- 7 735 analyses / 24 h, 51 symboles analysés, 211 symboles dans `prices`
- 11 appels Sonnet / jour, 1 validation, **0 trade**
- `prices` : ~64 s entre deux points par symbole
- **`prices` ne remonte qu'à 2026-07-25 19:30** — l'horizon +24 h n'est calculable
  pour aucune ligne aujourd'hui. Attendu, pas un bug.

### Échecs de tests pré-existants

`tests/test_bluesky_provider.py` (×2) et `tests/test_raw_content_model.py` (×1)
échouent sur `master` pour des raisons sans rapport. **Ne pas les corriger.**
Toute autre défaillance est imputable au travail en cours.

---

## Structure des fichiers

**Créés**

| Fichier | Responsabilité |
|---|---|
| `libs/cmi_common/cmi_common/events/journal.py` | `JournalEntryEvent` |
| `migrations/alembic/versions/0009_decision_journal.py` | hypertable + rétention 180 j |
| `services/api-gateway/app/journal_sim.py` | simulation de chemin + frais — **pure, sans I/O** |
| `services/api-gateway/app/journal_query.py` | requêtes prix/horizons + agrégation de cohortes |
| `services/api-gateway/app/journal_api.py` | routeur `GET /systems/journal/summary` |
| `services/ai-worker-sonnet/app/journal.py` | construction de l'événement — **pure** |

**Modifiés**

| Fichier | Changement |
|---|---|
| `libs/cmi_common/cmi_common/events/base.py` | `EventType.JOURNAL_ENTRY` |
| `libs/cmi_common/cmi_common/events/__init__.py` | export |
| `libs/cmi_common/cmi_common/kafka/topics.py` | `Topic.JOURNAL` + les deux tables |
| `libs/cmi_common/cmi_common/db/models.py` | modèle `DecisionJournal` |
| `libs/cmi_common/cmi_common/db/__init__.py` | export |
| `services/ai-worker-sonnet/app/worker.py` | émission de l'événement |
| `services/api-gateway/app/persister.py` | écriture + complétion |
| `services/api-gateway/app/main.py` | topic consommé + montage du routeur |
| `services/api-gateway/app/read_contract.py` | contrat de `systems/journal/summary` |
| `tests/test_read_contract.py` | assertion de contrat |

`journal_api.py` est un routeur séparé plutôt qu'un ajout à `read_api.py` : ce
dernier dépasse déjà 990 lignes, et `main.py` monte déjà les routeurs par
`include_router`, donc le coût d'un module de plus est nul.

**Panneau frontend : hors périmètre.** Tant que les effectifs restent sous 30, le
résumé ne renvoie que des `null` ; construire une interface pour afficher du vide
serait prématuré. À faire quand les données existeront.

---

## Task 1 : `JournalEntryEvent`

**Files:**
- Create: `libs/cmi_common/cmi_common/events/journal.py`
- Modify: `libs/cmi_common/cmi_common/events/base.py`
- Modify: `libs/cmi_common/cmi_common/events/__init__.py`
- Test: `tests/test_journal_event.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_journal_event.py`:

```python
"""JournalEntryEvent — l'enregistrement d'audit d'une décision d'appel IA.

Chaque analyse produit une ligne, escaladée ou non. Les champs de la moitié B
(discriminants de déduplication) sont déclarés dès maintenant mais restent nuls
jusqu'au chantier de déduplication.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cmi_common.events.journal import JournalEntryEvent


def _minimal(**kw):
    base = dict(
        symbol="BTC",
        signal_event_id="sig-1",
        factors={"momentum": 0.5, "volume": 0.0, "sentiment": 0.3, "liquidity": 0.5},
        features={"price_change_pct_24h": 7.5},
        score=42,
        confidence=0.7,
        factors_present=2,
        escalated=False,
        sonnet_called=False,
    )
    base.update(kw)
    return JournalEntryEvent(**base)


def test_non_escalated_analysis_is_journalled() -> None:
    """Le groupe témoin de Q2 : sans les non escaladées, le gate d'opportunité
    reste invérifiable pour toujours."""
    ev = _minimal()
    assert ev.escalated is False
    assert ev.sonnet_called is False
    assert ev.sonnet_validated is None


def test_dedup_fields_default_to_null() -> None:
    """Moitié B : déclarée, pas encore alimentée."""
    ev = _minimal()
    assert ev.dedup_trigger is None
    assert ev.drift_momentum is None
    assert ev.cooldown_verdict is None


def test_sonnet_verdict_round_trips_through_json() -> None:
    ev = _minimal(
        escalated=True, sonnet_called=True, sonnet_validated=True,
        sonnet_score=61, sonnet_confidence=0.52, sonnet_direction="long",
    )
    restored = JournalEntryEvent.model_validate(ev.model_dump(mode="json"))
    assert restored.sonnet_validated is True
    assert restored.sonnet_score == 61


def test_dominant_factor_is_free_text_including_mixed() -> None:
    """`mixed` est une valeur légitime : quand deux contributions sont à moins de
    0.02 l'une de l'autre, « dominant » n'a pas de sens."""
    ev = _minimal(dominant_factor="mixed", dominant_factor_share=0.26)
    assert ev.dominant_factor == "mixed"


def test_unknown_field_is_rejected() -> None:
    """BaseEvent est en extra='forbid' : une faute de frappe doit exploser à la
    construction, pas produire une colonne silencieusement vide."""
    with pytest.raises(ValidationError):
        _minimal(sonnet_validated_typo=True)


def test_factors_present_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _minimal(factors_present=9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_journal_event.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'cmi_common.events.journal'`.

- [ ] **Step 3: Add the EventType member**

In `libs/cmi_common/cmi_common/events/base.py`, add to `EventType` after
`EXECUTION`:

```python
    JOURNAL_ENTRY = "JournalEntryEvent"
```

- [ ] **Step 4: Create the event**

Create `libs/cmi_common/cmi_common/events/journal.py`:

```python
"""Audit record of one AI-call decision, escalated or not.

Written for every analysis — including the ones that never reach the analyst.
Without that control group the opportunity gate can never be evaluated: you
would only ever observe the signals it let through.

Half of these fields (the dedup discriminants) are declared here but populated
by a later change; they are nullable so the two halves can ship independently.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .base import BaseEvent, EventType, Source


class JournalEntryEvent(BaseEvent):
    """Published on ``journal.entries`` by ai-worker-sonnet, persisted by the
    api-gateway. Carries the full decision context so an outcome can later be
    explained, not merely scored."""

    event_type: Literal[EventType.JOURNAL_ENTRY] = EventType.JOURNAL_ENTRY
    source: Source = Source.AI_SONNET

    symbol: str
    signal_event_id: str

    # --- état décisionnel ---------------------------------------------------
    factors: dict[str, float] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    score: int = Field(..., ge=0, le=100)
    confidence: float = Field(..., ge=0, le=1)
    factors_present: int = Field(..., ge=0, le=4)
    escalated: bool = False

    # --- contexte d'entrée théorique : le « pourquoi » ----------------------
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward_ratio: float | None = None
    volatility_1h: float | None = None
    volatility_24h: float | None = None
    dominant_factor: str | None = None
    dominant_factor_share: float | None = None
    market_cap_rank: int | None = None

    # --- verdict de l'analyste ----------------------------------------------
    sonnet_called: bool = False
    sonnet_validated: bool | None = None
    sonnet_score: int | None = None
    sonnet_confidence: float | None = None
    sonnet_direction: str | None = None
    skip_reason: str | None = None

    # --- moitié B : alimentée par le chantier de déduplication --------------
    cooldown_verdict: bool | None = None
    dedup_verdict: bool | None = None
    dedup_trigger: str | None = None
    drift_momentum: float | None = None
    drift_volume: float | None = None
    drift_sentiment: float | None = None
    drift_liquidity: float | None = None
    sign_flip_chg: bool | None = None
    sign_flip_sentiment: bool | None = None
    score_anchor: int | None = None
    factors_present_anchor: int | None = None
    seconds_since_anchor: int | None = None
    regime: str | None = None
    regime_anchor: str | None = None
    dedup_version: str | None = None
    dedup_quantile: float | None = None
    dedup_deltas: dict[str, float | None] = Field(default_factory=dict)

    def partition_key(self) -> str:
        return self.symbol
```

- [ ] **Step 5: Export it — and register it in the union**

In `libs/cmi_common/cmi_common/events/__init__.py`:

1. add `from .journal import JournalEntryEvent` alongside the other event imports;
2. add `"JournalEntryEvent"` to `__all__`;
3. **add `JournalEntryEvent` to the `AnyEvent` union** (after `ControlCommandEvent`).

Step 3 is not optional and not cosmetic. `kafka/consumer.py` decodes every
message through `parse_event`, which validates against that discriminated union.
An event missing from it **publishes without error and is rejected on
consumption** — a silent failure on the side that matters, which would only
surface once the persister is wired up in Task 7.

Pin it with a round-trip test rather than trusting the export list:

```python
def test_round_trips_through_parse_event() -> None:
    from cmi_common.events import parse_event

    ev = _minimal(escalated=True, sonnet_called=True, sonnet_validated=False)
    decoded = parse_event(ev.as_kafka_value())
    assert isinstance(decoded, JournalEntryEvent)
    assert decoded.event_id == ev.event_id
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_journal_event.py tests/test_events.py -v`

Expected: 6 passed in the new file, `test_events.py` unchanged.

- [ ] **Step 7: Commit**

```bash
git add libs/cmi_common/cmi_common/events/journal.py libs/cmi_common/cmi_common/events/base.py libs/cmi_common/cmi_common/events/__init__.py tests/test_journal_event.py
git commit -m "feat(events): JournalEntryEvent for the counterfactual journal

Written for every analysis, escalated or not: without the non-escalated control
group the opportunity gate can never be evaluated, since you would only observe
the signals it let through.

Dedup discriminant fields are declared nullable now so the journal and the
deduplication work can ship independently.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2 : topic `journal.entries`

**Files:**
- Modify: `libs/cmi_common/cmi_common/kafka/topics.py`
- Test: `tests/test_journal_topic.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_journal_topic.py`:

```python
"""The journal topic is registered in all three tables that must agree."""

from __future__ import annotations

from cmi_common.events.journal import JournalEntryEvent
from cmi_common.kafka.topics import TOPIC_EVENT, TOPIC_PARTITIONS, Topic


def test_topic_is_declared() -> None:
    assert Topic.JOURNAL.value == "journal.entries"


def test_event_binding_is_declared() -> None:
    assert TOPIC_EVENT[Topic.JOURNAL] is JournalEntryEvent


def test_partition_count_is_declared() -> None:
    """A topic missing from TOPIC_PARTITIONS is created with the broker default,
    which silently caps consumer-group parallelism."""
    assert TOPIC_PARTITIONS[Topic.JOURNAL] >= 3


def test_every_topic_appears_in_both_tables() -> None:
    """Guards the three-table invariant: adding a Topic member without its
    binding or partition count is a silent misconfiguration."""
    assert set(TOPIC_EVENT) == set(Topic)
    assert set(TOPIC_PARTITIONS) == set(Topic)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_journal_topic.py -v`

Expected: FAIL — `AttributeError: JOURNAL`.

- [ ] **Step 3: Register the topic in all three places**

In `libs/cmi_common/cmi_common/kafka/topics.py`:

Add the import: `from ..events.journal import JournalEntryEvent`

Add to the `Topic` enum after `EXECUTION`:

```python
    JOURNAL = "journal.entries"
```

Add to `TOPIC_EVENT`:

```python
    Topic.JOURNAL: JournalEntryEvent,
```

Add to `TOPIC_PARTITIONS` — one line per analysis makes this the highest-volume
topic after price:

```python
    Topic.JOURNAL: 6,
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_journal_topic.py tests/test_control_topic.py tests/test_execution_topic.py -v`

Expected: all pass.

- [ ] **Step 5: Register the topic on the broker**

In `scripts/create-topics.sh`, add before the closing `echo "Done."`:

```bash
create journal.entries          6   15552000000  # 180d, matches the table retention
```

The helper signature is `create <topic> <partitions> [retention_ms]`, default
retention 7 days.

**Observation à remonter, hors périmètre :** ce script ne crée que 9 des 11
topics — `execution.events` et `control.commands` sont absents. Le compose active
`KAFKA_CFG_AUTO_CREATE_TOPICS_ENABLE: "true"`, donc ils existent quand même, mais
avec les partitions par défaut du broker et non celles déclarées dans
`TOPIC_PARTITIONS`. Le signaler dans le rapport ; ne pas le corriger ici.

- [ ] **Step 6: Commit**

```bash
git add libs/cmi_common/cmi_common/kafka/topics.py scripts/create-topics.sh tests/test_journal_topic.py
git commit -m "feat(kafka): journal.entries topic

Adds a test asserting every Topic member appears in both TOPIC_EVENT and
TOPIC_PARTITIONS -- a member missing from either is a silent misconfiguration
rather than an error.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3 : migration et modèle ORM

**Files:**
- Create: `migrations/alembic/versions/0009_decision_journal.py`
- Modify: `libs/cmi_common/cmi_common/db/models.py`
- Modify: `libs/cmi_common/cmi_common/db/__init__.py`

- [ ] **Step 1: Write the migration**

Create `migrations/alembic/versions/0009_decision_journal.py`:

```python
"""decision_journal hypertable

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_journal",
        # `time` is in the primary key because TimescaleDB rejects
        # create_hypertable when the partitioning column is absent from it.
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("signal_event_id", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        # état décisionnel
        sa.Column("factors", postgresql.JSONB, server_default="{}"),
        sa.Column("features", postgresql.JSONB, server_default="{}"),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("factors_present", sa.SmallInteger, nullable=False),
        sa.Column("escalated", sa.Boolean, server_default=sa.false(), nullable=False),
        # contexte d'entrée
        sa.Column("entry_price", sa.Numeric(38, 12)),
        sa.Column("stop_loss", sa.Numeric(38, 12)),
        sa.Column("take_profit", sa.Numeric(38, 12)),
        sa.Column("risk_reward_ratio", sa.Float),
        sa.Column("volatility_1h", sa.Float),
        sa.Column("volatility_24h", sa.Float),
        sa.Column("dominant_factor", sa.String(16)),
        sa.Column("dominant_factor_share", sa.Float),
        sa.Column("market_cap_rank", sa.Integer),
        # verdict analyste
        sa.Column("sonnet_called", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("sonnet_validated", sa.Boolean),
        sa.Column("sonnet_score", sa.Integer),
        sa.Column("sonnet_confidence", sa.Float),
        sa.Column("sonnet_direction", sa.String(8)),
        sa.Column("skip_reason", sa.String(64)),
        # moitié B — déduplication
        sa.Column("cooldown_verdict", sa.Boolean),
        sa.Column("dedup_verdict", sa.Boolean),
        sa.Column("dedup_trigger", sa.String(8)),
        sa.Column("drift_momentum", sa.Float),
        sa.Column("drift_volume", sa.Float),
        sa.Column("drift_sentiment", sa.Float),
        sa.Column("drift_liquidity", sa.Float),
        sa.Column("sign_flip_chg", sa.Boolean),
        sa.Column("sign_flip_sentiment", sa.Boolean),
        sa.Column("score_anchor", sa.Integer),
        sa.Column("factors_present_anchor", sa.SmallInteger),
        sa.Column("seconds_since_anchor", sa.Integer),
        sa.Column("regime", sa.String(16)),
        sa.Column("regime_anchor", sa.String(16)),
        sa.Column("dedup_version", sa.String(32)),
        sa.Column("dedup_quantile", sa.Float),
        sa.Column("dedup_deltas", postgresql.JSONB, server_default="{}"),
        # aval
        sa.Column("decision_event_id", sa.String(64)),
        sa.Column("risk_event_id", sa.String(64)),
        sa.Column("risk_verdict", sa.String(16)),
        sa.Column("risk_reason", sa.Text),
        sa.Column("execution_event_id", sa.String(64)),
        sa.Column("execution_kind", sa.String(16)),
        sa.Column("fill_price", sa.Numeric(38, 12)),
        sa.Column("realized_pnl", sa.Numeric(38, 12)),
    )
    op.create_index("ix_journal_symbol_time", "decision_journal", ["symbol", "time"])
    op.create_index("ix_journal_correlation", "decision_journal", ["correlation_id"])
    # Partial index: the completion path looks up only rows that carry a
    # decision, which is a small minority of the table.
    op.create_index(
        "ix_journal_decision_event",
        "decision_journal",
        ["decision_event_id"],
        postgresql_where=sa.text("decision_event_id IS NOT NULL"),
    )
    op.execute(
        "SELECT create_hypertable('decision_journal', 'time', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('decision_journal', INTERVAL '180 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.execute("SELECT remove_retention_policy('decision_journal', if_not_exists => TRUE)")
    op.drop_table("decision_journal")
```

- [ ] **Step 2: Add the ORM model**

In `libs/cmi_common/cmi_common/db/models.py`, add after the `PipelineRejection`
class (mirror its style; `datetime`, `String`, `Text`, `Boolean`, `Integer`,
`Float`, `Numeric`, `JSONB` are already imported):

```python
class DecisionJournal(Base):
    """One row per analysis — escalated or not -> hypertable on ``time``.

    The non-escalated rows are the control group. Without them "would this
    signal have deserved an analysis?" is unanswerable, because the only
    observable population would be the one the gate already selected.
    """

    __tablename__ = "decision_journal"

    time: Mapped[datetime] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    signal_event_id: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(64))

    factors: Mapped[dict] = mapped_column(JSONB, default=dict)
    features: Mapped[dict] = mapped_column(JSONB, default=dict)
    score: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    factors_present: Mapped[int] = mapped_column(SmallInteger)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)

    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
    risk_reward_ratio: Mapped[float | None] = mapped_column(Float, default=None)
    volatility_1h: Mapped[float | None] = mapped_column(Float, default=None)
    volatility_24h: Mapped[float | None] = mapped_column(Float, default=None)
    dominant_factor: Mapped[str | None] = mapped_column(String(16), default=None)
    dominant_factor_share: Mapped[float | None] = mapped_column(Float, default=None)
    market_cap_rank: Mapped[int | None] = mapped_column(Integer, default=None)

    sonnet_called: Mapped[bool] = mapped_column(Boolean, default=False)
    sonnet_validated: Mapped[bool | None] = mapped_column(Boolean, default=None)
    sonnet_score: Mapped[int | None] = mapped_column(Integer, default=None)
    sonnet_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    sonnet_direction: Mapped[str | None] = mapped_column(String(8), default=None)
    skip_reason: Mapped[str | None] = mapped_column(String(64), default=None)

    cooldown_verdict: Mapped[bool | None] = mapped_column(Boolean, default=None)
    dedup_verdict: Mapped[bool | None] = mapped_column(Boolean, default=None)
    dedup_trigger: Mapped[str | None] = mapped_column(String(8), default=None)
    drift_momentum: Mapped[float | None] = mapped_column(Float, default=None)
    drift_volume: Mapped[float | None] = mapped_column(Float, default=None)
    drift_sentiment: Mapped[float | None] = mapped_column(Float, default=None)
    drift_liquidity: Mapped[float | None] = mapped_column(Float, default=None)
    sign_flip_chg: Mapped[bool | None] = mapped_column(Boolean, default=None)
    sign_flip_sentiment: Mapped[bool | None] = mapped_column(Boolean, default=None)
    score_anchor: Mapped[int | None] = mapped_column(Integer, default=None)
    factors_present_anchor: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    seconds_since_anchor: Mapped[int | None] = mapped_column(Integer, default=None)
    regime: Mapped[str | None] = mapped_column(String(16), default=None)
    regime_anchor: Mapped[str | None] = mapped_column(String(16), default=None)
    dedup_version: Mapped[str | None] = mapped_column(String(32), default=None)
    dedup_quantile: Mapped[float | None] = mapped_column(Float, default=None)
    dedup_deltas: Mapped[dict] = mapped_column(JSONB, default=dict)

    decision_event_id: Mapped[str | None] = mapped_column(String(64), default=None)
    risk_event_id: Mapped[str | None] = mapped_column(String(64), default=None)
    risk_verdict: Mapped[str | None] = mapped_column(String(16), default=None)
    risk_reason: Mapped[str | None] = mapped_column(Text, default=None)
    execution_event_id: Mapped[str | None] = mapped_column(String(64), default=None)
    execution_kind: Mapped[str | None] = mapped_column(String(16), default=None)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(38, 12), default=None)
```

`SmallInteger` is **not** currently imported in that module — add it to the
existing `from sqlalchemy import (...)` block, in alphabetical position.

Register it in `HYPERTABLES` (the documentation-only registry near the top of
`models.py`): `"decision_journal": "time"`.

Export `DecisionJournal` from `libs/cmi_common/cmi_common/db/__init__.py` — both
the `from .models import (...)` list and `__all__`, alphabetically.

- [ ] **Step 3: Validate the migration offline**

No local database. Run:

`cd migrations && python -m alembic upgrade head --sql`

Read the emitted `0008 -> 0009` section and confirm, explicitly:

1. `CREATE TABLE decision_journal` with `PRIMARY KEY (time, event_id)` — `time`
   present, otherwise `create_hypertable` is rejected at deploy;
2. the three `CREATE INDEX`, including the partial one with its `WHERE` clause;
3. `SELECT create_hypertable(...)` **after** the CREATE TABLE;
4. `SELECT add_retention_policy(...)` with `INTERVAL '180 days'`;
5. `UPDATE alembic_version SET version_num='0009'`.

Paste the emitted SQL verbatim in your report. Then round-trip:

`cd migrations && python -m alembic downgrade 0008 --sql`

- [ ] **Step 4: Verify model/migration agreement**

Run:

```
python -c "from cmi_common.db import DecisionJournal as J; print(len(J.__table__.columns), 'colonnes'); print([c.name for c in J.__table__.primary_key])"
```

Expected: the column count matches the migration, primary key `['time', 'event_id']`.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/ -rN --tb=no`

Expected: 3 failed (the known pre-existing ones), everything else passing.
`tests/test_raw_content_model.py` inspects `HYPERTABLES` — if your registry entry
changes its failure mode, that IS your problem.

- [ ] **Step 6: Commit**

```bash
git add migrations/alembic/versions/0009_decision_journal.py libs/cmi_common/cmi_common/db/models.py libs/cmi_common/cmi_common/db/__init__.py
git commit -m "feat(db): decision_journal hypertable, 180-day retention

Retention is double that of events_signal: a 24h outcome horizon plus a
multi-week statistical window makes this table slower to mature than anything
else in the system.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4 : simulation de chemin (pure, sans base)

C'est le cœur analytique du journal, et il est entièrement testable hors base.

**Files:**
- Create: `services/api-gateway/app/journal_sim.py`
- Test: `tests/test_journal_sim.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_journal_sim.py`:

```python
"""Simulation d'issue de position sur le chemin de prix.

Comparer le prix d'entrée au prix final surestime systématiquement la
performance : une position stoppée à -5 %, dont le prix remonte ensuite, se
lirait comme gagnante. La simulation parcourt donc le chemin et applique la
première borne touchée.
"""

from __future__ import annotations

from service_modules import load_service_module

sim = load_service_module("api-gateway", "journal_sim")


def _path(*prices: float):
    """(offset_secondes, prix) espacés d'une minute, chronologiques."""
    return [(i * 60, p) for i, p in enumerate(prices)]


def test_long_hits_take_profit() -> None:
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0,
        path=_path(100.0, 104.0, 111.0, 108.0),
    )
    assert r.outcome == "take_profit"
    assert r.exit_price == 111.0
    assert r.seconds_held == 120


def test_long_hits_stop_loss() -> None:
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0,
        path=_path(100.0, 97.0, 94.0),
    )
    assert r.outcome == "stop_loss"
    assert r.pnl_gross_pct < 0


def test_stopped_then_recovered_is_a_loss() -> None:
    """Le cas qui justifie à lui seul le parcours de chemin. Comparer entrée et
    prix final donnerait +20 % sur une position qui a été stoppée à -6 %."""
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0,
        path=_path(100.0, 94.0, 105.0, 120.0),
    )
    assert r.outcome == "stop_loss"
    assert r.exit_price == 94.0
    assert r.pnl_net_pct < 0


def test_short_hits_take_profit_on_the_way_down() -> None:
    r = sim.simulate_path(
        entry=100.0, direction="short", stop_loss=105.0, take_profit=90.0,
        path=_path(100.0, 96.0, 89.0),
    )
    assert r.outcome == "take_profit"
    assert r.pnl_gross_pct > 0


def test_short_hits_stop_loss_on_the_way_up() -> None:
    r = sim.simulate_path(
        entry=100.0, direction="short", stop_loss=105.0, take_profit=90.0,
        path=_path(100.0, 103.0, 106.0),
    )
    assert r.outcome == "stop_loss"
    assert r.pnl_gross_pct < 0


def test_neither_bound_touched_marks_to_market() -> None:
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0,
        path=_path(100.0, 101.0, 103.0),
    )
    assert r.outcome == "horizon"
    assert r.exit_price == 103.0


def test_empty_path_reports_no_data_not_zero() -> None:
    """Un trou de collecte doit produire une absence, jamais un P&L de zéro —
    sinon une panne de collecteur se lirait comme une position neutre."""
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0, path=[],
    )
    assert r.outcome == "no_data"
    assert r.pnl_net_pct is None


def test_fees_are_charged_on_both_sides() -> None:
    """0.16 % par côté, cohérent avec read_api.map_portfolio_trade. Les ignorer
    rendrait profitable une stratégie à faible espérance."""
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0,
        path=_path(100.0, 110.0), fee_pct=0.0016,
    )
    assert r.pnl_gross_pct == 10.0
    assert abs(r.pnl_net_pct - (10.0 - 0.32)) < 0.01


def test_a_flat_path_is_a_small_loss_after_fees() -> None:
    """Une position qui ne bouge pas perd les frais. Un simulateur qui rendrait
    zéro ici surestimerait toute stratégie à faible espérance."""
    r = sim.simulate_path(
        entry=100.0, direction="long", stop_loss=95.0, take_profit=110.0,
        path=_path(100.0, 100.0, 100.0),
    )
    assert r.pnl_gross_pct == 0.0
    assert r.pnl_net_pct < 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_journal_sim.py -v`

Expected: FAIL — `FileNotFoundError` on `journal_sim.py`.

- [ ] **Step 3: Implement**

Create `services/api-gateway/app/journal_sim.py`:

```python
"""Walk a price path and report how a position would have ended.

Comparing entry price to the price at the horizon systematically overstates
performance: a position stopped out at -5% whose price later recovers would read
as a winner. This walks the path in order and applies whichever bound is touched
first.

Known limitations, all biasing the result optimistic: no slippage, no book
depth, ~60s price sampling (a sub-minute wick through the stop is invisible),
no execution latency. A marginally profitable result here should be treated as
unprofitable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# Taker fee per side, matching read_api.map_portfolio_trade.
DEFAULT_FEE_PCT = 0.0016


@dataclass(frozen=True, slots=True)
class TradeOutcome:
    outcome: str                  # stop_loss | take_profit | horizon | no_data
    exit_price: float | None
    seconds_held: int | None
    pnl_gross_pct: float | None
    pnl_net_pct: float | None


_NO_DATA = TradeOutcome("no_data", None, None, None, None)


def simulate_path(
    *,
    entry: float,
    direction: str,
    stop_loss: float,
    take_profit: float,
    path: Sequence[tuple[int, float]],
    fee_pct: float = DEFAULT_FEE_PCT,
) -> TradeOutcome:
    """``path`` is (seconds_since_entry, price), chronological.

    An empty path returns ``no_data`` rather than a zero P&L: a collector outage
    must read as an absence, not as a neutral position.
    """
    if entry <= 0 or not path:
        return _NO_DATA

    is_long = direction != "short"

    for seconds, price in path:
        if is_long:
            hit_stop, hit_target = price <= stop_loss, price >= take_profit
        else:
            hit_stop, hit_target = price >= stop_loss, price <= take_profit
        # Stop checked first: within one sampling interval we cannot know which
        # came first, and assuming the favourable one is how a backtest lies.
        if hit_stop:
            return _settle("stop_loss", entry, price, seconds, is_long, fee_pct)
        if hit_target:
            return _settle("take_profit", entry, price, seconds, is_long, fee_pct)

    seconds, price = path[-1]
    return _settle("horizon", entry, price, seconds, is_long, fee_pct)


def _settle(
    outcome: str, entry: float, exit_price: float, seconds: int,
    is_long: bool, fee_pct: float,
) -> TradeOutcome:
    move = (exit_price - entry) / entry
    gross = (move if is_long else -move) * 100
    # Fees apply on entry and exit regardless of direction or outcome.
    net = gross - fee_pct * 200
    return TradeOutcome(outcome, exit_price, seconds, round(gross, 6), round(net, 6))
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_journal_sim.py -v`

Expected: 9 passed.

- [ ] **Step 5: Lint**

Run: `python -m ruff check services/api-gateway/app/journal_sim.py tests/test_journal_sim.py`

Expected: clean. If ruff reports `UP017` on a `timezone.utc`, none is used here —
any finding is yours.

- [ ] **Step 6: Commit**

```bash
git add services/api-gateway/app/journal_sim.py tests/test_journal_sim.py
git commit -m "feat(api-gateway): price-path trade simulation with fees

Walks the path rather than comparing endpoints: a position stopped out at -5%
that later recovers would otherwise read as a winner. Checks the stop before the
target within a sampling interval, since assuming the favourable one is how a
backtest lies to you.

An empty path returns no_data rather than a zero P&L, so a collector outage
reads as an absence instead of a neutral position.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5 : construction de l'entrée de journal (pure)

**Files:**
- Create: `services/ai-worker-sonnet/app/journal.py`
- Test: `tests/test_journal_builder.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_journal_builder.py`:

```python
"""Construction de l'entrée de journal à partir d'une AnalysisEvent."""

from __future__ import annotations

from cmi_common.events import AnalysisEvent

from service_modules import load_service_module

jb = load_service_module("ai-worker-sonnet", "journal")


def _analysis(**kw) -> AnalysisEvent:
    base = dict(
        symbol="BTC", opportunity_score=42, confidence=0.7, reason="r",
        factors_present=3,
        meta={"factors": {"momentum": 0.8, "volume": 0.2,
                          "sentiment": 0.4, "liquidity": 0.5},
              "features": {"market_cap_rank": 150}},
    )
    base.update(kw)
    return AnalysisEvent(**base)


def test_dominant_factor_uses_weighted_contribution() -> None:
    """Pas un argmax brut : avec la saturation, momentum et volume valent tous
    deux 1.0 et l'argmax serait arbitraire. On classe sur poids x facteur."""
    ev = jb.build_entry(_analysis(), escalated=False)
    # momentum 0.8*0.35 = 0.280 ; sentiment 0.4*0.25 = 0.100 ; volume 0.2*0.25 = 0.050
    assert ev.dominant_factor == "momentum"
    assert abs(ev.dominant_factor_share - 0.280) < 1e-6


def test_near_tie_is_reported_as_mixed() -> None:
    """Quand deux contributions sont à moins de 0.02, « dominant » n'a pas de
    sens — inventer un gagnant créerait une cohorte fictive."""
    a = _analysis(meta={"factors": {"momentum": 0.30, "volume": 0.42,
                                    "sentiment": 0.0, "liquidity": 0.0},
                        "features": {}})
    # momentum 0.105 ; volume 0.105 -> écart nul
    ev = jb.build_entry(a, escalated=False)
    assert ev.dominant_factor == "mixed"


def test_saturated_factors_do_not_produce_an_arbitrary_winner() -> None:
    """Le cas DEXE réel : momentum et volume tous deux saturés à 1.0."""
    a = _analysis(meta={"factors": {"momentum": 1.0, "volume": 1.0,
                                    "sentiment": 0.35, "liquidity": 0.5},
                        "features": {}})
    ev = jb.build_entry(a, escalated=True)
    # momentum 0.35 vs volume 0.25 : écart 0.10 > 0.02, momentum gagne légitimement
    assert ev.dominant_factor == "momentum"


def test_correlation_id_is_carried_for_downstream_joins() -> None:
    a = _analysis()
    ev = jb.build_entry(a, escalated=True)
    assert ev.correlation_id == a.correlation_id
    assert ev.signal_event_id == a.event_id


def test_skip_reason_recorded_when_the_call_was_suppressed() -> None:
    ev = jb.build_entry(_analysis(), escalated=True, skip_reason="cooldown")
    assert ev.sonnet_called is False
    assert ev.skip_reason == "cooldown"


def test_market_cap_rank_is_lifted_from_features() -> None:
    """Axe de cohorte et clé du MAX_AGE différencié de la déduplication."""
    ev = jb.build_entry(_analysis(), escalated=False)
    assert ev.market_cap_rank == 150


def test_missing_factors_do_not_raise() -> None:
    """Une analyse sans meta.factors doit produire une ligne, pas une exception :
    perdre une ligne de journal est acceptable, casser le worker ne l'est pas."""
    ev = jb.build_entry(_analysis(meta={}), escalated=False)
    assert ev.dominant_factor is None
    assert ev.factors == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_journal_builder.py -v`

Expected: FAIL — `FileNotFoundError` on `journal.py`.

- [ ] **Step 3: Implement**

Create `services/ai-worker-sonnet/app/journal.py`:

```python
"""Build a journal entry from an analysis, plus whatever the analyst decided.

Pure: no I/O, no Redis, no Kafka. The worker publishes what this returns.
"""

from __future__ import annotations

from cmi_common.events import AnalysisEvent
from cmi_common.events.journal import JournalEntryEvent

# Scorer weights — must mirror ScorerConfig in ai-worker-haiku/app/scorer.py.
FACTOR_WEIGHTS = {
    "momentum": 0.35,
    "volume": 0.25,
    "sentiment": 0.25,
    "liquidity": 0.15,
}
# Below this gap between the top two contributions, "dominant" is meaningless.
DOMINANCE_MARGIN = 0.02


def dominant_factor(factors: dict[str, float]) -> tuple[str | None, float | None]:
    """Factor with the largest *contribution to the score*, i.e. weight x value.

    Not a raw argmax: momentum and volume both saturate at 1.0 on a strong move
    (observed on DEXE), so an argmax would pick between them arbitrarily. Ties
    are reported as "mixed" rather than inventing a winner and, with it, a
    spurious cohort.
    """
    if not factors:
        return None, None
    ranked = sorted(
        ((w * factors.get(name, 0.0), name) for name, w in FACTOR_WEIGHTS.items()),
        reverse=True,
    )
    top_share, top_name = ranked[0]
    if len(ranked) > 1 and top_share - ranked[1][0] < DOMINANCE_MARGIN:
        return "mixed", round(top_share, 6)
    return top_name, round(top_share, 6)


def build_entry(
    analysis: AnalysisEvent,
    *,
    escalated: bool,
    sonnet_called: bool = False,
    sonnet_validated: bool | None = None,
    sonnet_score: int | None = None,
    sonnet_confidence: float | None = None,
    sonnet_direction: str | None = None,
    skip_reason: str | None = None,
) -> JournalEntryEvent:
    factors = analysis.meta.get("factors") or {}
    features = analysis.meta.get("features") or {}
    name, share = dominant_factor(factors)
    rank = features.get("market_cap_rank")
    return JournalEntryEvent(
        correlation_id=analysis.correlation_id,
        symbol=analysis.symbol,
        signal_event_id=analysis.event_id,
        factors=factors,
        features=features,
        score=analysis.opportunity_score,
        confidence=analysis.confidence,
        factors_present=analysis.factors_present,
        escalated=escalated,
        dominant_factor=name,
        dominant_factor_share=share,
        market_cap_rank=int(rank) if rank is not None else None,
        sonnet_called=sonnet_called,
        sonnet_validated=sonnet_validated,
        sonnet_score=sonnet_score,
        sonnet_confidence=sonnet_confidence,
        sonnet_direction=sonnet_direction,
        skip_reason=skip_reason,
    )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_journal_builder.py -v`

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add services/ai-worker-sonnet/app/journal.py tests/test_journal_builder.py
git commit -m "feat(sonnet): pure journal-entry builder

dominant_factor ranks by weighted contribution rather than raw factor value:
momentum and volume both saturate at 1.0 on a strong move (observed on DEXE), so
an argmax would pick between them arbitrarily. Near-ties report 'mixed' instead
of inventing a winner and a spurious cohort with it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6 : le worker Sonnet émet le journal

**Files:**
- Modify: `services/ai-worker-sonnet/app/worker.py`
- Test: `tests/test_sonnet_journals.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sonnet_journals.py`:

```python
"""Le worker Sonnet journalise chaque analyse, y compris celles qu'il ignore."""

from __future__ import annotations

from cmi_common.events import AnalysisEvent
from cmi_common.kafka import Topic

from service_modules import load_service_module

sw = load_service_module("ai-worker-sonnet", "worker")


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    async def publish(self, topic, event) -> None:
        self.published.append((topic, event))


class FakeCache:
    """Refuse toujours l'appel : simule cooldown ou budget épuisé."""

    async def get_json(self, key):
        return 1

    async def set_json(self, key, value, ttl_seconds=0):
        return None

    async def allow(self, key, limit, window):
        return False


def _analysis(**kw) -> AnalysisEvent:
    base = dict(symbol="BTC", opportunity_score=42, confidence=0.7, reason="r",
                meta={"factors": {"momentum": 0.8}, "features": {}})
    base.update(kw)
    return AnalysisEvent(**base)


def _journals(producer) -> list:
    return [e for t, e in producer.published if t is Topic.JOURNAL]


async def test_non_escalated_analysis_is_journalled() -> None:
    """Le groupe témoin : sans ces lignes, le gate reste invérifiable."""
    p = FakeProducer()
    w = sw.SonnetWorker(claude=None, producer=p, cache=FakeCache())
    await w.handle(_analysis(escalate=False))
    entries = _journals(p)
    assert len(entries) == 1
    assert entries[0].escalated is False
    assert entries[0].sonnet_called is False


async def test_suppressed_call_is_journalled_with_its_reason() -> None:
    """Un appel supprimé disparaissait dans les logs. C'est exactement ce que le
    journal doit capter."""
    p = FakeProducer()
    w = sw.SonnetWorker(claude=None, producer=p, cache=FakeCache())
    await w.handle(_analysis(escalate=True))
    entries = _journals(p)
    assert len(entries) == 1
    assert entries[0].escalated is True
    assert entries[0].sonnet_called is False
    assert entries[0].skip_reason == "cooldown_or_budget"


async def test_journal_failure_does_not_break_the_worker() -> None:
    """Perdre une ligne de journal est acceptable ; interrompre le pipeline de
    trading ne l'est pas."""
    class Exploding(FakeProducer):
        async def publish(self, topic, event):
            if topic is Topic.JOURNAL:
                raise RuntimeError("kafka down")
            await super().publish(topic, event)

    p = Exploding()
    w = sw.SonnetWorker(claude=None, producer=p, cache=FakeCache())
    await w.handle(_analysis(escalate=False))   # ne doit pas lever
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sonnet_journals.py -v`

Expected: FAIL — no journal event is published.

- [ ] **Step 3: Implement**

In `services/ai-worker-sonnet/app/worker.py`:

Add the imports:

```python
from cmi_common.kafka import EventProducer, Topic  # Topic already imported
from .journal import build_entry
```

Replace the body of `handle` with:

```python
    async def handle(self, event: BaseEvent) -> None:
        if not isinstance(event, AnalysisEvent):
            return
        EVENTS_CONSUMED.labels(SERVICE, Topic.ANALYSIS.value, event.event_type).inc()

        # Senior analyst only intervenes on important signals.
        if not event.escalate:
            await self._journal(build_entry(event, escalated=False))
            return

        # Budget/cooldown gate — the only place we spend the subscription.
        if not await self._may_call(event.symbol):
            logger.info("sonnet skip %s (cooldown/budget)", event.symbol)
            await self._journal(
                build_entry(event, escalated=True, skip_reason="cooldown_or_budget")
            )
            return

        decision = await self._validate(event)
        if decision is None:
            await self._journal(
                build_entry(event, escalated=True, sonnet_called=True,
                            sonnet_validated=False)
            )
            return

        await self._producer.publish(Topic.DECISION, decision)
        EVENTS_PRODUCED.labels(SERVICE, Topic.DECISION.value, decision.event_type).inc()
        await self._journal(
            build_entry(
                event, escalated=True, sonnet_called=True, sonnet_validated=True,
                sonnet_score=decision.opportunity_score,
                sonnet_confidence=decision.confidence,
                sonnet_direction=decision.direction,
            ),
            decision_event_id=decision.event_id,
        )
```

Add the helper:

```python
    async def _journal(self, entry, *, decision_event_id: str | None = None) -> None:
        """Best effort. Losing a journal row is acceptable; stalling the trading
        pipeline because the audit trail failed is not."""
        try:
            if decision_event_id is not None:
                entry = entry.model_copy(update={"decision_event_id": decision_event_id})
            await self._producer.publish(Topic.JOURNAL, entry)
        except Exception:  # noqa: BLE001
            logger.warning("journal publish failed for %s", entry.symbol, exc_info=True)
```

`JournalEntryEvent` needs a `decision_event_id` field for this — add it to
`libs/cmi_common/cmi_common/events/journal.py` in the "aval" group:

```python
    decision_event_id: str | None = None
```

`BaseEvent` is `frozen=True`, so `model_copy(update=...)` is the correct way to
set it — direct assignment raises.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_sonnet_journals.py tests/test_sonnet_budget.py tests/test_journal_builder.py -v`

Expected: all pass. `test_sonnet_budget.py` exercises the same worker — if it
breaks, the refactor changed behaviour it depends on.

- [ ] **Step 5: Commit**

```bash
git add services/ai-worker-sonnet/app/worker.py libs/cmi_common/cmi_common/events/journal.py tests/test_sonnet_journals.py
git commit -m "feat(sonnet): publish a journal entry for every analysis

Including the ones never escalated and the ones suppressed by cooldown or
budget: a suppressed call previously vanished into the logs, which is exactly
the observation the journal exists to keep.

Publishing is best effort -- losing a journal row is acceptable, stalling the
trading pipeline because the audit trail failed is not.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7 : l'api-gateway persiste et complète

**Files:**
- Modify: `services/api-gateway/app/persister.py`
- Modify: `services/api-gateway/app/main.py`
- Test: `tests/test_journal_persister.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_journal_persister.py`:

```python
"""Persistance du journal et complétion par les événements aval."""

from __future__ import annotations

from cmi_common.events.base import Source
from cmi_common.events.journal import JournalEntryEvent
from cmi_common.events.risk import RiskRejectedEvent

from service_modules import load_service_module

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


def _entry(**kw) -> JournalEntryEvent:
    base = dict(symbol="BTC", signal_event_id="sig-1", score=42,
                confidence=0.7, factors_present=2)
    base.update(kw)
    return JournalEntryEvent(**base)


async def test_journal_entry_is_written() -> None:
    s = FakeSession()
    p = persister_mod.Persister(FakeDb(s))
    await p.handle(_entry())
    assert s.committed is True
    assert len(s.executed) == 1
    assert s.executed[0].table.name == "decision_journal"


async def test_risk_rejection_completes_the_matching_row() -> None:
    """Le refus du risque enrichit la ligne de journal existante plutôt que
    d'en créer une seconde — sans quoi Q1 compterait deux fois."""
    s = FakeSession()
    p = persister_mod.Persister(FakeDb(s))
    await p.handle(
        RiskRejectedEvent(source=Source.RISK_ENGINE, symbol="BTC",
                          reason="confidence 0.45 below floor",
                          decision_event_id="dec-1")
    )
    tables = [getattr(st, "table", None) for st in s.executed]
    assert any(t is not None and t.name == "decision_journal" for t in tables)


async def test_rejection_without_decision_id_writes_no_journal_update() -> None:
    """Sans identifiant de décision, il n'y a pas de ligne à rattacher : mieux
    vaut ne rien écrire qu'écrire au hasard."""
    s = FakeSession()
    p = persister_mod.Persister(FakeDb(s))
    await p.handle(
        RiskRejectedEvent(source=Source.RISK_ENGINE, symbol="BTC", reason="x")
    )
    tables = [getattr(st, "table", None) for st in s.executed]
    assert not any(t is not None and t.name == "decision_journal" for t in tables)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_journal_persister.py -v`

Expected: FAIL — nothing writes to `decision_journal`.

- [ ] **Step 3: Implement**

In `services/api-gateway/app/persister.py`:

Extend the db import with `DecisionJournal`, and add:

```python
from cmi_common.events.journal import JournalEntryEvent
```

Add to `Persister.handle`, **before** the `RiskRejectedEvent` branch:

```python
        elif isinstance(event, JournalEntryEvent):
            await self._save_journal(event)
```

Add the two methods:

```python
    async def _save_journal(self, e: JournalEntryEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.JOURNAL.value, e.event_type).inc()
        payload = e.model_dump(
            mode="json",
            exclude={"event_type", "schema_version", "source", "occurred_at", "meta"},
        )
        payload["time"] = _naive_utc(e.occurred_at)
        async with self._db._sessionmaker() as s:  # noqa: SLF001
            await s.execute(insert(DecisionJournal).values(**payload).on_conflict_do_nothing())
            await s.commit()

    async def _complete_journal(self, *, decision_event_id: str, **values) -> None:
        """Enrich the existing journal row rather than inserting a second one —
        a duplicate would double-count in every cohort."""
        if not decision_event_id:
            return
        async with self._db._sessionmaker() as s:  # noqa: SLF001
            await s.execute(
                update(DecisionJournal)
                .where(DecisionJournal.decision_event_id == decision_event_id)
                .values(**values)
            )
            await s.commit()
```

In `_save_rejection`, append the completion call:

```python
        await self._complete_journal(
            decision_event_id=e.decision_event_id or "",
            risk_verdict="rejected",
            risk_reason=e.reason,
        )
```

In `_save_trade` (the `RiskApprovedEvent` handler), append:

```python
        await self._complete_journal(
            decision_event_id=e.decision_event_id or "",
            risk_verdict="approved",
            risk_event_id=e.event_id,
            entry_price=e.entry_price,
            stop_loss=e.stop_loss,
            take_profit=e.take_profit,
            risk_reward_ratio=e.risk_reward_ratio,
        )
```

In `_update_trade` (the `ExecutionEvent` handler), the join key is
`risk_event_id`, not `decision_event_id` — add a dedicated update:

```python
        async with self._db._sessionmaker() as s:  # noqa: SLF001
            await s.execute(
                update(DecisionJournal)
                .where(DecisionJournal.risk_event_id == e.risk_event_id)
                .values(
                    execution_event_id=e.event_id,
                    execution_kind=e.kind,
                    fill_price=e.fill_price,
                    realized_pnl=e.pnl,
                )
            )
            await s.commit()
```

- [ ] **Step 4: Consume the topic**

In `services/api-gateway/app/main.py`, add `Topic.JOURNAL` to the consumer's
topic list (line 23).

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_journal_persister.py tests/test_rejection_persister.py tests/test_execution_persister.py -v`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add services/api-gateway/app/persister.py services/api-gateway/app/main.py tests/test_journal_persister.py
git commit -m "feat(api-gateway): persist journal entries and complete them downstream

Risk and execution events enrich the existing journal row rather than inserting
a second one; a duplicate would double-count in every cohort. Execution joins on
risk_event_id, not decision_event_id -- that is the link ExecutionEvent actually
carries.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8 : prix aux horizons et agrégation de cohortes

**Files:**
- Create: `services/api-gateway/app/journal_query.py`
- Test: `tests/test_journal_summary.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_journal_summary.py`:

```python
"""Agrégation du résumé — pure, sans base."""

from __future__ import annotations

from service_modules import load_service_module

jq = load_service_module("api-gateway", "journal_query")


def _rows(n, *, validated, pnl):
    return [{"sonnet_validated": validated, "pnl_net_pct": pnl} for _ in range(n)]


def test_below_minimum_sample_returns_null_not_a_number() -> None:
    """Un intervalle de confiance sur n=3 est plus dangereux qu'une absence de
    réponse : il invite à agir."""
    out = jq.compare_groups(_rows(3, validated=True, pnl=5.0),
                            _rows(3, validated=False, pnl=-1.0))
    assert out["mean_a"] is None
    assert out["mean_b"] is None
    assert out["n_a"] == 3
    assert out["insufficient_sample"] is True


def test_at_minimum_sample_returns_the_comparison() -> None:
    out = jq.compare_groups(_rows(30, validated=True, pnl=5.0),
                            _rows(30, validated=False, pnl=-1.0))
    assert out["insufficient_sample"] is False
    assert out["mean_a"] == 5.0
    assert out["mean_b"] == -1.0
    assert out["delta"] == 6.0


def test_rows_without_an_outcome_are_excluded_not_counted_as_zero() -> None:
    """Une ligne non mûre n'est pas une performance nulle."""
    rows = _rows(30, validated=True, pnl=4.0) + [
        {"sonnet_validated": True, "pnl_net_pct": None} for _ in range(10)
    ]
    out = jq.compare_groups(rows, _rows(30, validated=False, pnl=0.0))
    assert out["n_a"] == 30
    assert out["mean_a"] == 4.0


def test_cohort_minimum_applies_per_cohort_not_globally() -> None:
    """Croiser les axes fragmente vite l'échantillon ; le plancher doit mordre
    cohorte par cohorte."""
    rows = (
        [{"cohort": "major", "pnl_net_pct": 2.0}] * 40
        + [{"cohort": "small", "pnl_net_pct": 9.0}] * 5
    )
    out = jq.by_cohort(rows, key="cohort")
    assert out["major"]["mean"] == 2.0
    assert out["small"]["mean"] is None
    assert out["small"]["n"] == 5


def test_maturity_is_counted_per_horizon() -> None:
    """Une ligne de moins de 24 h est exploitable à +1 h et pas à +24 h. Un
    décompte global laisserait une analyse à +24 h se croire alimentée."""
    rows = [{"pnl_1h": 1.0, "pnl_24h": None} for _ in range(40)]
    assert jq.matured(rows, "pnl_1h") == 40
    assert jq.matured(rows, "pnl_24h") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_journal_summary.py -v`

Expected: FAIL — `FileNotFoundError` on `journal_query.py`.

- [ ] **Step 3: Implement the pure part**

Create `services/api-gateway/app/journal_query.py`:

```python
"""Journal aggregation: forward-price lookup plus pure summary shaping.

The shaping refuses to answer below a minimum sample. A confidence interval
computed on n=3 is more dangerous than no answer at all, because it invites
action.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Below this, every comparison returns null.
MIN_SAMPLE = 30
# Beyond this gap a stored price is not "the price at that instant" -- return
# nothing rather than something misleading, so a collector outage reads as an
# absence.
PRICE_TOLERANCE = timedelta(minutes=10)
DEFAULT_HORIZONS = ("1h", "4h", "24h")


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _outcomes(rows: Iterable[dict], field: str = "pnl_net_pct") -> list[float]:
    """Rows without an outcome are excluded, never counted as zero -- an
    immature row is not a flat trade."""
    return [r[field] for r in rows if r.get(field) is not None]


def matured(rows: Iterable[dict], field: str) -> int:
    """Sample size **per horizon**. A row younger than 24h is usable at +1h and
    not at +24h; a global count would let a 24h analysis believe it had data."""
    return len(_outcomes(rows, field))


def compare_groups(
    group_a: Iterable[dict], group_b: Iterable[dict], field: str = "pnl_net_pct"
) -> dict[str, Any]:
    a, b = _outcomes(group_a, field), _outcomes(group_b, field)
    thin = len(a) < MIN_SAMPLE or len(b) < MIN_SAMPLE
    mean_a, mean_b = (None, None) if thin else (_mean(a), _mean(b))
    return {
        "n_a": len(a),
        "n_b": len(b),
        "mean_a": mean_a,
        "mean_b": mean_b,
        "delta": None if thin else round(mean_a - mean_b, 6),
        "insufficient_sample": thin,
    }


def by_cohort(
    rows: Iterable[dict], *, key: str, field: str = "pnl_net_pct"
) -> dict[str, dict[str, Any]]:
    """The minimum applies **per cohort**. Crossing axes fragments a thin sample
    fast, so a global floor would wave through cohorts of four."""
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(str(row.get(key)), []).append(row)
    out: dict[str, dict[str, Any]] = {}
    for name, bucket in buckets.items():
        values = _outcomes(bucket, field)
        enough = len(values) >= MIN_SAMPLE
        out[name] = {"n": len(values), "mean": _mean(values) if enough else None}
    return out


async def price_path(
    session: AsyncSession, symbol: str, start, horizon: str
) -> list[tuple[int, float]]:
    """Chronological (seconds_since_start, price) over [start, start+horizon]."""
    rows = (
        await session.execute(
            text(
                "SELECT EXTRACT(EPOCH FROM (time - :start))::int AS s, price_usd "
                "FROM prices WHERE symbol = :symbol "
                "AND time >= :start AND time <= :start + CAST(:horizon AS interval) "
                "ORDER BY time"
            ),
            {"symbol": symbol, "start": start, "horizon": horizon},
        )
    ).all()
    return [(int(s), float(p)) for s, p in rows]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_journal_summary.py -v`

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/journal_query.py tests/test_journal_summary.py
git commit -m "feat(api-gateway): journal aggregation with a per-cohort sample floor

Comparisons below 30 observations return null rather than a number: a confidence
interval on n=3 is more dangerous than no answer, because it invites action. The
floor applies per cohort, since crossing axes fragments a thin sample fast.

Rows without an outcome are excluded rather than counted as zero -- an immature
row is not a flat trade -- and maturity is counted per horizon.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9 : endpoint `GET /systems/journal/summary`

**Files:**
- Create: `services/api-gateway/app/journal_api.py`
- Modify: `services/api-gateway/app/main.py`
- Modify: `services/api-gateway/app/read_contract.py`
- Modify: `tests/test_read_contract.py`

- [ ] **Step 1: Add the contract entry**

In `services/api-gateway/app/read_contract.py`, add inside `CONTRACT` after
`systems/funnel`:

```python
    "systems/journal/summary": {
        "window", "horizons", "sample", "q1_rejected_vs_approved",
        "q2_gate_discrimination", "q3_sonnet_value", "cohorts", "updated_at",
    },
```

- [ ] **Step 2: Add the contract assertion**

In `tests/test_read_contract.py`, add before the manifest-coverage test:

```python
async def test_systems_journal_summary_contract() -> None:
    resp = await journal_api.journal_summary(window="30d", session=_FakeSession(40))
    _assert_keys("systems/journal/summary", resp)
```

and add the module load next to the existing `read_api` one:

```python
journal_api = load_service_module("api-gateway", "journal_api")
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/test_read_contract.py -v`

Expected: FAIL — `journal_api` does not exist, and the manifest-coverage test
also reports `systems/journal/summary` as unasserted.

- [ ] **Step 4: Implement the route**

Create `services/api-gateway/app/journal_api.py`:

```python
"""Read-only summary of the counterfactual journal.

Every statistic carries its own sample size, and any comparison below the
minimum returns null. The `sample` block is first in the response on purpose:
the reader should see how much data backs a number before the number itself.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .journal_query import MIN_SAMPLE, by_cohort, compare_groups, matured
from .routers import get_session_dep

router = APIRouter(tags=["journal"])

HORIZONS = tuple(
    h.strip() for h in os.getenv("COUNTERFACTUAL_HORIZONS", "1h,4h,24h").split(",") if h.strip()
)
_WINDOWS = {"7d": 7, "30d": 30, "90d": 90}


@router.get("/systems/journal/summary")
async def journal_summary(
    window: str = Query("30d", pattern="^(7d|30d|90d)$"),
    session: AsyncSession = Depends(get_session_dep),
) -> dict:
    since = datetime.now(tz=timezone.utc) - timedelta(days=_WINDOWS[window])
    rows = [
        dict(r._mapping)
        for r in (
            await session.execute(
                text(
                    "SELECT symbol, escalated, sonnet_called, sonnet_validated, "
                    "       risk_verdict, risk_reason, confidence, dominant_factor, "
                    "       dedup_trigger, market_cap_rank "
                    "FROM decision_journal WHERE time >= :since"
                ),
                {"since": since},
            )
        ).all()
    ]

    escalated = [r for r in rows if r.get("escalated")]
    called = [r for r in escalated if r.get("sonnet_called")]
    validated = [r for r in called if r.get("sonnet_validated")]
    refused = [r for r in called if r.get("sonnet_validated") is False]
    approved = [r for r in rows if r.get("risk_verdict") == "approved"]
    rejected = [r for r in rows if r.get("risk_verdict") == "rejected"]

    return {
        "window": window,
        "horizons": list(HORIZONS),
        # First on purpose: how much data backs a number, before the number.
        "sample": {
            "min_required": MIN_SAMPLE,
            "analyses": len(rows),
            "escalated": len(escalated),
            "sonnet_called": len(called),
            "validated": len(validated),
            "approved": len(approved),
            "matured": {h: matured(rows, f"pnl_{h}") for h in HORIZONS},
        },
        # Q1 -- were risk rejections right? Stratified by reason: a rejection for
        # low confidence does not read like one for maximum exposure.
        "q1_rejected_vs_approved": compare_groups(rejected, approved),
        # Q2 -- did the gate let value through? Confounded by design: the two
        # populations differ before Sonnet ever intervenes.
        "q2_gate_discrimination": compare_groups(
            [r for r in rows if not r.get("escalated")], escalated
        ),
        # Q3 -- the central question. Clean comparison: both groups passed the
        # same gate and saw the same analyst; only the verdict differs.
        "q3_sonnet_value": compare_groups(validated, refused),
        "cohorts": {
            "by_dominant_factor": by_cohort(rows, key="dominant_factor"),
            "by_dedup_trigger": by_cohort(rows, key="dedup_trigger"),
            "by_symbol": by_cohort(rows, key="symbol"),
        },
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
```

The `pnl_{horizon}` fields are not yet produced by the query — they arrive in
Task 10. Until then `matured` reports 0 for every horizon, which is correct: no
outcome has been computed.

- [ ] **Step 5: Mount the router**

In `services/api-gateway/app/main.py`, after the existing `include_router` calls:

```python
app.include_router(journal_api.router)
```

and extend the module import on line 13 to include `journal_api`.

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_read_contract.py tests/test_journal_summary.py -v`

Expected: all pass, including the manifest-coverage test.

- [ ] **Step 7: Commit**

```bash
git add services/api-gateway/app/journal_api.py services/api-gateway/app/main.py services/api-gateway/app/read_contract.py tests/test_read_contract.py
git commit -m "feat(api-gateway): GET /systems/journal/summary

Q3 compares Sonnet-validated against Sonnet-rejected within the escalated
population: comparing escalated to non-escalated would measure the gate, not the
analyst, since the two populations already differ before Sonnet intervenes.

The sample block leads the response so a reader sees how much data backs a
number before the number itself.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10 : résultats marché branchés sur le résumé

**Files:**
- Modify: `services/api-gateway/app/journal_api.py`
- Test: `tests/test_journal_outcomes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_journal_outcomes.py`:

```python
"""Attachement des résultats marché aux lignes de journal."""

from __future__ import annotations

from service_modules import load_service_module

japi = load_service_module("api-gateway", "journal_api")
sim = load_service_module("api-gateway", "journal_sim")


def test_row_without_entry_price_gets_no_outcome() -> None:
    """Une analyse jamais convertie en décision n'a pas de niveaux : elle n'a
    donc pas de P&L, et surtout pas un P&L de zéro."""
    row = {"symbol": "BTC", "entry_price": None, "stop_loss": None,
           "take_profit": None, "sonnet_direction": None}
    out = japi.attach_outcome(row, path=[(0, 100.0), (60, 110.0)], horizon="1h")
    assert out["pnl_1h"] is None


def test_row_with_levels_gets_a_simulated_outcome() -> None:
    row = {"symbol": "BTC", "entry_price": 100.0, "stop_loss": 95.0,
           "take_profit": 110.0, "sonnet_direction": "long"}
    out = japi.attach_outcome(row, path=[(0, 100.0), (60, 111.0)], horizon="1h")
    assert out["pnl_1h"] is not None
    assert out["outcome_1h"] == "take_profit"


def test_empty_path_yields_null_not_zero() -> None:
    row = {"symbol": "BTC", "entry_price": 100.0, "stop_loss": 95.0,
           "take_profit": 110.0, "sonnet_direction": "long"}
    out = japi.attach_outcome(row, path=[], horizon="4h")
    assert out["pnl_4h"] is None
    assert out["outcome_4h"] == "no_data"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_journal_outcomes.py -v`

Expected: FAIL — `attach_outcome` does not exist.

- [ ] **Step 3: Implement**

In `services/api-gateway/app/journal_api.py`, add the import
`from .journal_sim import simulate_path` and:

```python
def attach_outcome(row: dict, *, path: list[tuple[int, float]], horizon: str) -> dict:
    """Attach a simulated outcome for one horizon.

    A row with no entry levels never became a decision, so it has no P&L -- and
    emphatically not a P&L of zero, which would drag every average toward the
    middle and make a stalled pipeline look neutral.
    """
    entry = row.get("entry_price")
    if not entry or row.get("stop_loss") is None or row.get("take_profit") is None:
        return {**row, f"pnl_{horizon}": None, f"outcome_{horizon}": None}
    result = simulate_path(
        entry=float(entry),
        direction=row.get("sonnet_direction") or "long",
        stop_loss=float(row["stop_loss"]),
        take_profit=float(row["take_profit"]),
        path=path,
    )
    return {**row, f"pnl_{horizon}": result.pnl_net_pct,
            f"outcome_{horizon}": result.outcome}
```

Then, in `journal_summary`, add `time, entry_price, stop_loss, take_profit,
sonnet_direction` to the SELECT column list, and after fetching `rows`:

```python
    for horizon in HORIZONS:
        rows = [
            attach_outcome(
                r,
                path=await price_path(session, r["symbol"], r["time"], horizon),
                horizon=horizon,
            )
            for r in rows
        ]
```

Import `price_path` from `.journal_query`.

**Performance caveat to record, not to pre-optimise:** this issues one price
query per row per horizon. At today's volume (7 735 rows/day, 3 horizons) a 30-day
window is ~700 k queries — far too slow. It is correct and simple; Task 11
measures it and decides whether to batch. Do not optimise before measuring.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_journal_outcomes.py tests/test_read_contract.py -v`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/journal_api.py tests/test_journal_outcomes.py
git commit -m "feat(api-gateway): attach simulated outcomes per horizon

A row with no entry levels gets a null P&L, not zero: counting it as flat would
drag every average toward the middle and make a stalled pipeline look neutral.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 11 : vérification et mesure de performance

- [ ] **Step 1: Full suite**

Run: `python -m pytest tests/ -rN --tb=no`

Expected: collection completes; exactly 3 failures, all pre-existing
(`test_bluesky_provider.py` ×2, `test_raw_content_model.py` ×1). Any other
failure is yours.

- [ ] **Step 2: Lint delta against master**

```bash
python -m ruff check libs services --statistics > /tmp/branch.txt
git stash -u && git checkout master
python -m ruff check libs services --statistics > /tmp/master.txt
git checkout - && git stash pop
diff /tmp/master.txt /tmp/branch.txt
```

Expected: no new finding **category**. Report any count delta with its rule.

- [ ] **Step 3: Offline migration check**

```
cd migrations && python -m alembic upgrade head --sql
cd migrations && python -m alembic downgrade 0008 --sql
```

Expected: the `0009` step emits `CREATE TABLE`, three indexes,
`create_hypertable`, `add_retention_policy`; the downgrade emits the matching
`DROP`s.

- [ ] **Step 4: Measure the summary query cost**

The per-row price lookup in Task 10 is the known risk. Measure it before
deciding anything:

```
curl -s -w "\n%{time_total}s\n" "http://localhost:8000/systems/journal/summary?window=7d" -o /dev/null
```

No local stack is available, so run this **after deployment**, against the VPS:

```
ssh <VPS_USER>@<VPS_HOST> "curl -s -w '%{time_total}s' -o /dev/null \
  'http://localhost:8000/systems/journal/summary?window=7d'"
```

**If it exceeds 5 s, do not optimise inside this plan** — record the measurement
and open a follow-up. The endpoint is analytical, not on any hot path, and a
premature batching rewrite would be harder to review than the measurement is to
take.

- [ ] **Step 5: Post-deploy acceptance**

```bash
ssh <VPS_USER>@<VPS_HOST> 'cd /opt/bottrading && docker compose logs migrate --tail 20'
# expect: Running upgrade 0008 -> 0009

ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-postgres-1 psql -U cmi -d cmi \
  -c \"SELECT hypertable_name FROM timescaledb_information.hypertables \
       WHERE hypertable_name = 'decision_journal';\""
# expect: one row

ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-postgres-1 psql -U cmi -d cmi \
  -c 'SELECT count(*) AS lignes, count(*) FILTER (WHERE escalated) AS escaladees, \
      count(*) FILTER (WHERE sonnet_called) AS appels FROM decision_journal;'"
# expect after ~1h: lignes in the hundreds, escaladees far fewer, appels a handful
```

**The ratio is the real acceptance test.** If `lignes` is close to `escaladees`,
the non-escalated control group is not being written and Q2 will never be
answerable — that is a bug, not a slow start.

---

## Self-review

**Couverture de la spec**

| Exigence | Tâche |
|---|---|
| Hypertable `decision_journal`, rétention 180 j | 3 |
| Discriminants bruts stockés (moitié B) | 1, 3 — colonnes créées, alimentation au plan de déduplication |
| Contexte d'entrée (niveaux, RR, volatilité, facteur dominant) | 1, 3, 5, 7 |
| `price_at` / chemin de prix | 8 |
| Simulation de chemin avec frais | 4 |
| Horizons configurables | 9 (`COUNTERFACTUAL_HORIZONS`) |
| Maturité par horizon | 8, 9 |
| Écriture depuis le worker, persistance côté gateway | 6, 7 |
| Complétion risque et exécution | 7 |
| Q1 / Q2 / Q3 | 9 |
| Cohortes + plancher par cohorte | 8, 9 |
| Chaînage `correlation_id` / liens explicites | 1, 3, 7 |
| Plancher n≥30 → `null` | 8 |

**Non couvert, et pourquoi**

- `scripts/replay_policies.py` — appelle `dedup.should_call`, qui n'existe pas.
  Plan de déduplication.
- `volatility_1h` / `volatility_24h` — colonnes créées (tâches 1 et 3) mais non
  calculées. Elles exigent une fenêtre glissante sur `prices` au moment de
  l'écriture, or l'écriture se fait dans un service sans base. **Le calcul
  reviendra à l'api-gateway au moment de la persistance** ; c'est une tâche à
  part entière et elle mérite son propre plan plutôt qu'un ajout mal placé ici.
  Signalé comme lacune assumée, pas comme oubli.
- Panneau frontend — prématuré tant que le résumé ne renvoie que des `null`.

**Cohérence des types :** `simulate_path` renvoie `TradeOutcome` avec
`pnl_net_pct`, consommé sous ce nom par `attach_outcome` (T10), agrégé sous ce
nom par `compare_groups` et `by_cohort` (T8). `MIN_SAMPLE = 30` est défini une
fois dans `journal_query.py` et importé par `journal_api.py`. `build_entry`
(T5) produit un `JournalEntryEvent` dont tous les champs existent bien dans le
modèle de la tâche 1 et dans la migration de la tâche 3.
