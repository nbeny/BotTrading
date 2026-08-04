# Ouvrir la vanne de décision — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Réparer le défaut de fuseau qui laisse l'axe `positioning` muet en production, rendre visible l'échec d'une tâche périodique, puis calibrer `DECISION_THRESHOLD` par rejeu exact du journal de décision.

**Architecture:** Deux temps. **A** (tâches 1–4) corrige quatre déclarations ORM qui affirment `TIMESTAMP` là où la base stocke `timestamptz`, retire la convention « naive UTC » que ce mensonge a fait proliférer, et fait répondre `/health` en 503 quand une tâche périodique échoue en série. **B** (tâches 5–10) fait voyager `market_sentiment` dans les features au lieu de la mémoire du moteur — ce qui rend la décision fonction pure de sa ligne de journal, donc rejouable exactement — puis livre `scripts/pick_threshold.py`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async + asyncpg, Pydantic v2, FastAPI, Redis, pytest (`asyncio_mode = "auto"`), prometheus-client.

**Spec :** `docs/superpowers/specs/2026-08-04-decision-valve-calibration-design.md`

---

## Ce que l'implémenteur doit savoir avant de commencer

**Les tests vivent dans un seul répertoire racine `tests/`,** pas par service. `pyproject.toml` fixe `testpaths = ["tests"]` et `asyncio_mode = "auto"` — une fonction `async def test_*` s'exécute sans décorateur.

**Ne jamais faire `import app`.** Chaque service embarque un package nommé `app` ; le premier importé fige `sys.modules["app"]` et le suivant se résout au mauvais endroit. `tests/conftest.py` fait échouer la collecte si un module `app` fuit. Utiliser :

```python
from service_modules import load_service_module
engine_mod = load_service_module("decision-engine", "engine")
```

**Le modèle de scoring exclut un axe absent au lieu de le pénaliser.** Donc une valeur non mesurée qui arrive comme une lecture sûre déplace toujours le score dans le sens de cette lecture. À chaque étape de ce plan, la question est : est-ce que `None` et un `0` mesuré restent distinguables ?

**Commandes :** `make lint` (ruff + black --check + mypy), `make format`, `make test`. Un test isolé : `python -m pytest tests/test_x.py::test_y -v`.

---

## Structure des fichiers

**Créés :**

| Fichier | Responsabilité |
|---|---|
| `tests/test_timestamp_columns_are_tz_aware.py` | Garde-fou : aucune colonne temporelle ne se déclare sans fuseau |
| `tests/test_no_naive_utc_convention.py` | Plus aucun site ne dépouille le fuseau avant une requête |
| `tests/test_periodic_task_health.py` | Le registre d'échecs consécutifs et son seuil |
| `tests/test_health_endpoint_degrades.py` | `/health` répond 503 quand une tâche est en panne |
| `tests/test_market_regime_store.py` | La clé de régime, son TTL, `None` vs `0.0` |
| `tests/test_haiku_market_sentiment.py` | haiku estampille la lecture de régime dans les features publiées |
| `tests/test_features_from_replay.py` | `features_from` et son instant de référence explicite |
| `tests/test_decision_engine_stateless.py` | Le moteur ne retient plus rien entre deux événements |
| `services/decision-engine/app/features_map.py` | `raw dict → Features`, pur, importable sans aiokafka |
| `scripts/pick_threshold.py` | Rejeu hors-ligne du journal, présence par axe, choix du seuil |

**Modifiés :**

| Fichier | Changement |
|---|---|
| `libs/cmi_common/cmi_common/db/models.py:54,70,93,111` | Quatre `Mapped[datetime]` nus → `DateTime(timezone=True)` |
| `libs/cmi_common/cmi_common/runner.py` | Registre de santé + compteur de ticks |
| `libs/cmi_common/cmi_common/observability/metrics.py` | Métrique `PERIODIC_TICKS` |
| `libs/cmi_common/cmi_common/observability/__init__.py` | Export |
| `libs/cmi_common/cmi_common/app.py:52-59` | `/health` consulte le registre |
| `services/api-gateway/app/persister.py:50-56,135,180,225` | `_naive_utc` supprimé |
| `services/api-gateway/app/archiver.py:84-89` | idem |
| `services/api-gateway/app/read_api.py:128-137,976-979` | idem |
| `services/api-gateway/app/systems_pipeline.py:202-207` | `_cutoffs` rend un seul instant *aware* |
| `services/ai-worker-haiku/app/features.py` | `MarketRegimeStore` |
| `services/ai-worker-haiku/app/worker.py:60-113` | Route MARKET vers le régime, estampille les features |
| `services/ai-worker-haiku/app/main.py:31-40` | Câble le `MarketRegimeStore` |
| `services/decision-engine/app/engine.py` | Perd son état, sa souscription `sentiment` et ses helpers |
| `services/decision-engine/app/main.py:22-28` | Ne consomme plus `Topic.SENTIMENT` |

---

# Partie A — réparer et rendre visible

## Task 1 : Le garde-fou de fuseau, puis les quatre déclarations

**Files:**
- Create: `tests/test_timestamp_columns_are_tz_aware.py`
- Modify: `libs/cmi_common/cmi_common/db/models.py:54,70,93,111`
- Modify: `libs/cmi_common/cmi_common/db/base.py:25-27` (`TimestampMixin.created_at`)

Quatre modèles déclarent `Mapped[datetime]` sans type explicite. SQLAlchemy le mappe alors sur `DateTime()` — `timezone=False` — et rend le paramètre `$1::TIMESTAMP WITHOUT TIME ZONE`. Les colonnes réelles sont `timestamptz`, vérifiées en production. Toute lecture filtrant sur un datetime *aware* lève `asyncpg.DataError` à l'encodage, avant d'atteindre la base.

Le test porte sur **toutes** les colonnes temporelles, pas sur les quatre connues : c'est ce qui empêche la cinquième.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/test_timestamp_columns_are_tz_aware.py
"""Aucune colonne temporelle ne doit se declarer sans fuseau.

Quatre modeles declaraient `Mapped[datetime]` nu la ou la colonne est
`timestamptz` en base. SQLAlchemy rendait alors le parametre en
`TIMESTAMP WITHOUT TIME ZONE`, et toute lecture filtrant sur un datetime
*aware* levait asyncpg.DataError a l'encodage -- avant meme d'atteindre la
base. collector-binance-futures a echoue a 100% de ses cycles pendant 28
heures sur ce defaut, en se declarant `healthy`, et l'axe positioning n'a
jamais produit une seule lecture en production.

Le test balaie toutes les colonnes plutot que les quatre connues: la
declaration et la colonne ne sont confrontees nulle part ailleurs dans la
suite, ce qui est exactement pourquoi la divergence a vecu.
"""

from __future__ import annotations

from sqlalchemy import DateTime

from cmi_common.db import Base


def _datetime_columns() -> list[tuple[str, DateTime]]:
    return [
        (f"{table.name}.{column.name}", column.type)
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, DateTime)
    ]


def test_the_sweep_actually_finds_columns() -> None:
    """Sans ceci, une erreur de parcours ferait passer le test suivant a vide."""
    assert len(_datetime_columns()) > 20


def test_every_datetime_column_declares_a_timezone() -> None:
    naive = [name for name, type_ in _datetime_columns() if not type_.timezone]
    assert not naive, (
        f"colonnes temporelles declarees sans fuseau: {naive}. "
        "La base les stocke en timestamptz; SQLAlchemy rendra le parametre en "
        "TIMESTAMP WITHOUT TIME ZONE et toute lecture avec un datetime aware "
        "levera asyncpg.DataError a l'encodage."
    )
```

- [ ] **Step 2: Lancer le test et vérifier qu'il échoue**

```bash
python -m pytest tests/test_timestamp_columns_are_tz_aware.py -v
```

Attendu : `test_the_sweep_actually_finds_columns` PASSE, `test_every_datetime_column_declares_a_timezone` **ÉCHOUE** en listant exactement ces **sept** entrées :

```
['decision_journal.time', 'decisions.created_at', 'pipeline_rejections.time',
 'prices.time', 'signals.time', 'tokens.created_at', 'trades.created_at']
```

Si la liste contient autre chose, s'arrêter et le signaler : le périmètre du plan est faux.

**Les trois `created_at` ne viennent pas de `models.py`** mais de `TimestampMixin`
(`base.py:25-27`), dont `Token`, `Decision` et `Trade` héritent. C'est la même cause dans
un second fichier, et les trois colonnes sont `timestamptz` en production — vérifié le
2026-08-04. Elles ne sont pas facultatives : `read_api.py:128-137` filtre précisément sur
`decisions.created_at` et `trades.created_at`, donc rendre son helper *aware* à la tâche 2
introduirait la même `DataError` dans le plan de lecture si le mixin restait naïf.

- [ ] **Step 3: Corriger les quatre déclarations et le mixin**

`DateTime` est déjà importé (`models.py:15`). Quatre remplacements :

```python
# models.py:54 — class Price
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
```

```python
# models.py:70 — class Signal
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
```

```python
# models.py:93 — class PipelineRejection
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
```

```python
# models.py:111 — class DecisionJournal
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
```

Puis le mixin, dans `libs/cmi_common/cmi_common/db/base.py` — il faut y ajouter
`DateTime` à l'import `from sqlalchemy import ...` :

```python
class TimestampMixin:
    #: `timestamptz` en base, comme toutes les colonnes temporelles du schema.
    #: Sans le type explicite, SQLAlchemy rend le parametre sans fuseau et toute
    #: lecture filtrant sur un datetime aware leve asyncpg.DataError -- le meme
    #: defaut qui a rendu l'axe positioning muet, ici pour Token, Decision et
    #: Trade d'un seul coup.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

**Aucune migration.** Les colonnes sont déjà `timestamptz` en production — vérifié table par table le 2026-08-04, `time` comme `created_at`. On corrige la croyance de l'ORM, pas le schéma.

- [ ] **Step 4: Relancer le test**

```bash
python -m pytest tests/test_timestamp_columns_are_tz_aware.py -v
```

Attendu : 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_timestamp_columns_are_tz_aware.py libs/cmi_common/cmi_common/db/
git commit -m "fix(db): sept colonnes temporelles se declaraient sans fuseau

Les colonnes sont timestamptz en base; les modeles disaient TIMESTAMP.
SQLAlchemy rendait donc le parametre sans fuseau et toute lecture filtrant
sur un datetime aware levait asyncpg.DataError a l'encodage. C'est ce qui
faisait echouer collector-binance-futures a 100% de ses cycles depuis son
deploiement, sans que l'axe positioning ne produise une seule lecture.

Trois des sept viennent de TimestampMixin plutot que d'une declaration
locale, donc Token, Decision et Trade portaient le defaut sans qu'il soit
visible dans models.py.

