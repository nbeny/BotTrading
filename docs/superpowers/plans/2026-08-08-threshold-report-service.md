# Rapport de calibration du seuil — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendre le rapport de `pick_threshold.py` consultable depuis `/journal`, recalculé toutes les 6 h et rejouable à la demande, sans jamais perdre les verdicts de refus qui font sa valeur.

**Architecture:** L'analyse est extraite de `pick_threshold.py` en un module pur dans `decision-engine` (seul service autorisé à importer `scoring.py`) ; le CLI et un service en deviennent deux faces. Le scan tourne en tâche périodique ou sur commande `control.commands`, sous verrou Redis qui sert aussi d'état de job ; le résultat est persisté en JSONB et servi en lecture seule par api-gateway.

**Tech Stack:** Python 3.12 + SQLAlchemy 2 async + Alembic + aiokafka + Redis, Next 15 + MUI 6 + TanStack Query 5 + vitest.

**Spec :** `docs/superpowers/specs/2026-08-08-threshold-report-service-design.md`

---

## Faits vérifiés dans le code

1. **Head de migration = `0019`** (`0019_market_snapshots.py`). La nouvelle migration est donc **`0020`**, `down_revision = "0019"`. Patron : `0010_events_market.py` (mais ici table **simple**, pas d'hypertable, pas de rétention).
2. **`ControlCommand`** (`libs/cmi_common/cmi_common/events/control.py`) est une enum de valeurs snake_case (`SET_MODE = "set_mode"`, …). Nouvelle entrée : `RUN_THRESHOLD_SCAN = "run_threshold_scan"`.
3. **Patron de route control-api** : `services/control-api/app/routers/orders.py` — une classe `XService` avec `self._pub.publish(ControlCommand.X, payload, issued_by=...)`, un `router = APIRouter(prefix=..., tags=[...])`, un `_svc(request)` lisant `request.app.state.<x>_service`, et `principal: Principal = Depends(require_principal)`. Le service est instancié dans `services/control-api/app/main.py::_startup` et le router monté en bas du fichier.
4. **`decision-engine/app/main.py`** est court (40 lignes) : `_startup` crée producer + `DecisionEngine` + un `EventConsumer` sur `Topic.ANALYSIS`, et lance `consumer.run()` en tâche. Il n'a **ni Redis ni base** aujourd'hui — les deux sont à câbler (patron Redis : `services/control-api/app/main.py`, `Cache(settings.redis)` ; patron DB : `Database(settings.db)`).
5. **`scripts/pick_threshold.py`** : `_scan(days)` ouvre sa propre `Database`, streame en `yield_per=5000` ; `_report(scan, args)` imprime et retourne un code de sortie (0 ok, 1 refus). Constantes à déplacer avec l'analyse : `AXIS_PROBE`, `WEIGHTS` (importé de scoring), `MIN_PRESENCE_PCT`, `_RISK_MIN_CONFIDENCE`, `_REGIME_GAP_WARN_HOURS`, `_DAY_VOLUME_WARN_RATIO`, `_threshold_for`, `_percentile`, `_counts_ge`, `_bounded_passing`.

**Règles transverses** : les textes de refus sont repris **mot pour mot** (ce sont eux qui distinguent « collecteur cassé » de « axe légitimement rare ») ; inconnu = `null` = « — » ; tests au root `tests/` via `load_service_module` ; ruff/black/mypy sur les fichiers touchés ; ne pas corriger le bruit pré-existant.

## Structure des fichiers

**Créés** : `services/decision-engine/app/threshold_scan.py` (Scan + analyze, pur sauf `scan_window`), `services/decision-engine/app/threshold_job.py` (verrou, persistance, déclencheurs), `services/control-api/app/routers/analysis.py`, `migrations/alembic/versions/0020_threshold_reports.py`, `frontend/src/components/journal/ThresholdReportPanel.tsx`, `frontend/src/lib/types/threshold.ts`, `frontend/src/lib/mock/threshold.ts`, `frontend/src/app/api/mock/systems/journal/threshold/route.ts`, `frontend/src/app/api/mock/analysis/threshold-scan/route.ts`.
**Modifiés** : `scripts/pick_threshold.py` (devient le formateur), `libs/cmi_common/.../events/control.py`, `libs/cmi_common/.../db/models.py` + `__init__.py`, `services/decision-engine/app/main.py`, `services/control-api/app/main.py`, `services/api-gateway/app/journal_api.py` + `read_contract.py`, `frontend/src/lib/api/endpoints.ts`, `frontend/src/app/(app)/journal/page.tsx`, `docker-compose*.yml`.

---

### Task 1 : Modèle + migration 0020

**Files:**
- Modify: `libs/cmi_common/cmi_common/db/models.py` (après `DeveloperSnapshot`), `libs/cmi_common/cmi_common/db/__init__.py`
- Create: `migrations/alembic/versions/0020_threshold_reports.py`

- [ ] **Step 1 : Modèle** — ajouter à `models.py` :

```python
class ThresholdReport(Base):
    """One threshold-calibration scan, as run by decision-engine.

    A failed scan is stored too (``status="error"``): the panel must be able to
    say "last scan failed at 14:02" rather than show a stale report as if it
    were fresh. `payload` is the serialised ThresholdReport dataclass; keeping
    it opaque here is deliberate — its shape is owned by
    `decision-engine/app/threshold_scan.py`, and a column per field would have
    to be migrated every time the report gains a line.
    """

    __tablename__ = "threshold_reports"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    window_days: Mapped[int] = mapped_column(Integer)
    target_per_day: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    duration_s: Mapped[float | None] = mapped_column(Float, default=None)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
```

Vérifier que `Text` et `JSONB` sont importés dans models.py (ils le sont pour `PipelineRejection`/`Decision` ; sinon compléter). Exporter `ThresholdReport` dans `db/__init__.py` (import + `__all__`), comme `DerivativesSnapshot`.

- [ ] **Step 2 : Migration** — `0020_threshold_reports.py`, `revision = "0020"`, `down_revision = "0019"` :

```python
"""threshold_reports

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-08
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Table simple, pas d'hypertable : quelques lignes par jour, lues par
    # `ORDER BY time DESC LIMIT 1`. Un chunking Timescale ici n'apporterait rien
    # et compliquerait le downgrade.
    op.create_table(
        "threshold_reports",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("window_days", sa.Integer, nullable=False),
        sa.Column("target_per_day", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text),
        sa.Column("duration_s", sa.Float),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_table("threshold_reports")
```

- [ ] **Step 3 : Vérifier** — `python -c "import sys; sys.path.insert(0,'libs/cmi_common'); from cmi_common.db import ThresholdReport; print('ok')"` ; `python -m py_compile migrations/alembic/versions/0020_threshold_reports.py` ; `python -m pytest -q` sans régression ; ruff/black/mypy sur models.py.

- [ ] **Step 4 : Commit**

```bash
git add libs/cmi_common/cmi_common/db/models.py libs/cmi_common/cmi_common/db/__init__.py migrations/alembic/versions/0020_threshold_reports.py
git commit -m "feat(db): table threshold_reports"
```

---

### Task 2 : Extraction de l'analyse en module pur

**Files:**
- Create: `services/decision-engine/app/threshold_scan.py`
- Modify: `scripts/pick_threshold.py`
- Test: `tests/test_threshold_scan.py`

**Lire d'abord `scripts/pick_threshold.py` en entier** (558 lignes). L'extraction déplace : `AXIS_PROBE`, `_check_axis_probe_matches_weights`, `MIN_PRESENCE_PCT`, `_RISK_MIN_CONFIDENCE`, `_DEFAULT_RISK_MIN_SCORE`, `_REGIME_GAP_WARN_HOURS`, `_DAY_VOLUME_WARN_RATIO`, `Scan`, `_threshold_for`, `_percentile`, `_counts_ge`, `_bounded_passing`, `_scan` (renommé `scan_window`, prenant une **session** au lieu d'ouvrir sa propre Database), et **toute la logique de décision** de `_report`. Le script conserve uniquement le formatage texte et l'`argparse`.

- [ ] **Step 1 : Écrire le test qui échoue** — `tests/test_threshold_scan.py` :

```python
"""L'analyse du scan de seuil, pure : mêmes verdicts que le CLI, sans base.

Le garde central de ce module est le refus : un axe muet doit empêcher toute
proposition de seuil, parce qu'un axe absent est EXCLU du dénominateur de
renormalisation, pas noté zéro -- un seuil calibré ainsi vaudrait pour un
modèle amputé de ce poids.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from service_modules import load_service_module

ts = load_service_module("decision-engine", "threshold_scan")

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _full_scan(total: int = 1000) -> "ts.Scan":
    """Un scan où les huit axes sont largement présents et le régime lu."""
    scan = ts.Scan(since=NOW - timedelta(days=7))
    scan.total = total
    scan.regime_seen = total
    scan.min_time = NOW - timedelta(days=7)
    scan.min_time_with_regime = NOW - timedelta(days=7)
    for axis in ts.AXIS_PROBE:
        scan.presence[axis] = total
    scan.score_counts = Counter({50: total // 2, 80: total // 2})
    scan.confidence_pass_counts = Counter({80: total // 2})
    scan.by_day = Counter({(NOW - timedelta(days=d)).date().isoformat(): total // 7 for d in range(7)})
    scan.best_by_symbol_day = {("BTC", "2026-08-07"): 80}
    return scan


def test_full_scan_proposes_a_threshold() -> None:
    report = ts.analyze(_full_scan(), days=7, target_per_day=200, now=NOW)
    assert report.refusal is None
    assert report.proposal is not None
    assert report.proposal["threshold"] > 0
    assert len(report.axes) == len(ts.AXIS_PROBE)
    assert all(not a["mute"] for a in report.axes)


def test_axes_are_ordered_by_weight_desc() -> None:
    report = ts.analyze(_full_scan(), days=7, target_per_day=200, now=NOW)
    weights = [a["weight"] for a in report.axes]
    assert weights == sorted(weights, reverse=True)


def test_mute_axis_refuses_and_proposes_nothing() -> None:
    scan = _full_scan()
    scan.presence["positioning"] = 0          # le cas du 2026-08-04
    report = ts.analyze(scan, days=7, target_per_day=200, now=NOW)
    assert report.refusal is not None
    assert report.refusal["code"] == "MUTE_AXES"
    assert "positioning" in report.refusal["title"]
    # Le texte qui distingue « collecteur casse » de « axe legitimement rare »
    # est la valeur du refus : il doit voyager avec lui.
    assert "collecte" in report.refusal["detail"]
    assert "fundamentals" in report.refusal["detail"]
    assert report.proposal is None


def test_raw_count_travels_with_the_percentage() -> None:
    """1 ligne sur 1 281 511 s'affiche « 0.0% » : le compte brut doit suivre."""
    scan = _full_scan(total=1_281_511)
    scan.presence["positioning"] = 1
    report = ts.analyze(scan, days=7, target_per_day=200, now=NOW)
    positioning = next(a for a in report.axes if a["key"] == "positioning")
    assert positioning["seen"] == 1
    assert positioning["mute"] is True


def test_absent_regime_refuses() -> None:
    scan = _full_scan()
    scan.regime_seen = 0
    scan.min_time_with_regime = None
    report = ts.analyze(scan, days=7, target_per_day=200, now=NOW)
    assert report.refusal["code"] == "NO_REGIME"
    assert report.proposal is None


def test_regime_gap_refuses_and_suggests_a_shorter_window() -> None:
    scan = _full_scan()
    scan.min_time_with_regime = NOW - timedelta(days=2)   # journalise depuis 2j
    report = ts.analyze(scan, days=7, target_per_day=200, now=NOW)
    assert report.refusal["code"] == "REGIME_GAP"
    assert report.refusal["suggested_days"] == 2
    assert report.proposal is None


def test_empty_window_refuses_rather_than_dividing_by_zero() -> None:
    report = ts.analyze(ts.Scan(since=NOW - timedelta(days=7)), days=7, target_per_day=200, now=NOW)
    assert report.refusal is not None
    assert report.window["total"] == 0
    assert report.proposal is None


def test_report_serialises_to_json_safe_primitives() -> None:
    import json

    report = ts.analyze(_full_scan(), days=7, target_per_day=200, now=NOW)
    json.dumps(report.to_payload())          # ne doit pas lever
```

Run `pytest tests/test_threshold_scan.py -q` → FAIL (module absent).

- [ ] **Step 2 : Créer `services/decision-engine/app/threshold_scan.py`** — déplacer les constantes et helpers listés ci-dessus depuis le script (copie fidèle, y compris leurs commentaires : ils documentent des mesures de production), puis :

```python
@dataclass
class ThresholdReport:
    """Ce que le CLI imprime et ce que le service persiste, une seule fois.

    `refusal` porte le verdict ET son texte : c'est la partie qui a de la
    valeur. Un refus réduit à un booléen priverait l'opérateur de ce qui
    distingue une collecte cassée d'un axe légitimement rare.
    """

    window: dict[str, Any]
    axes: list[dict[str, Any]]
    refusal: dict[str, Any] | None
    distribution: dict[str, Any]
    proposal: dict[str, Any] | None
    warnings: list[str]
    sonnet: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "axes": self.axes,
            "refusal": self.refusal,
            "distribution": self.distribution,
            "proposal": self.proposal,
            "warnings": self.warnings,
            "sonnet": self.sonnet,
        }


def analyze(
    scan: Scan, *, days: int, target_per_day: int, now: datetime | None = None
) -> ThresholdReport:
    ...
```

`analyze` reprend l'enchaînement de `_report` **dans le même ordre** : présence par axe (triée par poids décroissant, `mute` si `pct < MIN_PRESENCE_PCT`) → refus `MUTE_AXES` → refus `NO_REGIME` → refus `REGIME_GAP` (avec `suggested_days`) → distribution → proposition si une cible est donnée. Les paragraphes explicatifs des refus sont recopiés **mot pour mot** dans le champ `detail`. `now` est un paramètre (défaut `datetime.now(tz=UTC)`) pour que les tests soient déterministes.

`scan_window(session, days) -> Scan` reprend `_scan` mais **reçoit** une session (le script et le job gèrent chacun la leur).

- [ ] **Step 3 : Réécrire `scripts/pick_threshold.py` en formateur** — il importe `threshold_scan` (le `sys.path` hack `_ROOT` existe déjà et pointe la racine ; ajouter le chemin de decision-engine de la même façon), ouvre sa `Database`, appelle `scan_window` puis `analyze`, et imprime le rapport. Le code de sortie reste **1 si `refusal` est posé, 0 sinon**. Aucun changement d'interface CLI (`--days`, `--decisions-per-day`).

- [ ] **Step 4 : Vérifier** — `pytest tests/test_threshold_scan.py -q` → PASS ; `python -m py_compile scripts/pick_threshold.py` ; `pytest -q` complet ; ruff/black/mypy sur les deux fichiers.

- [ ] **Step 5 : Commit**

```bash
git add services/decision-engine/app/threshold_scan.py scripts/pick_threshold.py tests/test_threshold_scan.py
git commit -m "refactor(decision-engine): analyse du seuil en module pur, CLI en formateur"
```

---

### Task 3 : Le job — verrou, persistance, déclencheurs

**Files:**
- Create: `services/decision-engine/app/threshold_job.py`
- Modify: `services/decision-engine/app/main.py`
- Test: `tests/test_threshold_job.py`

- [ ] **Step 1 : Test qui échoue** — `tests/test_threshold_job.py` :

```python
"""Le job de scan : un seul à la fois, un échec s'écrit plutôt que de se taire."""

from __future__ import annotations

from contextlib import asynccontextmanager

from service_modules import load_service_module

job_mod = load_service_module("decision-engine", "threshold_job")


class _Session:
    def __init__(self) -> None:
        self.added: list = []
        self.committed = False

    def add(self, row) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class _Db:
    def __init__(self, session) -> None:
        self._session = session

    def sessionmaker(self):
        return self._session


class _Cache:
    """Verrou Redis factice : `held` dit si quelqu'un le tient déjà."""

    def __init__(self, *, held: bool = False) -> None:
        self.held = held
        self.acquired = 0

    @asynccontextmanager
    async def lock(self, name, timeout=30.0, blocking=True):
        if self.held:
            raise job_mod.ScanBusy(name)
        self.held = True
        self.acquired += 1
        try:
            yield object()
        finally:
            self.held = False


async def test_successful_scan_persists_an_ok_row() -> None:
    session = _Session()
    cache = _Cache()

    async def fake_scan(_session, days):  # noqa: ANN001, ANN202
        return "SCAN"

    def fake_analyze(scan, *, days, target_per_day):  # noqa: ANN001, ANN202
        assert scan == "SCAN"
        return type("R", (), {"to_payload": lambda self: {"axes": []}})()

    job = job_mod.ThresholdScanJob(
        _Db(session), cache, days=7, target_per_day=200,
        scan_window=fake_scan, analyze=fake_analyze,
    )
    await job.run_once()
    assert session.committed is True
    assert len(session.added) == 1
    assert session.added[0].status == "ok"
    assert session.added[0].payload == {"axes": []}


async def test_failed_scan_persists_an_error_row_rather_than_nothing() -> None:
    session = _Session()

    async def boom(_session, days):  # noqa: ANN001, ANN202
        raise RuntimeError("stream died")

    job = job_mod.ThresholdScanJob(
        _Db(session), _Cache(), days=7, target_per_day=200,
        scan_window=boom, analyze=lambda *a, **k: None,
    )
    await job.run_once()
    assert len(session.added) == 1
    assert session.added[0].status == "error"
    assert "stream died" in session.added[0].error
    assert session.added[0].payload == {}


async def test_second_scan_is_ignored_while_one_runs() -> None:
    """Sur 2 vCPU en concurrence avec le pipeline, deux scans simultanés ne
    sont pas une option -- et une demande refusée n'est pas une erreur."""
    session = _Session()
    cache = _Cache(held=True)

    async def never(_session, days):  # noqa: ANN001, ANN202
        raise AssertionError("le scan n'aurait pas du demarrer")

    job = job_mod.ThresholdScanJob(
        _Db(session), cache, days=7, target_per_day=200,
        scan_window=never, analyze=lambda *a, **k: None,
    )
    assert await job.run_once() is False        # refus silencieux, pas d'exception
    assert session.added == []
```

Run → FAIL.

- [ ] **Step 2 : Implémenter `threshold_job.py`**

```python
"""Déclenche, verrouille et persiste un scan de calibration du seuil.

Le verrou Redis est aussi l'état du job : `GET /systems/journal/threshold`
répond `running: true` tant qu'il est tenu, ce qui évite une machine à états
à maintenir en parallèle du travail réel.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any, Callable

from cmi_common.db import ThresholdReport as ThresholdReportRow

from .threshold_scan import analyze as _analyze
from .threshold_scan import scan_window as _scan_window

logger = logging.getLogger(__name__)

LOCK_NAME = "threshold-scan"
#: Large devant la durée observée du scan (minutes), assez court pour qu'un
#: processus tué ne bloque pas la demande suivante indéfiniment.
LOCK_TIMEOUT_S = 1800.0


class ScanBusy(RuntimeError):
    """Levée par le cache quand le verrou est déjà tenu."""


class ThresholdScanJob:
    def __init__(
        self,
        db: Any,
        cache: Any,
        *,
        days: int,
        target_per_day: int,
        scan_window: Callable[..., Any] = _scan_window,
        analyze: Callable[..., Any] = _analyze,
    ) -> None:
        self._db = db
        self._cache = cache
        self._days = days
        self._target = target_per_day
        self._scan_window = scan_window
        self._analyze = analyze

    async def run_once(self) -> bool:
        """True si le scan a tourné, False s'il a été refusé (déjà en cours)."""
        try:
            async with self._cache.lock(LOCK_NAME, timeout=LOCK_TIMEOUT_S, blocking=False):
                await self._scan_and_store()
                return True
        except ScanBusy:
            logger.info("threshold scan already running; request ignored")
            return False

    async def _scan_and_store(self) -> None:
        started = time.monotonic()
        status, error, payload = "ok", None, {}
        try:
            async with self._db.sessionmaker() as session:
                scan = await self._scan_window(session, self._days)
                report = self._analyze(scan, days=self._days, target_per_day=self._target)
                payload = report.to_payload()
        except Exception as exc:  # noqa: BLE001 - l'échec doit s'écrire, pas se taire
            logger.exception("threshold scan failed")
            status, error = "error", f"{type(exc).__name__}: {exc}"
        async with self._db.sessionmaker() as session:
            session.add(
                ThresholdReportRow(
                    time=datetime.now(tz=UTC),
                    window_days=self._days,
                    target_per_day=self._target,
                    status=status,
                    error=error,
                    duration_s=round(time.monotonic() - started, 2),
                    payload=payload,
                )
            )
            await session.commit()
```

Note pour l'implémenteur : `Cache.lock` de `cmi_common` prend `(name, timeout, blocking)` — **vérifier sa signature réelle** et, s'il ne lève pas quand le verrou est pris en `blocking=False`, adapter (par exemple en testant la valeur rendue par `acquire`) et **définir `ScanBusy` en conséquence**. Le contrat visé est : `run_once()` renvoie `False` sans exception quand un scan tourne déjà.

- [ ] **Step 3 : Câbler `decision-engine/app/main.py`** — READ le fichier (40 lignes), puis :
  1. Imports : `from cmi_common.cache import Cache`, `from cmi_common.db import Database`, `from cmi_common.events.control import ControlCommand, ControlCommandEvent`, `from .threshold_job import ThresholdScanJob`.
  2. Constantes : `THRESHOLD_SCAN_INTERVAL_H = float(os.getenv("THRESHOLD_SCAN_INTERVAL_H", "6"))`, `THRESHOLD_SCAN_DAYS = int(os.getenv("THRESHOLD_SCAN_DAYS", "7"))`, `THRESHOLD_SCAN_TARGET_PER_DAY = int(os.getenv("THRESHOLD_SCAN_TARGET_PER_DAY", "200"))`.
  3. Dans `_startup` : créer `Cache(settings.redis)` et `Database(settings.db)`, instancier le job, les poser sur `app.state`.
  4. **Boucle périodique** : une tâche asyncio qui dort `THRESHOLD_SCAN_INTERVAL_H * 3600` puis appelle `job.run_once()`, **désactivée si l'intervalle vaut 0** (ne pas créer la tâche du tout). Envelopper l'appel dans try/except pour qu'une exception ne tue pas la boucle.
  5. **Consumer de commandes** : un `EventConsumer` sur `[Topic.CONTROL]` avec `group_id=f"decision-engine-control-{os.getenv('HOSTNAME', 'local')}"` (groupe unique par réplique, comme trading-engine, pour que toutes reçoivent la commande — le verrou garantit qu'une seule travaille). Son handler filtre `isinstance(event, ControlCommandEvent) and event.command == ControlCommand.RUN_THRESHOLD_SCAN` puis appelle `job.run_once()`. **Vérifier le nom réel du membre `Topic`** pour `control.commands` et le nom du champ portant la commande dans `ControlCommandEvent`.
  6. `_shutdown` : arrêter le consumer de commandes, annuler la tâche périodique, fermer cache et db.

- [ ] **Step 4 : Vérifier** — `pytest tests/test_threshold_job.py -q` → PASS ; `pytest -q` complet ; ruff/black/mypy sur les fichiers touchés.

- [ ] **Step 5 : Commit**

```bash
git add services/decision-engine/app/threshold_job.py services/decision-engine/app/main.py tests/test_threshold_job.py
git commit -m "feat(decision-engine): job de scan du seuil, periodique et sur commande"
```

---

### Task 4 : La commande control-api

**Files:**
- Modify: `libs/cmi_common/cmi_common/events/control.py`
- Create: `services/control-api/app/routers/analysis.py`
- Modify: `services/control-api/app/main.py`
- Test: `tests/test_control_analysis_route.py`

- [ ] **Step 1 : Enum** — ajouter à `ControlCommand` : `RUN_THRESHOLD_SCAN = "run_threshold_scan"` (à la suite des membres existants).

- [ ] **Step 2 : Test qui échoue** — `tests/test_control_analysis_route.py` :

```python
"""La route de scan publie une commande, elle n'écrit rien elle-même."""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.events.control import ControlCommand

analysis = load_service_module("control-api", "routers.analysis")


class _Publisher:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, command, payload, *, issued_by=None):  # noqa: ANN001
        self.published.append((command, payload, issued_by))


async def test_scan_request_publishes_the_command() -> None:
    pub = _Publisher()
    svc = analysis.AnalysisService(pub)
    await svc.request_threshold_scan(issued_by="operator@example.com")
    assert len(pub.published) == 1
    command, _payload, issued_by = pub.published[0]
    assert command == ControlCommand.RUN_THRESHOLD_SCAN
    assert issued_by == "operator@example.com"
```

(Si `load_service_module` ne sait pas charger un sous-module `routers.analysis`, adapter l'appel — vérifier son implémentation dans `tests/service_modules.py` — ou placer `AnalysisService` de sorte qu'il soit chargeable ; le report en cas de blocage.)

- [ ] **Step 3 : Implémenter `services/control-api/app/routers/analysis.py`** — patron `routers/orders.py` :

```python
"""Demande de scan de calibration (publiée comme RUN_THRESHOLD_SCAN)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from cmi_common.auth import Principal
from cmi_common.events.control import ControlCommand

from ..auth_dep import require_principal


class AnalysisService:
    def __init__(self, publisher) -> None:
        self._pub = publisher

    async def request_threshold_scan(self, *, issued_by: str | None) -> None:
        await self._pub.publish(
            ControlCommand.RUN_THRESHOLD_SCAN, {}, issued_by=issued_by
        )


router = APIRouter(prefix="/analysis", tags=["analysis"])


def _svc(request: Request) -> AnalysisService:
    return request.app.state.analysis_service


@router.post("/threshold-scan")
async def request_threshold_scan(
    request: Request,
    principal: Principal = Depends(require_principal),
) -> dict:
    """Demande un scan. Le job décide s'il tourne : une demande pendant un scan
    en cours est ignorée côté decision-engine, ce n'est pas une erreur ici."""
    await _svc(request).request_threshold_scan(issued_by=principal.sub)
    return {"ok": True}
```

Dans `services/control-api/app/main.py` : `app.state.analysis_service = analysis_router.AnalysisService(publisher)` dans `_startup` (à côté des autres services) et `app.include_router(analysis_router.router)` en bas, avec l'import correspondant.

- [ ] **Step 4 : Vérifier** — `pytest tests/test_control_analysis_route.py -q` → PASS ; `pytest -q` ; ruff/black sur les fichiers touchés.

- [ ] **Step 5 : Commit**

```bash
git add libs/cmi_common/cmi_common/events/control.py services/control-api/app/routers/analysis.py services/control-api/app/main.py tests/test_control_analysis_route.py
git commit -m "feat(control-api): route de demande de scan du seuil"
```

---

### Task 5 : Lecture api-gateway

**Files:**
- Modify: `services/api-gateway/app/journal_api.py`, `services/api-gateway/app/read_contract.py`
- Modify: `tests/test_read_contract.py`

- [ ] **Step 1 : Contrat + test qui échoue** — dans `read_contract.py` :

```python
    "systems/journal/threshold": {"report", "status", "error", "computed_at",
                                  "window_days", "target_per_day", "duration_s",
                                  "running"},
```

Dans `tests/test_read_contract.py`, avant le méta-test :

```python
async def test_journal_threshold_contract() -> None:
    resp = await journal_api.journal_threshold(
        session=_FakeSession(1), cache=_FakeCache()
    )
    _assert_exact_keys("systems/journal/threshold", resp)
    # Aucun rapport en base : tout est null, jamais un rapport vide qui
    # passerait pour un scan réussi.
    assert resp["report"] is None and resp["computed_at"] is None
    assert resp["running"] is False
```

`_FakeCache` existe déjà dans ce fichier (ajouté pour `/market/regime`) — vérifier qu'il expose ce dont la route a besoin ; sinon lui ajouter la méthode manquante.

- [ ] **Step 2 : Implémenter la route** dans `journal_api.py` :

```python
@router.get("/systems/journal/threshold")
async def journal_threshold(
    session: AsyncSession = Depends(get_session_dep),
    cache: Cache = Depends(get_cache_dep),
) -> dict[str, Any]:
    """Dernier rapport de calibration, et si un scan tourne.

    `running` est lu depuis l'existence du verrou Redis plutôt que depuis un
    état persisté : le verrou est la seule source de vérité sur ce point, et
    un second état dériverait du premier au premier processus tué.
    """
    row = (
        (
            await session.execute(
                select(ThresholdReport).order_by(ThresholdReport.time.desc()).limit(1)
            )
        )
        .scalars()
        .first()
    )
    running = False
    try:
        running = bool(await cache.client.exists("lock:threshold-scan"))
    except Exception:
        logger.exception("threshold lock probe failed")
    return {
        "report": (row.payload or None) if row and row.status == "ok" else None,
        "status": row.status if row else None,
        "error": row.error if row else None,
        "computed_at": row.time.isoformat() if row else None,
        "window_days": row.window_days if row else None,
        "target_per_day": row.target_per_day if row else None,
        "duration_s": row.duration_s if row else None,
        "running": running,
    }
```

Imports à ajouter : `select` (déjà présent ?), `ThresholdReport` depuis `cmi_common.db`, `Cache` + `get_cache_dep` depuis `.regime_api` (la dépendance y est définie — **vérifier** ; si l'importer crée un cycle, la redéfinir localement à l'identique). **Vérifier le préfixe réel du verrou** posé par `Cache.lock` (`lock:` dans l'implémentation actuelle) et, s'il diffère, aligner la sonde — un préfixe faux rendrait `running` toujours `False`, ce qui est un mensonge silencieux.

- [ ] **Step 3 : Vérifier** — `pytest tests/test_read_contract.py -q` → PASS (méta-test inclus) ; `pytest -q` ; ruff/black.

- [ ] **Step 4 : Commit**

```bash
git add services/api-gateway/app/journal_api.py services/api-gateway/app/read_contract.py tests/test_read_contract.py
git commit -m "feat(api-gateway): endpoint de lecture du rapport de seuil"
```

---

### Task 6 : Frontend — types, endpoints, mocks

**Files:**
- Create: `frontend/src/lib/types/threshold.ts`, `frontend/src/lib/mock/threshold.ts`, `frontend/src/app/api/mock/systems/journal/threshold/route.ts`, `frontend/src/app/api/mock/analysis/threshold-scan/route.ts`
- Modify: `frontend/src/lib/api/endpoints.ts`

- [ ] **Step 1 : Types** — `frontend/src/lib/types/threshold.ts`, miroir exact de `ThresholdReport.to_payload()` et de la réponse de la route :

```ts
/** Contrat GET /systems/journal/threshold — miroir de
 *  decision-engine/app/threshold_scan.py::ThresholdReport.
 *  Règle du projet : null = non mesuré = rendu « — », jamais 0. */
export interface ThresholdAxis {
  key: string;
  weight: number;
  seen: number;
  pct: number;
  mute: boolean;
}

export interface ThresholdRefusal {
  code: 'MUTE_AXES' | 'NO_REGIME' | 'REGIME_GAP' | 'NO_DATA';
  title: string;
  /** Le paragraphe qui distingue « collecte cassée » d'« axe légitimement
   *  rare ». C'est la valeur du refus : il se rend en entier. */
  detail: string;
  suggested_days?: number;
}

export interface ThresholdProposal {
  threshold: number;
  target_per_day: number;
  actual_per_day: number;
  distinct_symbols: number;
  passing_pct: number;
}

export interface ThresholdReportPayload {
  window: { days: number; min_time: string | null; total: number; no_evidence: number; by_day: Record<string, number> };
  axes: ThresholdAxis[];
  refusal: ThresholdRefusal | null;
  distribution: Record<string, number | null>;
  proposal: ThresholdProposal | null;
  warnings: string[];
  sonnet: Record<string, number | null>;
}

export interface ThresholdReportResponse {
  report: ThresholdReportPayload | null;
  status: 'ok' | 'error' | null;
  error: string | null;
  computed_at: string | null;
  window_days: number | null;
  target_per_day: number | null;
  duration_s: number | null;
  running: boolean;
}
```

- [ ] **Step 2 : Endpoints** — dans `endpoints.ts`, ajouter au groupe `journalApi` :

```ts
  threshold: () =>
    api.get<ThresholdReportResponse>('/systems/journal/threshold').then((r) => r.data),
```

et un nouveau groupe **sur le client d'écriture** :

```ts
export const analysisApi = {
  requestThresholdScan: () =>
    control.post<{ ok: boolean }>('/analysis/threshold-scan').then((r) => r.data),
};
```

(Le `control` client, pas `api` : c'est une écriture.)

- [ ] **Step 3 : Mock** — `frontend/src/lib/mock/threshold.ts` avec `getThresholdReport()` déterministe (pas de `Math.random`) exerçant **le cas de refus** — c'est celui que l'UI doit bien rendre : `positioning` à `pct: 0.02, seen: 1, mute: true` sur un total de 1 281 511 (le cas réel du 4 août), `refusal.code = 'MUTE_AXES'` avec un `detail` de plusieurs lignes, `proposal: null`, deux `warnings`, `running: false`. Les deux routes mock miroir : `/api/mock/systems/journal/threshold` (GET) et `/api/mock/analysis/threshold-scan` (POST → `{ok: true}`).

- [ ] **Step 4 : Vérifier** — `cd frontend && npm run typecheck && npm run test:run && npm run build`.

- [ ] **Step 5 : Commit**

```bash
git add frontend/src/lib/types/threshold.ts frontend/src/lib/mock/threshold.ts frontend/src/app/api/mock/systems/journal/threshold frontend/src/app/api/mock/analysis frontend/src/lib/api/endpoints.ts
git commit -m "feat(frontend): types, endpoints et mock du rapport de seuil"
```

---

### Task 7 : Le panneau `/journal`

**Files:**
- Create: `frontend/src/components/journal/ThresholdReportPanel.tsx`
- Modify: `frontend/src/app/(app)/journal/page.tsx`
- Test: `frontend/src/components/journal/__tests__/ThresholdReportPanel.test.tsx`

- [ ] **Step 1 : Test qui échoue** — cinq états, ce sont eux le contrat visuel :

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ThresholdReportPanel } from '../ThresholdReportPanel';
import { getThresholdReport } from '@/lib/mock/threshold';

vi.mock('@/lib/api/endpoints', () => ({
  journalApi: { threshold: vi.fn() },
  analysisApi: { requestThresholdScan: vi.fn() },
}));

import { analysisApi, journalApi } from '@/lib/api/endpoints';

const thresholdGet = vi.mocked(journalApi.threshold);
const scanPost = vi.mocked(analysisApi.requestThresholdScan);

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ThresholdReportPanel />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('ThresholdReportPanel', () => {
  it('rend le refus en entier, avec son texte explicatif', async () => {
    thresholdGet.mockResolvedValue(getThresholdReport());
    renderPanel();
    expect(await screen.findByText(/positioning/)).toBeInTheDocument();
    // Le detail du refus doit etre rendu, pas resume
    expect(screen.getByText(/collecte/)).toBeInTheDocument();
    // Aucun seuil propose quand la garde refuse
    expect(screen.queryByTestId('threshold-proposal')).not.toBeInTheDocument();
  });

  it('affiche le compte brut à côté du pourcentage', async () => {
    thresholdGet.mockResolvedValue(getThresholdReport());
    renderPanel();
    // 1 ligne sur 1 281 511 : le pourcentage seul dirait « 0.0% »
    expect(await screen.findByText(/1 ligne|1 lignes|\(1\)/)).toBeInTheDocument();
  });

  it('état vide honnête quand aucun scan n’a tourné', async () => {
    thresholdGet.mockResolvedValue({
      report: null, status: null, error: null, computed_at: null,
      window_days: null, target_per_day: null, duration_s: null, running: false,
    });
    renderPanel();
    expect(await screen.findByText(/aucun scan/i)).toBeInTheDocument();
  });

  it('dit qu’un scan a échoué plutôt que d’afficher un rapport périmé', async () => {
    thresholdGet.mockResolvedValue({
      report: null, status: 'error', error: 'RuntimeError: stream died',
      computed_at: '2026-08-08T12:00:00+00:00', window_days: 7,
      target_per_day: 200, duration_s: 3.2, running: false,
    });
    renderPanel();
    expect(await screen.findByText(/échoué/i)).toBeInTheDocument();
    expect(screen.getByText(/stream died/)).toBeInTheDocument();
  });

  it('désactive le bouton et le dit pendant un scan', async () => {
    thresholdGet.mockResolvedValue({ ...getThresholdReport(), running: true });
    renderPanel();
    const button = await screen.findByRole('button', { name: /relancer/i });
    expect(button).toBeDisabled();
    expect(screen.getByText(/en cours/i)).toBeInTheDocument();
  });

  it('déclenche un scan au clic', async () => {
    thresholdGet.mockResolvedValue(getThresholdReport());
    scanPost.mockResolvedValue({ ok: true });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /relancer/i }));
    expect(scanPost).toHaveBeenCalledTimes(1);
  });
});
```

Run → FAIL.

- [ ] **Step 2 : Implémenter le panneau** — `'use client'`, `useQuery({ queryKey: ['journal','threshold'], queryFn: journalApi.threshold, refetchInterval: (q) => (q.state.data?.running ? 5_000 : 60_000) })` (poll rapide pendant un scan, lent sinon), `useMutation`-libre : un simple `onClick` async appelant `analysisApi.requestThresholdScan()` puis `queryClient.invalidateQueries({queryKey:['journal','threshold']})`.

Rendu, dans cet ordre : en-tête (âge via `fmtRelative`, bouton **Relancer** désactivé si `running`, mention « calcul en cours… ») → tableau de présence par axe (`key`, poids, `pct` avec **le compte brut à côté**, marqueur visuel si `mute`) → **verdict** : bloc de refus (`title` + `detail` en texte intégral, `white-space: pre-line`) ou `proposal` (`data-testid="threshold-proposal"`) → avertissements → répartition `by_day`. États : aucun rapport (« aucun scan encore effectué »), `status === 'error'` (« dernier scan échoué le … » + `error`), rapport de plus de 24 h → âge mis en évidence.

- [ ] **Step 3 : Monter dans la page** — dans `frontend/src/app/(app)/journal/page.tsx`, insérer `<SectionCard title="Rapport de scan — le seuil est-il calibrable ?"><ThresholdReportPanel /></SectionCard>` **au-dessus** de la grille existante (c'est la lecture qui précède la simulation).

- [ ] **Step 4 : Vérifier** — `npx vitest run src/components/journal` → tous verts ; `npm run test:run` complet ; `npm run typecheck` ; `npm run build`.

- [ ] **Step 5 : Commit**

```bash
git add frontend/src/components/journal/ThresholdReportPanel.tsx frontend/src/components/journal/__tests__/ThresholdReportPanel.test.tsx frontend/src/app/\(app\)/journal/page.tsx
git commit -m "feat(frontend): panneau du rapport de calibration sur /journal"
```

---

### Task 8 : Compose, vérification finale et docs

**Files:**
- Modify: `docker-compose.yml`, `docker-compose.vps.yml`, `README.md`, `CLAUDE.md`

- [ ] **Step 1 : Compose** — service `decision-engine` dans LES DEUX fichiers : ajouter au bloc `environment` `THRESHOLD_SCAN_INTERVAL_H: ${THRESHOLD_SCAN_INTERVAL_H:-6}`, `THRESHOLD_SCAN_DAYS: ${THRESHOLD_SCAN_DAYS:-7}`, `THRESHOLD_SCAN_TARGET_PER_DAY: ${THRESHOLD_SCAN_TARGET_PER_DAY:-200}`. **Vérifier que `decision-engine` a bien `redis` ET `postgres` dans son `depends_on`** — il gagne les deux dépendances dans cette vague ; si son `depends_on` local écrase la fusion d'ancre (le piège rencontré sur collector-kraken), re-lister explicitement `kafka`, `redis`, `postgres`.

- [ ] **Step 2 : Gates** — racine : `python -m pytest -q` (0 échec) ; `python -m ruff check libs services` et `python -m black --check libs services` (aucune NOUVELLE erreur vs baseline — comparer par `git stash` en cas de doute). Frontend : `npm run typecheck && npm run test:run && npm run build`. Compose : `docker compose -f docker-compose.yml config --quiet` et idem pour le fichier vps (avec les variables requises posées).

- [ ] **Step 3 : Revue null-vs-zéro** — sur `git diff <base>..HEAD` : chercher `?? 0`, `|| 0`, `or 0`, un `pct` arrondi qui masquerait un compte de 1, un `running` par défaut à `false` sur erreur de sonde (celui-là est assumé et commenté), un refus réduit à un booléen.

- [ ] **Step 4 : Docs** — README §5 : ligne decision-engine, mentionner le scan périodique de calibration. CLAUDE.md : un paragraphe court sous la section cockpit — le rapport de seuil est servi par decision-engine (seul service autorisé à importer `scoring.py`), persisté en `threshold_reports`, lu par api-gateway, déclenché par `control.commands`, et le verrou Redis sert d'état de job. Commit `docs: rapport de calibration du seuil dans le terminal`.

- [ ] **Step 5 : Récap final** — rappeler l'ordre de déploiement : `make migrate` (0020) puis déploiement manuel ; et qu'après le premier cycle (≤ 6 h) le panneau se remplit tout seul, ou immédiatement via le bouton.