Le garde-fou balaie toutes les colonnes: rien d'autre dans la suite ne
confronte une declaration a sa colonne, et c'est exactement pourquoi la
divergence a vecu dans deux fichiers a la fois."
```

---

## Task 2 : La convention « naive UTC » disparaît

**Files:**
- Modify: `services/api-gateway/app/persister.py:50-56` et ses 3 appels (135, 180, 225)
- Modify: `services/api-gateway/app/archiver.py:84-89` et ses appels
- Modify: `services/api-gateway/app/read_api.py:128-137` et `976-979`
- Modify: `services/api-gateway/app/systems_pipeline.py:202-207`
- Test: `tests/test_no_naive_utc_convention.py`

**Pourquoi cette tâche est obligatoire et non cosmétique.** Le mensonge de la tâche 1 a engendré une convention : cinq endroits dépouillent le fuseau avant de toucher la base. Une fois les colonnes déclarées `timestamptz`, asyncpg encode un datetime **naïf** en l'interprétant dans le fuseau **local du conteneur**. Laisser ces cinq sites en place remplacerait une erreur bruyante par un décalage silencieux — la forme de défaut exacte que ce plan existe pour supprimer.

Les docstrings sur place montrent que la convention s'est installée en se documentant elle-même, et deux d'entre elles sont fausses :

- `archiver.py:84` : « The columns are TIMESTAMPTZ and the session runs in UTC, so this is the repo-wide convention rather than a property of the column. » — exact, et c'est l'aveu que ça ne tient que par `TimeZone = UTC`.
- `read_api.py:977` : « signals.time is TIMESTAMP WITHOUT TIME ZONE » — **faux**.
- `systems_pipeline.py:203` : « `raw_content` is the one table with tz-aware columns » — **faux**, toutes le sont.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/test_no_naive_utc_convention.py
"""Plus aucun site ne depouille le fuseau avant de toucher la base.

La convention « naive UTC » etait une consequence de quatre declarations ORM
qui affirmaient TIMESTAMP la ou la colonne est timestamptz. Une fois les
declarations corrigees, elle devient nuisible: asyncpg encode un datetime
naif pour une colonne timestamptz en l'interpretant dans le fuseau *local du
conteneur*. La convention troquerait donc une erreur bruyante contre un
decalage silencieux.

Le test est syntaxique plutot que comportemental parce que le comportement
qu'il protege ne se manifeste que contre un vrai Postgres dans un fuseau non
UTC -- conditions qu'aucun test de cette suite ne reunit.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "services/api-gateway/app"
FILES = ["persister.py", "archiver.py", "read_api.py", "systems_pipeline.py"]


def test_no_module_strips_tzinfo_before_a_query() -> None:
    offenders = [
        f"{name}:{n}"
        for name in FILES
        for n, line in enumerate(
            (GATEWAY / name).read_text(encoding="utf-8").splitlines(), 1
        )
        if "replace(tzinfo=None)" in line
    ]
    assert not offenders, (
        f"depouillage du fuseau encore present: {offenders}. Les colonnes sont "
        "timestamptz et les modeles le declarent desormais; passer un datetime "
        "naif fait interpreter la valeur dans le fuseau local du conteneur."
    )


def test_the_helper_itself_is_gone() -> None:
    for name in ("persister.py", "archiver.py"):
        assert "_naive_utc" not in (GATEWAY / name).read_text(encoding="utf-8")
```

- [ ] **Step 2: Lancer le test et vérifier qu'il échoue**

```bash
python -m pytest tests/test_no_naive_utc_convention.py -v
```

Attendu : les deux **ÉCHOUENT**, le premier listant 5 sites.

- [ ] **Step 3: Supprimer `_naive_utc` du persister**

Retirer entièrement la fonction (`persister.py:50-56`) et remplacer les trois appels :

```python
# persister.py:135  (insert(Price))
                    time=e.occurred_at,
```

```python
# persister.py:180  (insert(Signal))
                    time=e.occurred_at,
```

```python
# persister.py:225  (insert(PipelineRejection))
                    time=e.occurred_at,
```

Si `UTC` ne sert plus dans le fichier après cette suppression, ajuster l'import ligne 7 — `make lint` le signalera sinon.

- [ ] **Step 4: Supprimer `_naive_utc` de l'archiver**

Retirer la fonction (`archiver.py:84-89`) et remplacer chacun de ses appels par l'expression qu'elle recevait. Les localiser par :

```bash
grep -n "_naive_utc" services/api-gateway/app/archiver.py
```

- [ ] **Step 5: Rendre `read_api._now_naive` aware**

```python
# read_api.py:128-137 — remplacer le corps et le nom
def _now() -> datetime:
    """Instant courant, avec fuseau.

    Les colonnes filtrees ici sont toutes `timestamptz` et les modeles le
    declarent desormais. Une version anterieure rendait un naif UTC, qui ne
    resolvait au bon instant que parce que la session Postgres tourne en UTC
    -- un reglage que personne n'avait declare.
    """
    return datetime.now(tz=UTC)
```

Renommer tous les appelants :

```bash
grep -rn "_now_naive" services/api-gateway/app/ tests/
```

- [ ] **Step 6: Retirer le dépouillage dans le lookup de signal**

```python
# read_api.py:976-979 — supprimer les trois lignes suivantes
    if at.tzinfo is not None:
        at = at.astimezone(UTC).replace(tzinfo=None)
```

`signals.time` est `timestamptz`, contrairement à ce qu'affirmait le commentaire supprimé.

- [ ] **Step 7: `_cutoffs` ne rend plus qu'un instant**

```python
# systems_pipeline.py:202-207
def _cutoff(window: str) -> datetime:
    """Borne basse de la fenetre, avec fuseau.

    Rendait auparavant un couple (naif, aware) parce que les modeles
    declaraient TIMESTAMP la ou la colonne est timestamptz. Les deux formes
    ont fusionne avec la correction des declarations.
    """
    return datetime.now(tz=UTC) - timedelta(hours=WINDOW_HOURS[window])
```

Adapter les appelants — ils déballaient un couple :

```bash
grep -n "_cutoffs" services/api-gateway/app/systems_pipeline.py
```

- [ ] **Step 8: Lancer le test et la suite complète**

```bash
python -m pytest tests/test_no_naive_utc_convention.py -v
python -m pytest tests/ -q
```

Attendu : le nouveau fichier passe (2 passed) et la suite reste verte. Les tests de `systems_pipeline` et `read_api` qui déballaient un couple ou appelaient `_now_naive` doivent être ajustés — c'est attendu et fait partie de cette tâche.

- [ ] **Step 9: `make lint`**

```bash
make lint
```

Attendu : exit 0. Les imports `UTC` devenus inutiles sont signalés ici.

- [ ] **Step 10: Commit**

```bash
git add services/api-gateway/app/ tests/
git commit -m "fix(api-gateway): retirer la convention naive UTC, devenue nuisible

Cinq sites depouillaient le fuseau avant d'interroger la base, consequence
des declarations corrigees au commit precedent. Une fois les colonnes
declarees timestamptz, asyncpg interprete un datetime naif dans le fuseau
local du conteneur: la convention troquait une erreur bruyante contre un
decalage silencieux.

Deux des docstrings qui la justifiaient etaient factuellement fausses --
signals.time n'est pas TIMESTAMP WITHOUT TIME ZONE, et raw_content n'est pas
la seule table a colonnes tz-aware."
```

---

## Task 3 : Le registre de santé des tâches périodiques

**Files:**
- Modify: `libs/cmi_common/cmi_common/observability/metrics.py`
- Modify: `libs/cmi_common/cmi_common/observability/__init__.py`
- Modify: `libs/cmi_common/cmi_common/runner.py`
- Test: `tests/test_periodic_task_health.py`

`run_periodic` avale toute exception — comportement correct, un tick raté ne doit pas tuer la boucle — mais sans compteur, sans métrique et sans effet sur `/health`. C'est ce qui a rendu 28 heures de panne intégrale indiscernables d'un service sain.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/test_periodic_task_health.py
"""run_periodic tient un registre d'echecs consecutifs.

Sans lui, une tache qui rate 100% de ses cycles est indiscernable d'une tache
saine: l'exception est journalisee puis avalee, et /health repond 200. C'est
la mecanique qui a laisse collector-binance-futures se declarer healthy
pendant 28 heures sans produire une seule lecture.
"""

from __future__ import annotations

import asyncio

import pytest

from cmi_common import runner


@pytest.fixture(autouse=True)
def _clean_registry():
    runner.TASK_HEALTH.clear()
    yield
    runner.TASK_HEALTH.clear()


async def _run_ticks(factory, *, name: str, ticks: int) -> None:
    """Laisse run_periodic executer `ticks` fois, puis l'annule."""
    calls = 0
    done = asyncio.Event()

    async def counted() -> None:
        nonlocal calls
        calls += 1
        try:
            await factory()
        finally:
            if calls >= ticks:
                done.set()

    task = asyncio.create_task(runner.run_periodic(counted, 0.001, name=name))
    await asyncio.wait_for(done.wait(), timeout=2.0)
    # Un dernier passage de boucle pour que l'issue du tick soit enregistree
    # avant l'annulation: done.set() se declenche dans le corps du tick.
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_failing_task_accumulates_consecutive_failures() -> None:
    async def boom() -> None:
        raise RuntimeError("upstream down")

    await _run_ticks(boom, name="boom-poll", ticks=3)

    state = runner.TASK_HEALTH["boom-poll"]
    assert state.consecutive_failures >= 3
    assert "upstream down" in state.last_error
    assert state.last_success is None


async def test_a_success_resets_the_counter() -> None:
    outcomes = [RuntimeError("x"), RuntimeError("x")]

    async def flaky() -> None:
        if outcomes:
            raise outcomes.pop(0)

    await _run_ticks(flaky, name="flaky-poll", ticks=3)

    state = runner.TASK_HEALTH["flaky-poll"]
    assert state.consecutive_failures == 0
    assert state.last_success is not None


def test_failing_tasks_reports_only_those_past_the_threshold() -> None:
    """Teste _record directement plutot que la boucle: avec un intervalle de
    1 ms, le nombre exact de ticks executes avant l'annulation n'est pas
    controlable, et un test de seuil qui ne controle pas son compte ne teste
    pas un seuil."""
    for _ in range(runner.UNHEALTHY_AFTER - 1):
        runner._record("two-fails", error=RuntimeError("nope"))
    assert runner.failing_tasks() == {}

    runner._record("two-fails", error=RuntimeError("nope"))
    assert "two-fails" in runner.failing_tasks()


def test_one_success_clears_a_task_past_the_threshold() -> None:
    for _ in range(runner.UNHEALTHY_AFTER + 2):
        runner._record("recovering", error=RuntimeError("nope"))
    assert "recovering" in runner.failing_tasks()

    runner._record("recovering", error=None)
    assert runner.failing_tasks() == {}


def test_the_threshold_is_three() -> None:
    """Sur un cycle de 5 min, trois echecs valent 15 minutes de panne avant
    l'alerte: un rate-limit transitoire ne fait pas clignoter, et on ne
    reproduit pas les 28 heures."""
    assert runner.UNHEALTHY_AFTER == 3
```

- [ ] **Step 2: Lancer le test et vérifier qu'il échoue**

```bash
python -m pytest tests/test_periodic_task_health.py -v
```

Attendu : **ÉCHEC** — `AttributeError: module 'cmi_common.runner' has no attribute 'TASK_HEALTH'`.

- [ ] **Step 3: Ajouter la métrique partagée**

```python
# libs/cmi_common/cmi_common/observability/metrics.py — a la suite des autres
#: Ticks de tache periodique, par issue. Le nom est une constante parce qu'une
#: seconde copie de ces chaines est ce qui avait casse le graphe du Command
#: Center: le collecteur scrutait `events_consumed_total` quand le compteur
#: s'appelle `cmi_events_consumed_total`, et servait « 0 » pour « non mesure ».
PERIODIC_TICKS_METRIC = "cmi_periodic_ticks_total"

PERIODIC_TICKS = Counter(
    PERIODIC_TICKS_METRIC,
    "Periodic task ticks by outcome",
    ["service", "task", "status"],
)
```

```python
# libs/cmi_common/cmi_common/observability/__init__.py
from .metrics import (
    AI_TOKENS,
    CONTENT_DROPPED,
    EVENT_PROCESSING_SECONDS,
    EVENTS_CONSUMED,
    EVENTS_CONSUMED_METRIC,
    EVENTS_PRODUCED,
    EVENTS_PRODUCED_METRIC,
    INFLIGHT,
    LEXICON_COINS,
    PERIODIC_TICKS,
    PERIODIC_TICKS_METRIC,
    UNMEASURED,
    UPSTREAM_REQUESTS,
)
```

et ajouter `"PERIODIC_TICKS"` et `"PERIODIC_TICKS_METRIC"` à `__all__`, dans l'ordre alphabétique existant.

- [ ] **Step 4: Écrire le registre dans `runner.py`**

```python
# libs/cmi_common/cmi_common/runner.py — remplace integralement le fichier
"""Background task helpers for services: periodic pollers and consumer loops."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .config import get_settings
from .observability import PERIODIC_TICKS

logger = logging.getLogger(__name__)

#: Echecs consecutifs au-dela desquels une tache est declaree en panne.
#: Sur un cycle de 5 min, trois echecs valent 15 minutes de panne integrale
#: avant l'alerte: un rate-limit transitoire ne fait pas clignoter, et on ne
#: reproduit pas les 28 heures pendant lesquelles collector-binance-futures a
#: rate 100% de ses cycles en se declarant healthy.
UNHEALTHY_AFTER = 3


@dataclass(slots=True)
class TaskState:
    #: Nom de la tache, repris ici pour que failing_tasks() reste lisible dans
    #: une reponse JSON sans que l'appelant ait a rezipper les cles.
    name: str = ""
    consecutive_failures: int = 0
    last_success: float | None = None
    last_error: str | None = None


#: Etat par nom de tache. Global au processus, comme les compteurs Prometheus:
#: un service execute ses taches dans un seul event loop.
TASK_HEALTH: dict[str, TaskState] = {}


def failing_tasks() -> dict[str, TaskState]:
    """Taches ayant depasse le seuil d'echecs consecutifs."""
    return {
        name: state
        for name, state in TASK_HEALTH.items()
        if state.consecutive_failures >= UNHEALTHY_AFTER
    }


def _record(name: str, *, error: BaseException | None) -> None:
    state = TASK_HEALTH.setdefault(name, TaskState(name=name))
    service = get_settings().service_name
    if error is None:
        state.consecutive_failures = 0
        state.last_success = time.time()
        PERIODIC_TICKS.labels(service, name, "ok").inc()
        return
    state.consecutive_failures += 1
    state.last_error = f"{type(error).__name__}: {error}"
    PERIODIC_TICKS.labels(service, name, "error").inc()


async def run_periodic(
    coro_factory: Callable[[], Awaitable[None]],
    interval_seconds: float,
    *,
    name: str = "task",
) -> None:
    """Run ``coro_factory`` every ``interval_seconds`` until cancelled.

    Exceptions in a single tick are logged and swallowed so one bad poll never
    kills the loop -- but they are *counted*. Swallowing without counting is
    what made a collector failing every cycle indistinguishable from a healthy
    one: the traceback went to the log, `/health` kept answering 200, and the
    axis it feeds stayed empty for 28 hours without a single alert.
    """
    logger.info("starting periodic task '%s' every %ss", name, interval_seconds)
    TASK_HEALTH.setdefault(name, TaskState(name=name))
    try:
        while True:
            started = asyncio.get_event_loop().time()
            try:
                await coro_factory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("periodic task '%s' tick failed", name)
                _record(name, error=exc)
            else:
                _record(name, error=None)
            elapsed = asyncio.get_event_loop().time() - started
            await asyncio.sleep(max(0.0, interval_seconds - elapsed))
    except asyncio.CancelledError:
        logger.info("periodic task '%s' cancelled", name)
        raise
```

- [ ] **Step 5: Lancer le test**

```bash
python -m pytest tests/test_periodic_task_health.py -v
```

Attendu : 5 passed.

- [ ] **Step 6: Vérifier l'absence de cycle d'import**

`runner.py` importe désormais `config` et `observability`. Vérifier qu'aucun des deux n'importe `runner` :

```bash
python -c "import cmi_common.runner; print('ok')"
python -m pytest tests/ -q
```

Attendu : `ok`, puis la suite verte.

- [ ] **Step 7: Commit**

```bash
git add libs/cmi_common/cmi_common/runner.py libs/cmi_common/cmi_common/observability/ tests/test_periodic_task_health.py
git commit -m "feat(runner): compter les ticks periodiques au lieu de seulement les avaler

run_periodic avalait toute exception sans compteur ni metrique. Une tache
ratant 100% de ses cycles etait donc indiscernable d'une tache saine, ce qui
a laisse collector-binance-futures se declarer healthy pendant 28 heures.

Le seuil de trois echecs consecutifs vaut 15 minutes de panne integrale sur
un cycle de 5 min: assez pour qu'un rate-limit transitoire ne clignote pas."
```

---

## Task 4 : `/health` répond 503 quand une tâche est en panne

**Files:**
- Modify: `libs/cmi_common/cmi_common/app.py:52-59`
- Test: `tests/test_health_endpoint_degrades.py`

Le `HEALTHCHECK` des trois Dockerfile (`docker/Dockerfile`, `.ai`, `.ml`) utilise `curl -fsS`, qui échoue sur tout code ≥ 400. Un 503 bascule donc le conteneur en `unhealthy`. Et comme `restart: unless-stopped` ne redémarre pas sur ce motif, et qu'aucun `depends_on: service_healthy` ne porte sur les services applicatifs, la panne devient visible sans boucle de redémarrage ni déploiement bloqué.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/test_health_endpoint_degrades.py
"""Un service dont une tache periodique est en panne cesse de se dire sain.

Le HEALTHCHECK des Dockerfile utilise `curl -fsS`, qui echoue sur tout code
>= 400: un 503 bascule le conteneur en `unhealthy` sans le redemarrer, puisque
`restart: unless-stopped` ne redemarre pas sur ce motif.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cmi_common import create_app, runner


@pytest.fixture(autouse=True)
def _clean_registry():
    runner.TASK_HEALTH.clear()
    yield
    runner.TASK_HEALTH.clear()


def test_health_is_ok_with_no_periodic_task() -> None:
    with TestClient(create_app("test-svc")) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_is_ok_below_the_threshold() -> None:
    runner.TASK_HEALTH["poll"] = runner.TaskState(
        name="poll", consecutive_failures=runner.UNHEALTHY_AFTER - 1
    )
    with TestClient(create_app("test-svc")) as client:
        response = client.get("/health")
    assert response.status_code == 200


def test_health_degrades_once_a_task_passes_the_threshold() -> None:
    runner.TASK_HEALTH["binance-futures-poll"] = runner.TaskState(
        name="binance-futures-poll",
        consecutive_failures=runner.UNHEALTHY_AFTER,
        last_error="DataError: invalid input for query argument $1",
    )
    with TestClient(create_app("test-svc")) as client:
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    failing = body["failing_tasks"]["binance-futures-poll"]
    assert failing["consecutive_failures"] == runner.UNHEALTHY_AFTER
    assert "DataError" in failing["last_error"]


def test_the_failing_task_name_reaches_the_body() -> None:
    """Un 503 sans le nom de la tache oblige a ouvrir les logs pour savoir
    laquelle -- ce qui est precisement l'etape que ce changement supprime."""
    runner.TASK_HEALTH["a"] = runner.TaskState(name="a", consecutive_failures=9)
    runner.TASK_HEALTH["b"] = runner.TaskState(name="b", consecutive_failures=0)
    with TestClient(create_app("test-svc")) as client:
        body = client.get("/health").json()
    assert set(body["failing_tasks"]) == {"a"}
```

- [ ] **Step 2: Lancer le test et vérifier qu'il échoue**

```bash
python -m pytest tests/test_health_endpoint_degrades.py -v
```

Attendu : les deux premiers PASSENT, les deux derniers **ÉCHOUENT** (`assert 200 == 503`).

- [ ] **Step 3: Faire consulter le registre par `/health`**

```python
# libs/cmi_common/cmi_common/app.py — remplace le handler existant (l.52-59)
    @app.get("/health", tags=["ops"])
    async def health(response: Response) -> dict[str, object]:
        """Liveness + readiness probe.

        Repond 503 des qu'une tache periodique a echoue `UNHEALTHY_AFTER` fois
        de suite. Sans cela un collector ratant tous ses cycles reste `healthy`
        pour Docker, ce qui s'est produit pendant 28 heures.
        """
        failing = failing_tasks()
        body: dict[str, object] = {
            "service": service_name,
            "status": "degraded" if failing else "ok",
            "ready": bool(getattr(app.state, "ready", False)),
        }
        if failing:
            response.status_code = 503
            body["failing_tasks"] = {
                name: {
                    "consecutive_failures": state.consecutive_failures,
                    "last_error": state.last_error,
                    "last_success": state.last_success,
                }
                for name, state in failing.items()
            }
        return body
```

Ajouter l'import en tête de `app.py`, après `from .observability.tracing import setup_tracing` :

```python
from .runner import failing_tasks
```

**Attention au cycle :** `runner.py` importe `config` et `observability`, jamais `app`. L'import est donc sûr — mais le vérifier à l'étape suivante plutôt que le supposer.

- [ ] **Step 4: Lancer le test et vérifier l'absence de cycle**

```bash
python -c "import cmi_common; print('ok')"
python -m pytest tests/test_health_endpoint_degrades.py -v
```

Attendu : `ok`, puis 4 passed.

- [ ] **Step 5: Lancer la suite complète**

```bash
python -m pytest tests/ -q
```

Attendu : verte. Tout test existant qui affirme `status_code == 200` sur `/health` reste vrai — le registre est vide hors des tests de la tâche 3.

- [ ] **Step 6: Commit**

```bash
git add libs/cmi_common/cmi_common/app.py tests/test_health_endpoint_degrades.py
git commit -m "feat(health): un service dont une tache periodique est morte cesse de se dire sain

curl -fsS echoue sur tout code >= 400, donc un 503 bascule le conteneur en
unhealthy. restart: unless-stopped ne redemarre pas sur ce motif et aucun
depends_on: service_healthy ne porte sur les services applicatifs: la panne
devient visible sans boucle de redemarrage ni deploiement bloque.

Le nom de la tache et sa derniere erreur voyagent dans le corps, sans quoi un
503 obligerait a ouvrir les logs pour savoir laquelle -- l'etape que ce
changement supprime."
```

---

# Partie B — fidélité du rejeu, puis calibration

## Task 5 : La lecture de régime prend sa propre clé

**Files:**
- Modify: `services/ai-worker-haiku/app/features.py`
- Test: `tests/test_market_regime_store.py`

`features:MARKET` existe en production et est vivante, mais n'est jamais lue : `_ready()` refuse de scorer un symbole sans prix, ce qui est correct pour MARKET. La lecture de régime a besoin de sa propre clé **et de son propre TTL**.

**Le TTL est le point délicat, et pas pour la raison qu'on croit.** Mesuré sur 14 jours de
production, 399 mises à jour de l'agrégat MARKET : écart médian **1 120 s** (~19 min), p95
**4 241 s** (~71 min), maximum **144 845 s** (40,2 h), et **38 écarts sur 399 dépassent une
heure** (9,5 %).

Donc 3600 s ne « couvre » pas la cadence — il est franchi une fois sur dix. Ce n'est pas le
critère. Le critère est que le decision-engine applique **déjà** `market_ttl_seconds=3600` à
l'état qu'il tient en mémoire : sa lecture de régime est donc déjà absente pendant ces mêmes
trous, aujourd'hui, en production. Reprendre 3600 s **reproduit ce comportement à l'identique,
trous compris**, ce qu'exige la fidélité du rejeu.

Deux erreurs symétriques à éviter. Hériter du `FEATURE_TTL` de 900 s **raccourcirait** la
fenêtre d'un facteur quatre et ferait disparaître `news_score` pour des lignes qui le gardent
aujourd'hui. Allonger le TTL pour combler les trous mesurés ci-dessus **modifierait le scoring**
et fausserait la distribution qu'on s'apprête justement à calibrer. Dans les deux cas on
calibrerait sur un modèle que la production n'exécute pas.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/test_market_regime_store.py
"""La lecture de regime a sa propre cle et son propre TTL.

Le decision-engine la tenait en memoire avec un TTL de 3600 s. La deplacer
dans le FeatureStore telle quelle l'aurait ramenee a 900 s -- plus court que
la cadence mesuree d'alimentation, 10 a 30 minutes -- ce qui aurait fait
disparaitre l'axe news_score pour des lignes qui le gardent aujourd'hui.
"""

from __future__ import annotations

from typing import Any

from service_modules import load_service_module

features_mod = load_service_module("ai-worker-haiku", "features")
MarketRegimeStore = features_mod.MarketRegimeStore


class FakeCache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}

    async def get_json(self, key: str) -> Any | None:
        return self.values.get(key)

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        self.values[key] = value
        self.ttls[key] = ttl_seconds


async def test_absent_regime_reads_as_none() -> None:
    assert await MarketRegimeStore(FakeCache()).get() is None


async def test_a_stored_regime_reads_back() -> None:
    store = MarketRegimeStore(FakeCache())
    await store.set(-0.42)
    assert await store.get() == -0.42


async def test_a_measured_zero_is_not_an_absence() -> None:
    """0.0 est un regime neutre mesure. Le confondre avec l'absence ferait
    disparaitre l'axe news_score pour les symboles sans sentiment propre --
    la conflation None/0 que ce projet paie a chaque etage."""
    store = MarketRegimeStore(FakeCache())
    await store.set(0.0)
    assert await store.get() == 0.0


async def test_the_ttl_matches_the_engine_window_it_replaces() -> None:
    cache = FakeCache()
    await MarketRegimeStore(cache).set(0.1)
    assert cache.ttls[features_mod.REGIME_KEY] == 3600
    assert features_mod.REGIME_TTL == 3600


async def test_the_regime_key_is_not_a_symbol_feature_key() -> None:
    """Sinon elle heriterait du TTL de 900 s du FeatureStore, plus court que
    la cadence d'alimentation mesuree."""
    assert features_mod.REGIME_KEY != features_mod.KEY.format(symbol="MARKET")
```

- [ ] **Step 2: Lancer le test et vérifier qu'il échoue**

```bash
python -m pytest tests/test_market_regime_store.py -v
```

Attendu : **ÉCHEC** à l'import — `AttributeError: module has no attribute 'MarketRegimeStore'`.

- [ ] **Step 3: Implémenter**

```python
# services/ai-worker-haiku/app/features.py — a la suite de FeatureStore
#: La lecture de regime a un TTL propre, plus long que celui des features par
#: symbole. Il reprend le `market_ttl_seconds=3600` que le decision-engine
#: appliquait a l'etat qu'il tenait en memoire. Reutiliser FEATURE_TTL (900 s)
#: l'aurait raccourci sous la cadence d'alimentation mesuree en production --
#: une mise a jour toutes les 10 a 30 minutes -- rendant la cle absente une
#: bonne partie du temps et faisant disparaitre l'axe news_score pour les
#: symboles sans sentiment propre, soit 34% des lignes.
REGIME_TTL = 3600
REGIME_KEY = "market:regime"


class MarketRegimeStore:
    """Lecture de sentiment a l'echelle du marche, hors de tout symbole.

    Le contenu crypto qui ne nomme aucune piece -- regulation, macro,
    incidents d'exchange -- porte le symbole MARKET. Il informe le score de
    tous les symboles sans sentiment propre, mais jamais leur confiance: il
    est identique pour tout le livre.
    """

    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    async def set(self, sentiment_score: float) -> None:
        await self._cache.set_json(
            REGIME_KEY, {"sentiment_score": sentiment_score}, ttl_seconds=REGIME_TTL
        )

    async def get(self) -> float | None:
        stored = await self._cache.get_json(REGIME_KEY)
        if not stored:
            return None
        value = stored.get("sentiment_score")
        return None if value is None else float(value)
```

- [ ] **Step 4: Lancer le test**

```bash
python -m pytest tests/test_market_regime_store.py -v
```

Attendu : 5 passed.

- [ ] **Step 5: Commit**

```bash
git add services/ai-worker-haiku/app/features.py tests/test_market_regime_store.py
git commit -m "feat(haiku): la lecture de regime prend sa propre cle et son propre TTL

3600 s, comme le market_ttl_seconds que le decision-engine appliquait a
l'etat qu'il tenait en memoire. Le TTL du FeatureStore (900 s) est plus
court que la cadence d'alimentation mesuree en production, une mise a jour
toutes les 10 a 30 minutes: la cle aurait ete absente une bonne partie du
temps et l'axe news_score aurait disparu pour 34% des lignes."
```

---

## Task 6 : haiku estampille `market_sentiment` dans les features publiées

**Files:**
- Modify: `services/ai-worker-haiku/app/worker.py:60-113`
- Modify: `services/ai-worker-haiku/app/main.py:31-40`
- Test: `tests/test_haiku_market_sentiment.py`

Une fois la valeur dans `meta["features"]`, elle atterrit dans `decision_journal.features` **sans migration** — la colonne est du JSONB — et le rejeu devient exact.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/test_haiku_market_sentiment.py
"""La lecture de regime voyage avec les features publiees.

Le decision-engine la tenait en memoire, donc rien ne l'ecrivait et rien ne
la rejouait. Elle n'est pas decorative: pour les lignes sans sentiment propre
-- 34,0% mesure sur 276 966 lignes de 24 h -- elle decide si l'axe news_score
(13,8% du poids) est present ou exclu. Elle deplace donc le score *et* le
poids present, ligne par ligne.
"""

from __future__ import annotations

from typing import Any

from service_modules import load_service_module

from cmi_common.events import SentimentEvent
from cmi_common.events.base import Source
from cmi_common.kafka import Topic

features_mod = load_service_module("ai-worker-haiku", "features")
worker_mod = load_service_module("ai-worker-haiku", "worker")


class FakeCache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_json(self, key: str) -> Any | None:
        return self.values.get(key)

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        self.values[key] = value


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Any]] = []

    async def publish(self, topic: Topic, event: Any) -> None:
        self.published.append((topic, event))


class Clock:
    """Horloge pilotee. Le worker compare `now - last` a SETTLE_S; laisser
    `handle` estampiller avec time.monotonic puis forcer une valeur avant le
    flush rendrait le test dependant de l'uptime de la machine."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _build(cache: FakeCache, clock: Clock | None = None):
    producer = FakeProducer()
    worker = worker_mod.HaikuWorker(
        features_mod.FeatureStore(cache),
        producer,
        regime=features_mod.MarketRegimeStore(cache),
        clock=clock or Clock(),
    )
    return worker, producer


def _sentiment(symbol: str, value: float) -> SentimentEvent:
    return SentimentEvent(
        source=Source.SENTIMENT_SERVICE,
        symbol=symbol,
        sentiment_score=value,
        confidence=0.9,
        input_kind="news",
    )


async def _analysis_features(cache: FakeCache) -> dict[str, Any]:
    clock = Clock()
    worker, producer = _build(cache, clock)
    await worker._store.update("BTC", {"price_change_pct_24h": 5.0})
    await worker.handle(_sentiment("BTC", 0.3))
    clock.t = 1_000.0  # la fenetre du symbole est retombee au calme
    await worker.flush_settled()
    return producer.published[0][1].meta["features"]


async def test_a_market_sentiment_event_lands_in_the_regime_store() -> None:
    cache = FakeCache()
    worker, _ = _build(cache)
    await worker.handle(_sentiment("MARKET", -0.4))
    assert await features_mod.MarketRegimeStore(cache).get() == -0.4


async def test_market_never_becomes_a_pending_symbol() -> None:
    """_ready() refuse de scorer un symbole sans prix, donc MARKET n'est jamais
    analyse. L'inscrire au registre des symboles en attente ne ferait que le
    balayer a chaque passage du sweeper."""
    cache = FakeCache()
    worker, _ = _build(cache)
    await worker.handle(_sentiment("MARKET", -0.4))
    assert worker.pending_symbols() == 0


async def test_the_regime_is_stamped_into_the_published_features() -> None:
    cache = FakeCache()
    await features_mod.MarketRegimeStore(cache).set(-0.4)
    assert (await _analysis_features(cache))["market_sentiment"] == -0.4


async def test_an_absent_regime_leaves_the_key_out() -> None:
    """Une cle absente et une cle a None ne doivent pas se confondre en aval:
    features_from lit `raw.get(...)`, et un 0.0 fabrique ferait passer l'axe
    news_score de exclu a present."""
    assert "market_sentiment" not in await _analysis_features(FakeCache())


async def test_a_measured_neutral_regime_is_stamped() -> None:
    cache = FakeCache()
    await features_mod.MarketRegimeStore(cache).set(0.0)
    assert (await _analysis_features(cache))["market_sentiment"] == 0.0
```

- [ ] **Step 2: Lancer le test et vérifier qu'il échoue**

```bash
python -m pytest tests/test_haiku_market_sentiment.py -v
```

Attendu : **ÉCHEC** — `HaikuWorker.__init__() got an unexpected keyword argument 'regime'`.

- [ ] **Step 3: Câbler le store dans le worker**

```python
# services/ai-worker-haiku/app/worker.py — signature (l.60-75)
    def __init__(
        self,
        store: FeatureStore,
        producer: EventProducer,
        *,
        regime: MarketRegimeStore | None = None,
        scorer_config: ScorerConfig | None = None,
        clock=None,
    ) -> None:
        self._store = store
        self._producer = producer
        self._regime = regime
        self._cfg = scorer_config or ScorerConfig()
```

Le reste du corps du constructeur est inchangé. Ajouter l'import :

```python
from .features import FeatureStore, MarketRegimeStore
```

et, depuis la couche partagée :

```python
from cmi_common.sources import MARKET_SYMBOL
```

- [ ] **Step 4: Router MARKET vers le régime plutôt que vers le registre des symboles**

```python
# services/ai-worker-haiku/app/worker.py — handle() (l.77-88)
    async def handle(self, event: BaseEvent) -> None:
        symbol, fields, topic = self._extract(event)
        if symbol is None:
            return
        EVENTS_CONSUMED.labels(SERVICE, topic, event.event_type).inc()
        if symbol == MARKET_SYMBOL:
            # Lecture a l'echelle du marche: elle informe le score de tous les
            # symboles sans sentiment propre, mais n'est le sujet d'aucun. La
            # faire entrer dans le registre des symboles en attente ne ferait
            # que la balayer a chaque passage, puisque _ready() refuse de
            # scorer un symbole sans prix.
            value = fields.get("sentiment_score")
            if self._regime is not None and value is not None:
                await self._regime.set(float(value))
            return
        # Unconditional: the event is folded into the symbol's state whether or
        # not it triggers an inference. Nothing arriving here is discarded.
        await self._store.update(symbol, fields)
        now = self._clock()
        _last, first, _corr = self._pending.get(symbol, (now, now, ""))
        self._pending[symbol] = (now, first, event.correlation_id)
```

- [ ] **Step 5: Estampiller au flush**

```python
# services/ai-worker-haiku/app/worker.py — flush_settled() (l.89-113)
    async def flush_settled(self) -> None:
        """Emit one analysis per symbol whose window has gone quiet.

        Read from the store rather than from what `handle` passed in: that is
        what makes this an aggregation of the window instead of a replay of the
        last event to arrive.
        """
        now = self._clock()
        due = [
            (symbol, corr)
            for symbol, (last, first, corr) in self._pending.items()
            if now - last >= SETTLE_S or now - first >= MAX_DELAY_S
        ]
        # Une lecture par balayage, pas par symbole: elle est identique pour
        # tout le livre.
        regime = await self._regime.get() if self._regime is not None else None
        for symbol, correlation_id in due:
            del self._pending[symbol]
            features = await self._store.get(symbol)
            # Only score once we have at least a price/dex anchor plus one signal.
            if not self._ready(features):
                continue
            if regime is not None:
                # Absente, la cle reste absente: `None` et un 0.0 mesure ne
                # disent pas la meme chose a _norm_news, qui exclut l'axe dans
                # un cas et le score dans l'autre.
                features = {**features, "market_sentiment": regime}
            analysis = self._score(symbol, features, correlation_id)
            await self._producer.publish(Topic.ANALYSIS, analysis)
            EVENTS_PRODUCED.labels(
                SERVICE, Topic.ANALYSIS.value, analysis.event_type
            ).inc()
```

- [ ] **Step 6: Câbler dans `main.py`**

```python
# services/ai-worker-haiku/app/main.py:31-40
async def _startup(app: FastAPI, settings: Settings) -> None:
    cache = Cache(settings.redis)
    producer = EventProducer(settings.kafka)
    await producer.start()
    # Haiku triage is now a deterministic local scorer — no Claude, no quota.
    worker = HaikuWorker(
        FeatureStore(cache),
        producer,
        regime=MarketRegimeStore(cache),
        scorer_config=scorer_config_from_env(),
    )
```

et l'import :

```python
from .features import FeatureStore, MarketRegimeStore
```

- [ ] **Step 7: Lancer les tests**

```bash
python -m pytest tests/test_haiku_market_sentiment.py tests/test_analysis_settling.py -v
python -m pytest tests/ -q
```

Attendu : le nouveau fichier 5 passed, `test_analysis_settling.py` toujours vert (il construit `HaikuWorker` sans `regime=`, que le défaut `None` couvre), suite verte.

- [ ] **Step 8: Commit**

```bash
git add services/ai-worker-haiku/ tests/test_haiku_market_sentiment.py
git commit -m "feat(haiku): la lecture de regime voyage avec les features publiees

Elle atterrit dans decision_journal.features sans migration -- la colonne est
du JSONB -- ce qui rend le rejeu du journal exact plutot qu'approxime.

MARKET cesse au passage d'entrer dans le registre des symboles en attente:
_ready() refuse de scorer un symbole sans prix, donc il n'y etait balaye que
pour rien. Une cle absente reste absente, parce que None et un 0.0 mesure ne
disent pas la meme chose a _norm_news."
```

---

## Task 7 : `features_from` extrait, avec instant de référence explicite

**Files:**
- Create: `services/decision-engine/app/features_map.py`
- Test: `tests/test_features_from_replay.py`

Le mapping `raw → Features` vit aujourd'hui dans `engine.py:138-160`, entremêlé au consommateur Kafka. Le script de calibration doit exécuter **exactement** ce mapping, sans importer aiokafka. Il devient donc une fonction pure dans son propre module. Le branchement du moteur dessus est la tâche 8.

**Deux points de rejeu.**

`_unlock_days` calcule les jours restants contre `datetime.now()`. Rejoué une semaine plus tard, chaque `next_unlock_at` est dans le passé, la fonction rend `None` et le terme disparaît. Elle prend donc un instant de référence : `now()` en production, `row.time` en rejeu.

Le moteur lisait quatre champs sur l'événement (`price_change_pct_24h`, `volume_spike_ratio`, `sentiment_score`, `social_growth`) et le reste dans `meta["features"]`. Or `worker.py:283-286` remplit ces quatre champs **depuis ce même dict**. Ils sont donc identiques par construction, et `features_from` ne lit plus que `raw` — une seule source, donc un rejeu qui ne peut pas diverger. Le test verrouille l'invariant chez le producteur.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/test_features_from_replay.py
"""Le mapping raw -> Features est pur, partage, et rejouable a une date donnee.

Le script de calibration doit executer exactement le mapping que la production
execute. Une seconde copie mesurerait un modele que personne ne fait tourner.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

from service_modules import load_service_module

fm = load_service_module("decision-engine", "features_map")
features_from = fm.features_from

ROOT = Path(__file__).resolve().parents[1]
HAIKU_WORKER = ROOT / "services/ai-worker-haiku/app/worker.py"
NOW = datetime(2026, 8, 1, tzinfo=UTC)


def test_liquidity_falls_back_to_24h_volume() -> None:
    """liquidity_usd n'est ecrit que pour les DexEvent; une paire listee en CEX
    n'en produit jamais. Sans le repli, 10% du poids du modele reste mort."""
    assert features_from({"volume_24h_usd": 5_000_000}, now=NOW).liquidity_usd == 5e6
    assert (
        features_from(
            {"liquidity_usd": 12_000, "volume_24h_usd": 5_000_000}, now=NOW
        ).liquidity_usd
        == 12_000.0
    )


def test_unlock_days_are_measured_from_the_reference_instant() -> None:
    """Rejoue avec `now()`, un deverrouillage du 4 aout lu depuis une ligne du
    1er aout serait dans le passe et le terme disparaitrait -- alors qu'il
    valait 3 jours au moment de la decision."""
    raw = {"next_unlock_at": "2026-08-04T00:00:00+00:00", "has_unlock_schedule": True}
    assert features_from(raw, now=NOW).next_unlock_days == 3.0


def test_a_past_unlock_is_stale_not_imminent() -> None:
    """Une date passee est une lecture perimee, pas une urgence. La ramener a
    zero jour ferait lire l'axe a son *pire* la ou la verite est son meilleur."""
    later = datetime(2026, 8, 10, tzinfo=UTC)
    raw = {"next_unlock_at": "2026-08-04T00:00:00+00:00"}
    assert features_from(raw, now=later).next_unlock_days is None


def test_an_unparseable_unlock_date_does_not_raise() -> None:
    raw = {"next_unlock_at": "pas une date"}
    assert features_from(raw, now=NOW).next_unlock_days is None


def test_a_naive_unlock_date_is_read_as_utc() -> None:
    raw = {"next_unlock_at": "2026-08-03T00:00:00"}
    assert features_from(raw, now=NOW).next_unlock_days == 2.0


def test_market_sentiment_comes_from_the_row() -> None:
    assert features_from({"market_sentiment": -0.4}, now=NOW).market_sentiment == -0.4
    assert features_from({}, now=NOW).market_sentiment is None


def test_absent_flags_are_false_not_none() -> None:
    f = features_from({}, now=NOW)
    assert f.has_unlock_schedule is False
    assert f.all_repos_archived is False


def test_haiku_fills_the_event_fields_from_the_same_dict() -> None:
    """features_from ne lit plus que meta["features"]. L'invariant qui le rend
    exact est chez le producteur: les quatre champs de tete de l'AnalysisEvent
    sont remplis depuis ce meme dict. Verrouille par analyse syntaxique, comme
    test_axis_parity.py, plutot que par import: worker.py tire aiokafka."""
    tree = ast.parse(HAIKU_WORKER.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AnalysisEvent"
    ]
    assert len(calls) == 1, "un seul site construit l'AnalysisEvent"
    sources = {
        kw.arg: ast.unparse(kw.value)
        for kw in calls[0].keywords
        if kw.arg
        in {
            "price_change_pct_24h",
            "volume_spike_ratio",
            "sentiment_score",
            "social_growth",
        }
    }
    assert sources == {
        "price_change_pct_24h": "features.get('price_change_pct_24h')",
        "volume_spike_ratio": "features.get('volume_spike_ratio')",
        "sentiment_score": "features.get('sentiment_score')",
        "social_growth": "features.get('social_growth')",
    }, (
        "un champ de tete de l'AnalysisEvent ne vient plus du dict features; "
        "features_from, qui ne lit que ce dict, cesserait de rejouer la production"
    )
```

**Note sur le dernier test :** `ast.unparse` normalise les guillemets en apostrophes simples. Si l'assertion échoue sur la seule forme des guillemets, corriger les valeurs attendues plutôt que le code source.

- [ ] **Step 2: Lancer le test et vérifier qu'il échoue**

```bash
python -m pytest tests/test_features_from_replay.py -v
```

Attendu : **ÉCHEC** à l'import — `features_map` n'existe pas.

- [ ] **Step 3: Créer `features_map.py`**

```python
# services/decision-engine/app/features_map.py
"""Le dict de features stocke -> l'entree du scoreur. Pur, sans I/O.

Extrait de `engine.py` pour que le rejeu hors-ligne execute *exactement* le
mapping que la production execute. Une seconde copie mesurerait un modele que
personne ne fait tourner, ce qui est precisement le risque qu'un script de
calibration doit ecarter.

Le module ne lit que `meta["features"]`. Le moteur lisait auparavant quatre
champs sur l'evenement lui-meme, mais `ai-worker-haiku` les remplit depuis ce
meme dict (`worker.py:283-286`): ils sont identiques par construction, et une
source unique est une source qui ne peut pas diverger au rejeu. L'invariant
est verrouille chez le producteur par
`tests/test_features_from_replay.py::test_haiku_fills_the_event_fields_from_the_same_dict`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from .scoring import Features

logger = logging.getLogger(__name__)


def _liquidity(raw: dict) -> float | None:
    """DEX liquidity when there is a reading, 24h volume as the stand-in when not.

    Reading ``liquidity_usd`` alone left ``liquidity_score`` at zero for
    essentially the entire flow: ai-worker-haiku only writes that key for
    DexEvents, and CEX-listed pairs never produce one. Measured over the 12,183
    highest-scoring production signals, it was populated in exactly none of
    them -- a tenth of the model weight permanently dead.

    The substitution is not invented here: haiku's own scorer has used 24h
    volume as the liquidity stand-in since Plan-1, normalising it identically,
    and records which of the two it used in ``liquidity_source`` so calibration
    can still tell an estimate from a measurement.
    """
    liq = raw.get("liquidity_usd")
    if liq:
        return float(liq)
    proxy = raw.get("volume_24h_usd")
    return float(proxy) if proxy else None


def _unlock_days(raw: dict, *, now: datetime) -> float | None:
    """Days until the next unlock, measured from ``now``.

    Stored absolute and converted at read time: a stored "days remaining" would
    silently age between the collector's poll and the decision that reads it.

    ``now`` is a parameter rather than ``datetime.now()`` because this function
    runs twice over the same row -- once live, once in replay, a week later.
    With a wall clock every stored date would be in the past on replay, the
    term would vanish, and the recomputed score would silently differ from the
    one production emitted.
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
    days = (at - now).total_seconds() / 86400.0
    if days < 0:
        # A past date is a *stale* reading, not an imminent unlock, and the
        # difference is the whole axis. When an unlock passes, the collector
        # republishes next_unlock_at=None -- "read, nothing pending" -- but the
        # feature store drops None on merge, so the superseded date and pct
        # survive beside it. Clamping that to zero days made proximity 1.0 and
        # reported the axis at its *worst* where the truth is its best.
        # Returning None lets the pct-XOR-days guard drop the term instead:
        # half a reading is no reading.
        logger.warning("next_unlock_at %s is before %s; treating as stale", value, now)
        return None
    return days


def features_from(raw: dict, *, now: datetime) -> Features:
    """Build the scorer's input from one stored feature dict.

    ``now`` dates every time-relative derivation. Pass ``datetime.now(tz=UTC)``
    live and the journal row's own timestamp in replay.
    """
    return Features(
        price_change_pct_24h=raw.get("price_change_pct_24h"),
        volume_spike_ratio=raw.get("volume_spike_ratio"),
        liquidity_usd=_liquidity(raw),
        sentiment_score=raw.get("sentiment_score"),
        social_growth=raw.get("social_growth"),
        news_impact=1.0 if raw.get("has_news") else None,
        market_sentiment=raw.get("market_sentiment"),
        funding_rate_8h=raw.get("funding_rate_8h"),
        long_short_account_ratio=raw.get("long_short_account_ratio"),
        open_interest_change_pct_24h=raw.get("open_interest_change_pct_24h"),
        tvl_change_pct_7d=raw.get("tvl_change_pct_7d"),
        fees_change_pct_7d=raw.get("fees_change_pct_7d"),
        next_unlock_pct_supply=raw.get("next_unlock_pct_supply"),
        next_unlock_days=_unlock_days(raw, now=now),
        has_unlock_schedule=bool(raw.get("has_unlock_schedule")),
        commit_ratio_4w=raw.get("commit_ratio_4w"),
        pr_ratio_4w=raw.get("pr_ratio_4w"),
        days_since_push=raw.get("days_since_push"),
        star_growth_pct_7d=raw.get("star_growth_pct_7d"),
        all_repos_archived=bool(raw.get("all_repos_archived")),
    )
```

- [ ] **Step 4: Lancer le test**

```bash
python -m pytest tests/test_features_from_replay.py -v
```

Attendu : 8 passed.

- [ ] **Step 5: Commit**

```bash
git add services/decision-engine/app/features_map.py tests/test_features_from_replay.py
git commit -m "feat(decision-engine): le mapping raw -> Features devient pur et date

Extrait d'engine.py pour que le rejeu hors-ligne execute exactement le
mapping que la production execute, sans tirer aiokafka.

_unlock_days prend un instant de reference au lieu d'appeler datetime.now():
rejoue une semaine plus tard, chaque date stockee serait dans le passe, le
terme disparaitrait, et le score recalcule differerait en silence de celui
que la production a emis.

Le module ne lit plus que les features stockees. Les quatre champs de tete de
l'AnalysisEvent en sortent deja chez le producteur, donc une source unique
est une source qui ne peut pas diverger -- l'invariant est verrouille par
test chez haiku."
```

---

## Task 8 : Le moteur devient sans état

**Files:**
- Modify: `services/decision-engine/app/engine.py`
- Modify: `services/decision-engine/app/main.py:22-28`
- Test: `tests/test_decision_engine_stateless.py`

`score()` était déjà pur ; `_market` était le dernier état du moteur. Une fois la lecture de régime portée par les features, la décision devient **une fonction pure de sa ligne de journal**. C'est la propriété qui rend le rejeu exact, et elle tient pour tous les recalibrages futurs.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/test_decision_engine_stateless.py
"""La decision est une fonction pure de l'evenement recu.

_market etait le dernier etat du moteur: une lecture de regime tenue en
memoire avec un TTL de 1 h, alimentee par le topic sentiment. Rien ne
l'ecrivait, donc rien ne la rejouait, et le recompute hors-ligne ne pouvait
etre qu'approxime. Portee par les features, elle rend la decision rejouable a
l'identique.
"""

from __future__ import annotations

import inspect
from typing import Any

from service_modules import load_service_module

from cmi_common.events import AnalysisEvent
from cmi_common.events.base import Source
from cmi_common.kafka import Topic

engine_mod = load_service_module("decision-engine", "engine")
main_mod = load_service_module("decision-engine", "main")


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


async def _breakdown(features: dict[str, Any]) -> dict[str, float]:
    producer = FakeProducer()
    engine = engine_mod.DecisionEngine(producer, decision_threshold=0)
    await engine.handle(_analysis(features))
    return producer.published[0][1].meta["breakdown"]


async def test_market_sentiment_is_read_from_the_event() -> None:
    breakdown = await _breakdown(
        {"price_change_pct_24h": 4.0, "volume_24h_usd": 1e6, "market_sentiment": -0.4}
    )
    assert "news_score" in breakdown


async def test_without_it_the_news_axis_is_excluded() -> None:
    """L'exclusion est le comportement voulu: un axe absent n'est pas note 0,
    il sort du denominateur."""
    breakdown = await _breakdown({"price_change_pct_24h": 4.0, "volume_24h_usd": 1e6})
    assert "news_score" not in breakdown


async def test_an_earlier_event_cannot_change_a_later_score() -> None:
    """La propriete que le rejeu exige."""
    raw = {"price_change_pct_24h": 4.0, "volume_24h_usd": 1e6, "market_sentiment": 0.9}
    producer = FakeProducer()
    engine = engine_mod.DecisionEngine(producer, decision_threshold=0)
    await engine.handle(_analysis({**raw, "market_sentiment": -0.9}))
    await engine.handle(_analysis(raw))
    assert producer.published[1][1].meta["breakdown"] == await _breakdown(raw)


def test_the_engine_holds_no_market_state() -> None:
    source = inspect.getsource(engine_mod.DecisionEngine)
    assert "_market" not in source
    assert "market_ttl_seconds" not in source


def test_the_engine_no_longer_subscribes_to_sentiment() -> None:
    """La souscription servait uniquement a alimenter l'etat supprime. La
    garder ferait tourner un consommateur qui defile un topic sans rien en
    faire."""
    assert "Topic.SENTIMENT" not in inspect.getsource(main_mod)
```

- [ ] **Step 2: Lancer le test et vérifier qu'il échoue**

```bash
python -m pytest tests/test_decision_engine_stateless.py -v
```

Attendu : `test_market_sentiment_is_read_from_the_event` ÉCHOUE (la clé est ignorée aujourd'hui), ainsi que les deux tests d'inspection.

- [ ] **Step 3: Alléger `engine.py`**

Supprimer : `MARKET_SYMBOL` (l.29-31), `_liquidity` (l.34-53), `_unlock_days` (l.56-90), les paramètres `market_ttl_seconds` / `clock` du constructeur, l'attribut `_market`, la méthode `_market_sentiment` et la branche `SentimentEvent` de `handle`.

```python
# services/decision-engine/app/engine.py — constructeur
    def __init__(
        self,
        producer: EventProducer,
        *,
        decision_threshold: int = 70,
    ) -> None:
        self._producer = producer
        self._threshold = decision_threshold
```

```python
# services/decision-engine/app/engine.py — handle
    async def handle(self, event: BaseEvent) -> None:
        if isinstance(event, AnalysisEvent):
            await self._on_analysis(event)
```

```python
# services/decision-engine/app/engine.py — debut de _on_analysis
    async def _on_analysis(self, event: AnalysisEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.ANALYSIS.value, event.event_type).inc()
        features = features_from(
            event.meta.get("features", {}), now=datetime.now(tz=UTC)
        )
        result = score(features)
```

Le reste de `_on_analysis` — le rejet sous seuil, la construction du `DecisionEvent` — est inchangé.

Ajuster les imports : retirer `SentimentEvent`, ainsi que `Callable` et `time` s'ils ne servent plus ; ajouter

```python
from .features_map import features_from
```

et conserver `from datetime import UTC, datetime`.

- [ ] **Step 4: Retirer la souscription au topic sentiment**

```python
# services/decision-engine/app/main.py:22-28
    consumer = EventConsumer(
        settings.kafka,
        [Topic.ANALYSIS],
        engine.handle,
        group_id="decision-engine",
    )
```

- [ ] **Step 5: Lancer les tests**

```bash
python -m pytest tests/test_decision_engine_stateless.py tests/test_decision_engine_context_features.py tests/test_decision_engine_rejection.py -v
```

Attendu : tout vert. `test_decision_engine_context_features.py` passe ses features via `meta["features"]` — il continue de fonctionner. S'il contient un test de la lecture de régime via `SentimentEvent`, le déplacer vers le nouveau chemin (`meta["features"]["market_sentiment"]`) : le comportement testé demeure, son point d'entrée change.

- [ ] **Step 6: Suite complète et lint**

```bash
python -m pytest tests/ -q && make lint
```

Attendu : verte, exit 0.

- [ ] **Step 7: Commit**

```bash
git add services/decision-engine/ tests/
git commit -m "refactor(decision-engine): le moteur perd son dernier etat

_market etait une lecture de regime tenue en memoire, alimentee par le topic
sentiment, que rien n'ecrivait et que rien ne pouvait donc rejouer. Portee
par les features depuis haiku, elle rend la decision fonction pure de sa
ligne de journal -- la propriete dont depend l'exactitude du recompute, et
elle tient pour tous les recalibrages futurs.

La souscription a Topic.SENTIMENT part avec: elle n'alimentait que cet etat."
```

---

## Task 9 : `scripts/pick_threshold.py`

**Files:**
- Create: `scripts/pick_threshold.py`

Lecture seule. S'exécute sur le VPS dans le conteneur decision-engine, qui porte déjà `app.scoring`, `app.features_map` et l'accès base.

- [ ] **Step 1: Écrire le script**

```python
#!/usr/bin/env python
"""Choisit DECISION_THRESHOLD par rejeu du journal de decision.

Le seuil est une **vanne de debit**, pas un reglage de finesse. La production
emet ~11 500 analyses par heure, soit ~276 000 par jour, contre
MAX_ORDERS_PER_HOUR=10 en aval: meme le 99,9e percentile laisserait passer
~276 decisions par jour. L'operateur choisit donc un debit, et le script rend
le seuil qui le produit -- l'inverse ne veut rien dire.

Ce qu'il ne faut PAS faire, et qui a ete propose deux fois: un ratio SQL sur
`decision_journal.score / confidence`. Ces colonnes ne sont pas la sortie de ce
modele -- `ai-worker-sonnet/app/journal.py` les ecrit depuis `analysis.*`,
c'est-a-dire le scoreur a quatre facteurs de haiku, dont la confiance est une
affine plancherisee a 0.25 sans rapport avec le poids present. Mesure sur 30k
echantillons, l'identite est violee de plus d'un point sur 24% des lignes, au
pire de +33,8. Les deux erreurs poussent le seuil trop haut, ce qui restaure
le deadlock que ce travail supprime.

Le rapport de presence par axe sort AVANT tout nombre, et le script refuse de
proposer un seuil si un axe est a 0%. C'est le garde-fou central: le 2026-08-04,
l'axe positioning etait a 0 sur 1 281 511 lignes -- collector-binance-futures
echouait a tous ses cycles en se declarant healthy -- et une calibration lancee
ce jour-la aurait rendu un nombre parfaitement plausible et faux.

Usage :
    python scripts/pick_threshold.py --days 7 --decisions-per-day 200
    python scripts/pick_threshold.py --days 7            # rapport seul
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

# Le script touche un service qui embarque un package nomme `app`, comme tous
# les autres. On reutilise le chargeur des tests, qui enregistre chaque service
# sous un alias distinct -- un `sys.path.insert` figerait `sys.modules["app"]`.
_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "tests"))
sys.path.insert(0, os.path.join(_ROOT, "libs", "cmi_common"))

from service_modules import load_service_module  # noqa: E402
from sqlalchemy import select  # noqa: E402

from cmi_common.config import get_settings  # noqa: E402
from cmi_common.db import Database, DecisionJournal  # noqa: E402

_scoring = load_service_module("decision-engine", "scoring")
features_from = load_service_module("decision-engine", "features_map").features_from
score = _scoring.score
WEIGHTS = _scoring.WEIGHTS

#: Cle du dict de features qui atteste qu'un axe a ete *lu* pour cette ligne.
#: Une seule cle par axe suffit: la presence se mesure a la source du signal,
#: pas au fait que l'axe ait ete score, qui peut dependre d'un XOR interne.
AXIS_PROBE = {
    "volume_growth": "volume_spike_ratio",
    "social_score": "social_growth",
    "news_score": "sentiment_score",
    "market_trend": "price_change_pct_24h",
    "liquidity_score": "volume_24h_usd",
    "positioning": "funding_rate_8h",
    "fundamentals": "tvl_change_pct_7d",
    "developer_activity": "commit_ratio_4w",
}


@dataclass
class Scan:
    """Tout ce que le rejeu retient, et rien de plus.

    La fenetre ne tient pas en memoire: 1 414 216 lignes sur 7 jours, 368 Mo de
    JSONB compresse, contre 1 996 Mo disponibles sur le VPS. Une fois converties
    en dicts Python, les features pesent plusieurs fois leur taille sur disque.
    Le parcours est donc un flux, et l'on n'accumule que des agregats bornes.

    `best_by_symbol_day` merite un mot: compter les symboles distincts par seuil
    en gardant un ensemble par seuil couterait jusqu'a cent insertions par ligne,
    soit ~141 millions. Retenir le meilleur score de chaque couple (symbole, jour)
    donne la meme reponse pour tout seuil, dans ~10 000 entrees.
    """

    total: int = 0
    no_evidence: int = 0
    #: Lignes portant une lecture de regime. Zero signifie que la fenetre
    #: precede le deploiement qui la journalise, pas qu'il n'y en avait pas.
    regime_seen: int = 0
    presence: Counter = field(default_factory=Counter)
    score_counts: Counter = field(default_factory=Counter)
    best_by_symbol_day: dict = field(default_factory=dict)
    sonnet_scores: list = field(default_factory=list)


async def _scan(days: int) -> Scan:
    db = Database(get_settings().db)
    since = datetime.now(tz=UTC) - timedelta(days=days)
    stmt = (
        select(
            DecisionJournal.time,
            DecisionJournal.symbol,
            DecisionJournal.features,
            DecisionJournal.sonnet_score,
        )
        .where(DecisionJournal.time > since)
        .execution_options(yield_per=5_000)
    )
    scan = Scan()
    async with db.sessionmaker() as session:
        result = await session.stream(stmt)
        async for time_, symbol, raw, sonnet in result:
            scan.total += 1
            raw = raw or {}
            for axis, probe in AXIS_PROBE.items():
                if probe in raw:
                    scan.presence[axis] += 1
            if "market_sentiment" in raw:
                scan.regime_seen += 1
            if sonnet is not None:
                scan.sonnet_scores.append(sonnet)
            outcome = score(features_from(raw, now=time_))
            if outcome.confidence == 0.0:
                # _MIN_PRESENT_WEIGHT a refuse de renormaliser: trop peu de
                # preuve. Ces lignes n'auraient produit aucune decision, quel
                # que soit le seuil; les compter tirerait la distribution vers
                # le bas et donnerait un seuil trop permissif.
                scan.no_evidence += 1
                continue
            value = outcome.opportunity_score
            scan.score_counts[value] += 1
            key = (symbol, time_.date().isoformat())
            if value > scan.best_by_symbol_day.get(key, -1):
                scan.best_by_symbol_day[key] = value
    await db.engine.dispose()
    return scan


def _threshold_for(counts: Counter, target_per_day: int, days: int) -> int:
    """Plus petit seuil entier dont le debit ne depasse pas la cible."""
    budget = target_per_day * days
    running = 0
    for value in range(100, -1, -1):
        running += counts[value]
        if running > budget:
            return value + 1
    return 0


def _percentile(counts: Counter, pct: float) -> int:
    """Percentile lu sur un histogramme, sans materialiser la serie."""
    total = sum(counts.values())
    target = total * pct / 100.0
    running = 0
    for value in range(0, 101):
        running += counts[value]
        if running >= target:
            return value
    return 100


def _report(scan: Scan, args) -> int:
    print(f"\n=== {scan.total} lignes sur {args.days} jours ===\n")

    print("Presence par axe (part des lignes ou la feature a ete lue) :")
    for axis, weight in sorted(WEIGHTS.items(), key=lambda kv: -kv[1]):
        pct = 100.0 * scan.presence[axis] / scan.total
        flag = "  <-- MUET" if scan.presence[axis] == 0 else ""
        print(f"  {axis:<20} poids {weight:<7.4f} {pct:6.2f} %{flag}")

    dark = [axis for axis in AXIS_PROBE if scan.presence[axis] == 0]
    if dark:
        print(
            f"\nREFUS. Axes muets : {dark}.\n"
            "Un axe absent est *exclu* du denominateur, pas note zero. Un seuil\n"
            "calibre ici vaudrait pour un modele ampute, et deviendrait faux des\n"
            "que ces axes se remettent a parler -- sans erreur ni test rouge.\n"
            "Reparer la collecte, laisser tourner 24 h, relancer."
        )
        return 1

    # La lecture de regime n'est pas un axe -- elle alimente news_score -- mais
    # son absence *totale* signifie que la fenetre precede le deploiement qui la
    # fait voyager avec les features. Le rejeu de ces lignes-la ne reproduirait
    # pas ce que la production avait calcule: le moteur, lui, avait la valeur en
    # memoire. Une absence partielle est normale et attendue: mesure sur 14
    # jours, 38 ecarts d'alimentation sur 399 depassent le TTL d'une heure.
    if scan.regime_seen == 0:
        print(
            "\nREFUS. `market_sentiment` absent de toutes les lignes.\n"
            "La fenetre precede le deploiement qui le journalise. Le moteur\n"
            "avait pourtant cette valeur en memoire au moment de decider, donc\n"
            "le rejeu de ces lignes serait faux d'un axe entier sur le tiers des\n"
            "symboles qui n'ont pas de sentiment propre.\n"
            "Attendre 24 h apres le deploiement, puis relancer avec --days 1."
        )
        return 1

    scored = sum(scan.score_counts.values())
    if not scored:
        print("\nAucune ligne scorable sur la fenetre.")
        return 1
    print(
        f"\n{scan.no_evidence} lignes sans preuve suffisante "
        f"(_MIN_PRESENT_WEIGHT), exclues.\n{scored} lignes scorees."
    )

    print("\nDistribution des scores recomputes :")
    for label, pct in (("p50", 50.0), ("p90", 90.0), ("p99", 99.0), ("p99.9", 99.9)):
        print(f"  {label:<6} {_percentile(scan.score_counts, pct)}")
    print(f"  max    {max(scan.score_counts)}")

    if args.decisions_per_day is None:
        print("\nAucun debit cible donne (--decisions-per-day) : rapport seul.")
        return 0

    threshold = _threshold_for(scan.score_counts, args.decisions_per_day, args.days)
    passing = sum(n for value, n in scan.score_counts.items() if value >= threshold)
    symbol_days = sum(1 for best in scan.best_by_symbol_day.values() if best >= threshold)
    print(
        f"\n=== Pour {args.decisions_per_day} decisions/jour ===\n"
        f"  DECISION_THRESHOLD = {threshold}\n"
        f"  debit reel         : {passing / args.days:.1f} decisions/jour\n"
        f"  symboles distincts : {symbol_days / args.days:.1f} par jour\n"
        "\nLes deux derniers nombres different parce qu'un meme symbole peut\n"
        "franchir le seuil plusieurs fois par heure. Le second est le nombre\n"
        "d'opportunites; le premier, le nombre d'evenements a absorber."
    )

    print("\nEffet de RISK_MIN_SCORE sur cette sous-population :")
    for floor in (threshold - 10, threshold - 5, threshold, threshold + 5):
        if floor < 0:
            continue
        effective = max(threshold, floor)
        kept = sum(n for value, n in scan.score_counts.items() if value >= effective)
        print(f"  RISK_MIN_SCORE={floor:<4} -> {kept / args.days:.1f}/jour")

    sonnet = sorted(scan.sonnet_scores)
    if sonnet:
        print(
            f"\nSeconde population filtree par le meme plancher : {len(sonnet)} "
            "decisions Sonnet.\n"
            f"  p50 {sonnet[len(sonnet) // 2]}   max {sonnet[-1]}\n"
            "Leur score sort d'un LLM, pas de l'echelle a huit axes. Un plancher\n"
            "calibre sur la premiere population est arbitraire pour celle-ci --\n"
            "c'est deja ce qui bloquait en juillet. Signale, pas resolu."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--decisions-per-day",
        type=int,
        default=None,
        help="debit cible en aval; sans lui, le script ne sort que le rapport",
    )
    args = parser.parse_args()
    scan = asyncio.run(_scan(args.days))
    if not scan.total:
        print("Aucune ligne sur la fenetre demandee.")
        return 1
    return _report(scan, args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Vérifier que le script se charge**

```bash
python scripts/pick_threshold.py --help
```

Attendu : l'aide s'affiche. Le chargement des modules de service se produit à l'import, donc une erreur ici signifie que `features_map` ou `scoring` n'est pas atteignable — pas que la base est injoignable, qui n'est touchée qu'à l'exécution.

- [ ] **Step 3: `make lint`**

```bash
make lint
```

Attendu : exit 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/pick_threshold.py
git commit -m "feat(scripts): choisir DECISION_THRESHOLD par rejeu du journal

Le seuil est une vanne de debit: ~276 000 analyses par jour contre
MAX_ORDERS_PER_HOUR=10 en aval, donc l'operateur choisit un debit et le
script rend le seuil correspondant. Le nombre de symboles distincts sort a
cote du nombre de decisions, parce qu'un meme symbole franchit le seuil
plusieurs fois par heure.

Le rapport de presence par axe sort avant tout nombre, et le script refuse de
proposer un seuil si un axe est muet: le 2026-08-04 positioning etait a 0 sur
1 281 511 lignes, et une calibration lancee ce jour-la aurait rendu un nombre
parfaitement plausible et faux."
```

---

## Task 10 : Vérification live et pose des valeurs

**Files:**
- Create: `docs/superpowers/plans/2026-08-04-decision-valve-RESUME.md`

Rien de ce qui précède ne prouve que la production se comporte comme la suite de tests. Ces vérifications se lancent **après** déploiement, dans l'ordre.

- [ ] **Step 1: Vérifier que les collectors ne tombent plus**

```bash
ssh <VPS_USER>@<VPS_HOST> "docker logs --since 30m bottrading-collector-binance-futures-1 2>&1 | grep -c 'tick failed'"
```

Attendu : **0**. Avant le correctif : 6 en 30 min, un par cycle de 5 min.

- [ ] **Step 2: Vérifier que l'axe positioning se peuple**

```bash
ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-postgres-1 psql -U cmi -d cmi -c \"select count(*) filter (where features ? 'funding_rate_8h') funding, count(*) filter (where features ? 'market_sentiment') regime, count(*) total from decision_journal where time > now() - interval '30 minutes';\""
```

Attendu : `funding` > 0 et `regime` > 0. Si `funding` reste à 0 alors que les logs sont propres, le défaut est en aval du collector — vérifier que `DerivativesEvent` atteint bien haiku.

- [ ] **Step 3: Vérifier que `/health` sait dire non**

```bash
ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-collector-defillama-1 curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/health"
```

Attendu : `200`. Puis, pour prouver que le 503 est atteignable, couper l'accès réseau du collector le moins critique et réinterroger après trois cycles :

```bash
ssh <VPS_USER>@<VPS_HOST> "docker network disconnect cmi bottrading-collector-defillama-1"
# attendre trois cycles de poll, puis
ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-collector-defillama-1 curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/health"
ssh <VPS_USER>@<VPS_HOST> "docker network connect cmi bottrading-collector-defillama-1 && docker restart bottrading-collector-defillama-1"
```

Attendu : `503` au second appel, puis retour à `200` après reconnexion et redémarrage.

- [ ] **Step 4: Vérifier que le rejeu est exact**

C'est la vérification qui vaut pour toutes les autres : si le rejeu reproduit le score que la production a émis, le seuil calculé porte sur le modèle réellement exécuté.

**Elle exige que des décisions existent**, donc que le seuil soit franchissable. Tant que `DECISION_THRESHOLD=101`, la table `decisions` reste vide et la jointure ci-dessous ne rend rien. Poser alors une valeur haute mais atteignable — `DECISION_THRESHOLD=95` dans le `.env` du VPS, puis `docker compose -f docker-compose.vps.yml up -d decision-engine` — laisser passer quelques décisions, vérifier, et seulement ensuite poser la valeur définitive de l'étape 6.

Ne **pas** comparer contre `decision_journal.score` : cette colonne vient du scoreur à quatre facteurs de haiku, pas de ce modèle.

```bash
ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-postgres-1 psql -U cmi -d cmi -t -A -F'|' -c \"select dj.time, d.opportunity_score, dj.features::text from decision_journal dj join decisions d on d.correlation_id = dj.correlation_id where dj.time > now() - interval '2 hours' limit 5;\""
```

Pour chaque ligne, rejouer dans le conteneur :

```bash
ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-decision-engine-1 python -c \"
import json, sys
from datetime import datetime
from app.features_map import features_from
from app.scoring import score
row_time = datetime.fromisoformat(sys.argv[1])
print(score(features_from(json.loads(sys.argv[2]), now=row_time)).opportunity_score)
\" '<time>' '<features>'"
```

Attendu : le score recalculé **égale** `decisions.opportunity_score`. Un écart signifie que le rejeu n'est pas fidèle et que le seuil qui en sortirait serait faux — s'arrêter et diagnostiquer avant de poser quoi que ce soit.

- [ ] **Step 5: Lancer la calibration**

Après **24 h** de collecte à huit axes :

```bash
ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-decision-engine-1 python /app/scripts/pick_threshold.py --days 1"
```

Attendu : aucun axe à 0 %, le rapport sort. Choisir alors un débit et relancer avec `--decisions-per-day N`.

Si le script n'est pas présent dans l'image, le copier : `scp scripts/pick_threshold.py <VPS_USER>@<VPS_HOST>:/opt/bottrading/scripts/` puis monter le répertoire, ou l'ajouter au `COPY` du Dockerfile dans un commit dédié.

- [ ] **Step 6: Poser les deux valeurs dans le même changement**

Dans `/opt/bottrading/.env` sur le VPS :

```bash
DECISION_THRESHOLD=<valeur rendue par le script>
RISK_MIN_SCORE=<valeur decidee au vu du tableau>
```

puis

```bash
ssh <VPS_USER>@<VPS_HOST> "cd /opt/bottrading && docker compose -f docker-compose.vps.yml up -d decision-engine risk-engine"
```

Ce sont des variables d'environnement : un redémarrage suffit, pas un redéploiement.

**`RISK_MIN_SCORE` doit bouger dans le même changement.** `docker-compose.vps.yml:381` le dit explicitement : 70 a été calibré contre le scoreur v1 non renormalisant, dont le plafond documenté était 61. Rouvrir `DECISION_THRESHOLD` seul dégate aussi ce plancher-là, sur une échelle qui a changé de sens entre-temps.

- [ ] **Step 7: Observer 24 h, puis écrire le RESUME**

Créer `docs/superpowers/plans/2026-08-04-decision-valve-RESUME.md` avec : les valeurs posées et leur date, la sortie complète du script (présence par axe incluse), le débit observé sur 24 h, et ce qui reste ouvert — la sémantique de `RISK_MIN_SCORE` face aux deux populations, et le dimensionnement par confiance de `rules.py:98`, qui n'a pas de variable d'environnement et rétrécit de 31 % les positions des symboles n'ayant que les cinq axes historiques.

- [ ] **Step 8: Commit**

```bash
git add docs/superpowers/plans/2026-08-04-decision-valve-RESUME.md
git commit -m "docs: valeurs posees et mesures apres calibration"
```

---

## Couverture du spec

| Exigence du spec | Tâche |
|---|---|
| Quatre déclarations `DateTime(timezone=True)`, aucune migration | 1 |
| Test de régression tz qui échoue sur le code actuel | 1 |
| `_naive_utc` et la convention naive UTC supprimés | 2 |
| Compteur partagé dans `cmi_common.observability` | 3 |
| Registre d'échecs consécutifs dans `runner.py` | 3 |
| `/health` répond 503 au-delà de 3 échecs consécutifs | 4 |
| Clé de régime dédiée à 3600 s, pas `FEATURE_TTL` | 5 |
| `market_sentiment` estampillé dans les features publiées | 6 |
| `features_from` pur et partagé, instant de référence explicite | 7 |
| `_unlock_days` daté plutôt que `datetime.now()` | 7 |
| Le moteur perd son état et sa souscription `sentiment` | 8 |
| Présence par axe avant tout nombre ; refus si un axe est muet | 9 |
| Débit cible → seuil, et symboles distincts à côté des décisions | 9 |
| Effet mesuré de `RISK_MIN_SCORE` sur les deux populations | 9 |
| Harnais live : présence, 503, `market_sentiment`, rejeu exact | 10 |
| Séquence de déploiement, deux valeurs dans le même changement | 10 |

## Deux écarts assumés par rapport au spec

**La tâche 2 est plus large que le spec ne l'annonçait.** Le spec parlait de « `_naive_utc` et ses trois appels ». Il y a en réalité **cinq** sites de dépouillage du fuseau, répartis sur quatre fichiers, dont deux justifiés par des docstrings factuellement fausses. Les laisser en place transformerait l'erreur bruyante d'aujourd'hui en décalage silencieux, puisque asyncpg interprète un datetime naïf dans le fuseau local du conteneur. La correction n'est donc complète qu'à cinq.

**Le rejeu exact n'est vérifiable qu'après réouverture partielle de la vanne.** L'étape 4 de la tâche 10 compare le score rejoué à `decisions.opportunity_score`, table qui reste vide tant que le seuil vaut 101. La procédure prévoit un seuil intermédiaire atteignable le temps de la vérification. C'est une contrainte de l'ordre des opérations, pas un trou du plan.
