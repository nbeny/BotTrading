# Phase 1 — Instrumentation du pipeline (entonnoir) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendre observable *où* meurent les signaux dans la chaîne Haiku → decision-engine → risk-engine, et rendre le seuil d'escalade Haiku configurable — sans changer aucun comportement.

**Architecture:** Le scorer expose déjà `ambiguous` et les facteurs normalisés mais les jette. On les fait remonter dans l'`AnalysisEvent`, on les persiste sur `signals`, on persiste les refus (le `RiskRejectedEvent` existe déjà et transite déjà par l'api-gateway), et on agrège le tout dans un endpoint `/systems/funnel` lu par un panneau du Command Center.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2.0 async, Alembic, FastAPI, pytest, Next.js 14 + MUI + react-query.

**Spec:** `docs/superpowers/specs/2026-07-25-command-center-pipeline-kraken-events-design.md` (phases 1a + 1b)

---

## Contexte indispensable

Trois verrous en série bloquent aujourd'hui la production de décisions :

| Étage | Condition | Fichier |
|---|---|---|
| Haiku → Sonnet | `score >= 60` **et** (`ambiguous` ou `vol >= 0.6` ou `mom >= 0.6`) | `services/ai-worker-haiku/app/scorer.py:105` |
| decision-engine | `score >= 70` | `services/decision-engine/app/engine.py:53` |
| risk-engine | `score >= 70` **et** `confidence >= 0.55` | `services/risk-engine/app/rules.py:53,51` |

Ce plan **ne change aucune valeur de seuil**. Il rend l'entonnoir mesurable pour
que les valeurs soient choisies ensuite sur données réelles.

Ce qui existe déjà et qu'il ne faut PAS recréer :

- `RiskRejectedEvent` (`libs/cmi_common/cmi_common/events/risk.py:64`) — publié
  par le risk-engine sur le topic `decision.events`, déjà consommé par
  l'api-gateway (`services/api-gateway/app/main.py:23`).
- `DECISION_THRESHOLD` (`services/decision-engine/app/main.py:15`),
  `RISK_MIN_SCORE` / `RISK_MIN_CONFIDENCE` (`services/risk-engine/app/main.py:23-24`).

### Conventions du projet à respecter

- Les tests vivent à plat dans `tests/`, nommés `test_<sujet>.py`.
- Les services ne sont pas des packages installés : pour tester un module de
  service, on le charge via `importlib` (voir `tests/test_haiku_extract.py:12-25`).
- Les datetimes écrits en base sont **UTC naïfs** (`persister.py:27-33`).
- Tout ajout au plan de lecture doit entrer dans
  `services/api-gateway/app/read_contract.py`, sinon `tests/test_read_contract.py`
  échoue.
- Commandes : `make lint` (ruff + black --check + mypy), `make test` (pytest).

---

## Structure des fichiers

**Créés :**

| Fichier | Responsabilité |
|---|---|
| `migrations/alembic/versions/0008_signal_diagnostics.py` | colonnes de diagnostic sur `signals` + table `pipeline_rejections` |
| `services/api-gateway/app/funnel.py` | agrégation pure + requêtes de l'entonnoir |
| `tests/test_scorer_diagnostics.py` | table de vérité des trois verrous |
| `tests/test_funnel_aggregation.py` | agrégation de l'entonnoir (pur, sans DB) |
| `tests/test_rejection_persister.py` | persistance des refus |
| `frontend/src/components/command/FunnelPanel.tsx` | panneau entonnoir |

**Modifiés :**

| Fichier | Changement |
|---|---|
| `services/ai-worker-haiku/app/scorer.py` | `factors_present`, `liquidity_source`, `block_reason`, confiance découplée de l'ambiguïté |
| `libs/cmi_common/cmi_common/events/analysis.py` | champs `ambiguous`, `factors_present`, `block_reason` |
| `services/ai-worker-haiku/app/worker.py` | propage les nouveaux champs |
| `services/ai-worker-haiku/app/main.py` | `HAIKU_ESCALATE_SCORE` |
| `services/decision-engine/app/engine.py` | émet un `RiskRejectedEvent` au lieu d'un `return` nu |
| `libs/cmi_common/cmi_common/db/models.py` | colonnes `Signal` + modèle `PipelineRejection` |
| `services/api-gateway/app/persister.py` | persiste les nouveaux champs + les refus |
| `services/api-gateway/app/read_api.py` | route `/systems/funnel` |
| `services/api-gateway/app/read_contract.py` | contrat de `systems/funnel` |
| `frontend/src/lib/types/systems.ts` | type `FunnelStats` |
| `frontend/src/lib/api/endpoints.ts` | `systemsApi.funnel` |
| `frontend/src/app/(app)/command/page.tsx` | monte `FunnelPanel` |
| `docker-compose.vps.yml` | expose les 4 seuils |

---

## Task 0 : Réparer le chargement des modules de service dans les tests

**Prérequis dur.** `pytest tests/` échoue aujourd'hui à la *collecte* sur
`master` — aucun test ne s'exécute, donc `make test` est vert par accident
d'affichage seulement. Sans cette tâche, la tâche 11 est invérifiable et chaque
test ajouté aggrave la collision.

**Cause :** chaque service a un package nommé `app`. `tests/test_api_gateway_read.py`
s'exécute avant `tests/test_haiku_extract.py` (ordre alphabétique) et laisse
`sys.modules["app"]` pointant sur l'`app` de l'api-gateway. `test_haiku_extract.py`
charge ensuite `app.worker` du worker Haiku, dont l'import relatif
`from .features import FeatureStore` cherche `app.features` dans le mauvais
package → `ModuleNotFoundError`, collecte interrompue.

`tests/test_scorer.py:3-4` documente déjà la parade (charger sous un nom unique)
mais chaque fichier la réinvente ou l'oublie. On la centralise.

**Files:**
- Create: `tests/service_modules.py`
- Modify: `tests/test_haiku_extract.py`
- Modify: `tests/test_scorer_diagnostics.py` (créé en T1 avec le mauvais motif)

- [ ] **Step 1: Reproduce the failure**

Run: `python -m pytest tests/ -q`

Expected: `ERROR tests/test_haiku_extract.py` … `ModuleNotFoundError: No module
named 'app.features'` … `Interrupted: 1 error during collection`.

Note the exact error — Step 5 checks it is gone.

- [ ] **Step 2: Write the shared loader**

Create `tests/service_modules.py`:

```python
"""Load a service's ``app`` package under a unique top-level alias.

Every service in this repo ships a package literally named ``app``. Loading two
of them in one pytest session makes the second shadow the first in
``sys.modules``, so a relative import such as ``from .features import
FeatureStore`` resolves against the wrong service and raises ModuleNotFoundError
— which aborted collection of the whole suite.

Registering each service under a distinct alias (``haiku_app``, ``gateway_app``,
…) gives every module a parent package rooted at its own service directory, so
relative imports resolve within that service and nowhere else.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[1]


def load_service_module(service: str, module: str, alias: str) -> ModuleType:
    """Import ``services/<service>/app/<module>.py`` as ``<alias>.<module>``.

    ``alias`` must be unique per service across the whole test suite — that
    uniqueness is the entire point. The parent package is created with its
    search path pinned to the service's ``app/`` directory.
    """
    app_dir = _REPO_ROOT / "services" / service / "app"

    if alias not in sys.modules:
        pkg_spec = importlib.util.spec_from_file_location(
            alias,
            app_dir / "__init__.py",
            submodule_search_locations=[str(app_dir)],
        )
        assert pkg_spec and pkg_spec.loader
        pkg = importlib.util.module_from_spec(pkg_spec)
        sys.modules[alias] = pkg
        pkg_spec.loader.exec_module(pkg)

    qualified = f"{alias}.{module}"
    if qualified in sys.modules:
        return sys.modules[qualified]

    spec = importlib.util.spec_from_file_location(qualified, app_dir / f"{module}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Registered before exec so dataclasses and relative imports can resolve it.
    sys.modules[qualified] = mod
    spec.loader.exec_module(mod)
    return mod
```

- [ ] **Step 3: Migrate `tests/test_haiku_extract.py`**

Replace its entire importlib preamble (the block from `import importlib.util`
through `_spec.loader.exec_module(hw)`, roughly lines 5-25) with:

```python
from cmi_common.events.sentiment import SentimentEvent

from .service_modules import load_service_module

hw = load_service_module("ai-worker-haiku", "worker", "haiku_app")
```

If the relative import `from .service_modules import ...` fails because `tests/`
is not a package, use `from service_modules import load_service_module` instead —
pytest puts the test file's directory on `sys.path` under rootdir-based
collection. Verify which one works in Step 5 and use that form consistently in
every migrated file.

- [ ] **Step 4: Migrate `tests/test_scorer_diagnostics.py`**

Replace its importlib preamble with the same pattern:

```python
from service_modules import load_service_module

sc = load_service_module("ai-worker-haiku", "scorer", "haiku_app")
```

Leave the seven test functions untouched.

- [ ] **Step 5: Verify the whole suite now collects**

Run: `python -m pytest tests/ -q`

Expected: collection completes — no `Interrupted: N error during collection`.
Record the pass/fail counts. **Pre-existing failures may now become visible for
the first time; that is progress, not regression.** Report them, do not fix them
in this task.

Run: `python -m pytest tests/test_haiku_extract.py tests/test_scorer_diagnostics.py tests/test_scorer.py -v`

Expected: all pass (2 + 7 + 5 = 14).

- [ ] **Step 6: Commit**

```bash
git add tests/service_modules.py tests/test_haiku_extract.py tests/test_scorer_diagnostics.py
git commit -m "fix(tests): load service app packages under unique aliases

Every service ships a package named 'app'. Loading two in one pytest session
shadowed the first in sys.modules, so a relative import inside the second
resolved against the wrong service and aborted collection of the entire suite —
'make test' has been running zero tests.

Centralises the workaround that test_scorer.py already documented ad hoc.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Convention pour tous les tests suivants

**Ne jamais charger un module de service sous un nom commençant par `app.`.**
Utiliser systématiquement :

```python
from service_modules import load_service_module

mod = load_service_module("<service-dir>", "<module>", "<alias>")
```

Alias attribués : `haiku_app` (ai-worker-haiku), `gateway_app` (api-gateway),
`decision_app` (decision-engine), `risk_app` (risk-engine).

---

## Task 1 : Le scorer expose ses diagnostics

Le scorer calcule déjà `ambiguous` et les facteurs, mais ne dit pas *pourquoi* il
n'escalade pas, ni combien de facteurs étaient réellement disponibles.

> **Réalisé en deux commits.** `1e61a00` implémente le plan ci-dessous ; `c3dc14f`
> corrige deux défauts que la revue qualité a trouvés et que j'ai reproduits :
>
> 1. **La formule de confiance ci-dessous est erronée** — `0.3 + 0.4·liq_f +
>    0.15·(fp/4)` récompense l'absence de données (dict vide → 0.50 contre 0.45
>    pour quatre facteurs avec liquidité faible), parce que la substitution
>    neutre de 0.5 pour une liquidité inconnue rapporte 0.20 quand le terme de
>    couverture plafonne à 0.15. Elle est aussi bornée à [0.30, 0.85], rendant
>    son `_clamp` inatteignable. Remplacée par
>    `round(0.25 + 0.35 * liq_f + 0.4 * (factors_present / _N_FACTORS), 2)`,
>    monotone et bornée à [0.25, 1.00], sans clamp.
> 2. **`liquidity_usd == 0.0` est le chemin normal, pas un cas limite** —
>    `worker.py:83` envoie `float(event.liquidity_usd or 0)`, donc une liquidité
>    absente arrive à `0.0`. `thin_liq_big_move` la lisait comme « liquidité
>    ténue » et escaladait vers le LLM payant un symbole sans information.
>    Corrigé par des prédicats `has_chg`/`has_vol`/`has_sent`/`has_liq` employés
>    par `factors_present`, `liquidity_source`, la normalisation,
>    `thin_liq_big_move` et la chaîne `reason`. **Changement de comportement
>    assumé**, couvert par un test.
>
> `block_reason` prend aussi `"unknown"` comme défaut de dataclass (et non
> `"escalated"`), ce que les tâches 2 et 5 répercutent.

**Files:**
- Modify: `services/ai-worker-haiku/app/scorer.py`
- Test: `tests/test_scorer_diagnostics.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scorer_diagnostics.py`:

```python
"""Truth table for the Haiku triage scorer's diagnostic outputs.

The scorer decides whether a symbol reaches the senior (Sonnet) analyst. Before
this test existed, a signal could be dropped for three different reasons and the
pipeline reported none of them. Each case below pins one reason.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parents[1] / "services" / "ai-worker-haiku"
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

_spec = importlib.util.spec_from_file_location(
    "app.scorer", _APP_ROOT / "app" / "scorer.py"
)
sc = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = sc
_spec.loader.exec_module(sc)


def test_typical_major_pair_is_blocked_by_score() -> None:
    """The real-world case: BTC +3%, mild sentiment, no volume spike.

    Scores ~22 against an escalation floor of 60. This is why the pipeline
    produced no decisions at all.
    """
    r = sc.local_opportunity(
        {"price_change_pct_24h": 3.0, "sentiment_score": 0.3}
    )
    assert r.opportunity_score < 60
    assert r.escalate is False
    assert r.block_reason == "score_below_threshold"


def test_strong_score_but_calm_move_is_blocked_by_gate() -> None:
    """Score clears 60 but the setup is unanimous and liquid: no LLM needed."""
    r = sc.local_opportunity(
        {
            "price_change_pct_24h": 8.0,
            "volume_spike_ratio": 2.0,
            "sentiment_score": 0.9,
            "liquidity_usd": 900_000.0,
        }
    )
    assert r.opportunity_score >= 60
    assert r.ambiguous is False
    assert r.escalate is False
    assert r.block_reason == "gate_not_met"


def test_escalated_signal_reports_escalated() -> None:
    """Strong and high-momentum: crosses both the score and the gate."""
    r = sc.local_opportunity(
        {
            "price_change_pct_24h": 14.0,
            "volume_spike_ratio": 4.0,
            "sentiment_score": 0.9,
            "liquidity_usd": 900_000.0,
        }
    )
    assert r.escalate is True
    assert r.block_reason == "escalated"


def test_factors_present_counts_only_supplied_factors() -> None:
    r = sc.local_opportunity(
        {"price_change_pct_24h": 3.0, "sentiment_score": 0.3}
    )
    assert r.factors_present == 2

    full = sc.local_opportunity(
        {
            "price_change_pct_24h": 3.0,
            "volume_spike_ratio": 1.5,
            "sentiment_score": 0.3,
            "liquidity_usd": 500_000.0,
        }
    )
    assert full.factors_present == 4


def test_liquidity_source_is_unknown_when_absent() -> None:
    r = sc.local_opportunity({"price_change_pct_24h": 3.0, "sentiment_score": 0.3})
    assert r.liquidity_source == "unknown"


def test_liquidity_source_is_dex_when_supplied() -> None:
    r = sc.local_opportunity(
        {"price_change_pct_24h": 3.0, "sentiment_score": 0.3, "liquidity_usd": 500_000.0}
    )
    assert r.liquidity_source == "dex"


def test_ambiguity_no_longer_drags_confidence_under_the_risk_floor() -> None:
    """Regression guard for the contradiction found during design.

    The scorer escalates *ambiguous* setups to Sonnet, but the old confidence
    formula (0.3 + 0.4*liq + 0.3 if not ambiguous) gave exactly 0.50 to an
    ambiguous signal with unknown liquidity — below the risk engine's 0.55
    floor. Ambiguity is a reason to escalate, not a reason to distrust the data.
    """
    r = sc.local_opportunity(
        {"price_change_pct_24h": 5.0, "sentiment_score": -0.5}
    )
    assert r.ambiguous is True
    assert r.confidence >= 0.55
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scorer_diagnostics.py -v`

Expected: FAIL — `AttributeError: 'ScoreResult' object has no attribute 'block_reason'`.

- [ ] **Step 3: Implement the diagnostics in the scorer**

In `services/ai-worker-haiku/app/scorer.py`, replace the `ScoreResult` dataclass
(lines 36-43) with:

```python
@dataclass(frozen=True, slots=True)
class ScoreResult:
    opportunity_score: int          # 0-100
    confidence: float               # 0-1
    reason: str
    escalate: bool                  # worth a senior (LLM) look?
    ambiguous: bool
    # Diagnostics — why a signal did or did not reach the senior analyst, and
    # how much evidence the score was actually computed from. A score built on
    # 2 of 4 factors is not comparable to one built on 4 of 4; the funnel needs
    # to tell them apart before any threshold is tuned.
    block_reason: str = "escalated"  # escalated | score_below_threshold | gate_not_met
    factors_present: int = 0         # 0-4
    liquidity_source: str = "unknown"  # dex | volume_proxy | unknown
    factors: dict[str, float] = field(default_factory=dict)
```

Then replace the body of `local_opportunity` from the confidence line (line 88)
through the `return` (lines 88-119) with:

```python
    # Confidence measures how much we trust the *data*, not how clean the setup
    # looks. Ambiguity used to subtract 0.3 here, which pushed ambiguous signals
    # (exactly the ones we escalate) below the risk engine's confidence floor.
    confidence = round(_clamp(0.3 + 0.4 * liq_f + 0.15 * (factors_present / 4), 0.0, 1.0), 2)

    bits: list[str] = []
    if chg is not None:
        bits.append(f"24h {chg:+.1f}%")
    if vol_spike:
        bits.append(f"vol x{vol_spike:.1f}")
    if sent is not None:
        bits.append(f"sent {sent:+.2f}")
    if liq is not None:
        bits.append(f"liq ${liq:,.0f}")
    if disagreement:
        bits.append("price/sentiment disagree")
    reason = "deterministic triage — " + (", ".join(bits) or "insufficient signal")

    # Escalate only strong setups that are also ambiguous or high-conviction —
    # a calm, unanimous move needs no LLM.
    strong = score >= cfg.escalate_score
    gate = ambiguous or vol >= 0.6 or mom >= 0.6
    escalate = strong and gate
    if escalate:
        block_reason = "escalated"
    elif not strong:
        block_reason = "score_below_threshold"
    else:
        block_reason = "gate_not_met"

    return ScoreResult(
        opportunity_score=score,
        confidence=confidence,
        reason=reason,
        escalate=escalate,
        ambiguous=ambiguous,
        block_reason=block_reason,
        factors_present=factors_present,
        liquidity_source=liquidity_source,
        factors={
            "momentum": round(mom, 3),
            "volume": round(vol, 3),
            "sentiment": round(sent_mag, 3),
            "liquidity": round(liq_f, 3),
        },
    )
```

Insert the two new derived values just after the four raw reads (after line 56,
`liq = f.get("liquidity_usd")`):

```python
    # How many of the four factors were actually supplied. Absent factors are
    # normalized to a neutral value, so the score alone hides thin evidence.
    factors_present = sum(
        1 for v in (chg, vol_spike, sent, liq) if v is not None
    )
    liquidity_source = "dex" if liq else "unknown"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scorer_diagnostics.py -v`

Expected: PASS, 7 passed.

Then confirm nothing else regressed:

Run: `python -m pytest tests/ -q -k "haiku or scorer"`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/ai-worker-haiku/app/scorer.py tests/test_scorer_diagnostics.py
git commit -m "feat(haiku): expose triage diagnostics; decouple confidence from ambiguity

The scorer computed ambiguity and factor coverage then discarded both, so a
dropped signal reported no reason. Adds block_reason, factors_present and
liquidity_source.

Confidence no longer subtracts 0.3 for ambiguity: that put ambiguous signals
(precisely the ones escalated to Sonnet) at 0.50, under the risk engine's 0.55
floor. Confidence now measures data quality only.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2 : Porter les diagnostics dans l'AnalysisEvent

**Files:**
- Modify: `libs/cmi_common/cmi_common/events/analysis.py`
- Modify: `services/ai-worker-haiku/app/worker.py:106-120`
- Test: `tests/test_analysis_diagnostics_event.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_analysis_diagnostics_event.py`:

```python
"""AnalysisEvent carries the triage diagnostics downstream."""

from __future__ import annotations

from cmi_common.events import AnalysisEvent


def test_defaults_are_backwards_compatible() -> None:
    """Existing producers that omit the new fields must still validate, and an
    unset block_reason must not read as "this reached the senior analyst"."""
    ev = AnalysisEvent(
        symbol="BTC", opportunity_score=22, confidence=0.6, reason="r"
    )
    assert ev.ambiguous is False
    assert ev.factors_present == 0
    assert ev.block_reason == "unknown"


def test_diagnostics_round_trip_through_json() -> None:
    ev = AnalysisEvent(
        symbol="BTC",
        opportunity_score=22,
        confidence=0.6,
        reason="r",
        ambiguous=True,
        factors_present=2,
        block_reason="score_below_threshold",
        liquidity_source="volume_proxy",
    )
    restored = AnalysisEvent.model_validate(ev.model_dump(mode="json"))
    assert restored.block_reason == "score_below_threshold"
    assert restored.factors_present == 2
    assert restored.liquidity_source == "volume_proxy"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_analysis_diagnostics_event.py -v`

Expected: FAIL — `AttributeError: 'AnalysisEvent' object has no attribute 'ambiguous'`.

- [ ] **Step 3: Add the fields**

In `libs/cmi_common/cmi_common/events/analysis.py`, inside `AnalysisEvent`, add
immediately after the `escalate: bool = False` line:

```python
    # Triage diagnostics — carried so the pipeline funnel can report *where* a
    # signal stopped without re-deriving it. Defaults keep older producers valid;
    # "unknown" rather than "escalated" so an event from a producer that does not
    # set it is never mistaken for one that reached the senior analyst.
    ambiguous: bool = False
    block_reason: str = "unknown"
    factors_present: int = Field(default=0, ge=0, le=4)
    liquidity_source: str = "unknown"
```

In `services/ai-worker-haiku/app/worker.py`, in `_score`, add the four fields to
the `AnalysisEvent(...)` construction, just after `escalate=r.escalate,`:

```python
            ambiguous=r.ambiguous,
            block_reason=r.block_reason,
            factors_present=r.factors_present,
            liquidity_source=r.liquidity_source,
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_analysis_diagnostics_event.py tests/test_events.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/events/analysis.py services/ai-worker-haiku/app/worker.py tests/test_analysis_diagnostics_event.py
git commit -m "feat(events): carry triage diagnostics on AnalysisEvent

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3 : Le seuil d'escalade Haiku devient configurable

`main.py:22` construit `HaikuWorker(FeatureStore(cache), producer)` sans
`scorer_config`, donc `escalate_score=60` est figé dans le code.

**Files:**
- Modify: `services/ai-worker-haiku/app/main.py`
- Test: `tests/test_haiku_scorer_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_haiku_scorer_config.py`:

```python
"""HAIKU_ESCALATE_SCORE overrides the hardcoded escalation floor."""

from __future__ import annotations

from service_modules import load_service_module

hm = load_service_module("ai-worker-haiku", "main", "haiku_app")


def test_default_preserves_current_behaviour(monkeypatch) -> None:
    monkeypatch.delenv("HAIKU_ESCALATE_SCORE", raising=False)
    assert hm.scorer_config_from_env().escalate_score == 60


def test_env_override_is_applied(monkeypatch) -> None:
    monkeypatch.setenv("HAIKU_ESCALATE_SCORE", "35")
    assert hm.scorer_config_from_env().escalate_score == 35
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_haiku_scorer_config.py -v`

Expected: FAIL — `AttributeError: module 'app.main_cfg' has no attribute 'scorer_config_from_env'`.

- [ ] **Step 3: Implement**

In `services/ai-worker-haiku/app/main.py`, add `import os` after `import asyncio`,
add the import of `ScorerConfig` next to the existing service imports:

```python
from .features import FeatureStore
from .scorer import ScorerConfig
from .worker import HaikuWorker


def scorer_config_from_env() -> ScorerConfig:
    """Escalation floor is operator-tunable: it is the only lever that decides
    how much traffic reaches the (paid) senior analyst. Default matches the
    previous hardcoded value, so enabling this changes nothing on its own."""
    return ScorerConfig(escalate_score=int(os.getenv("HAIKU_ESCALATE_SCORE", "60")))
```

Then replace line 22:

```python
    worker = HaikuWorker(FeatureStore(cache), producer, scorer_config=scorer_config_from_env())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_haiku_scorer_config.py -v`

Expected: PASS, 2 passed.

- [ ] **Step 5: Commit**

```bash
git add services/ai-worker-haiku/app/main.py tests/test_haiku_scorer_config.py
git commit -m "feat(haiku): HAIKU_ESCALATE_SCORE env override (default unchanged at 60)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4 : Le decision-engine cesse d'abandonner silencieusement

`engine.py:53-54` fait un `return` nu sous le seuil. C'est le seul étage dont les
refus ne laissent aucune trace.

**Files:**
- Modify: `services/decision-engine/app/engine.py`
- Test: `tests/test_decision_engine_rejection.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_decision_engine_rejection.py`:

```python
"""Below-threshold analyses emit an auditable rejection instead of vanishing."""

from __future__ import annotations

import pytest

from cmi_common.events import AnalysisEvent
from cmi_common.events.base import Source
from cmi_common.kafka import Topic

from service_modules import load_service_module

de = load_service_module("decision-engine", "engine", "decision_app")


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    async def publish(self, topic, event) -> None:
        self.published.append((topic, event))


@pytest.mark.asyncio
async def test_low_score_publishes_a_rejection() -> None:
    producer = FakeProducer()
    engine = de.DecisionEngine(producer, decision_threshold=70)
    await engine.handle(
        AnalysisEvent(symbol="BTC", opportunity_score=22, confidence=0.6, reason="r")
    )
    assert len(producer.published) == 1
    topic, event = producer.published[0]
    assert topic is Topic.DECISION
    assert event.event_type == "RiskRejectedEvent"
    assert event.symbol == "BTC"
    assert "below decision threshold" in event.reason
    assert event.source is Source.DECISION_ENGINE


@pytest.mark.asyncio
async def test_high_score_still_publishes_a_decision() -> None:
    producer = FakeProducer()
    engine = de.DecisionEngine(producer, decision_threshold=10)
    await engine.handle(
        AnalysisEvent(
            symbol="BTC",
            opportunity_score=90,
            confidence=0.9,
            reason="r",
            price_change_pct_24h=14.0,
            volume_spike_ratio=4.0,
            sentiment_score=0.9,
        )
    )
    assert len(producer.published) == 1
    _topic, event = producer.published[0]
    assert event.event_type == "DecisionEvent"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_decision_engine_rejection.py -v`

Expected: FAIL on the first test — `assert len([]) == 1`, nothing is published.

- [ ] **Step 3: Implement**

In `services/decision-engine/app/engine.py`, add to the imports:

```python
from cmi_common.events.risk import RiskRejectedEvent
```

Replace lines 53-54:

```python
        if result.opportunity_score < self._threshold:
            return
```

with:

```python
        if result.opportunity_score < self._threshold:
            # Reuse the risk engine's audit event rather than inventing a second
            # one of identical shape. Without this the deterministic path was the
            # only stage whose rejections left no trace at all.
            rejected = RiskRejectedEvent(
                source=Source.DECISION_ENGINE,
                correlation_id=event.correlation_id,
                symbol=event.symbol,
                reason=(
                    f"score {result.opportunity_score} below decision threshold "
                    f"{self._threshold}"
                ),
                decision_event_id=event.event_id,
            )
            await self._producer.publish(Topic.DECISION, rejected)
            EVENTS_PRODUCED.labels(
                SERVICE, Topic.DECISION.value, rejected.event_type
            ).inc()
            return
```

`Source` is already imported at line 14 (`from cmi_common.events.base import Source`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_decision_engine_rejection.py -v`

Expected: PASS, 2 passed.

- [ ] **Step 5: Commit**

```bash
git add services/decision-engine/app/engine.py tests/test_decision_engine_rejection.py
git commit -m "feat(decision-engine): emit RiskRejectedEvent below threshold

The deterministic path dropped sub-threshold analyses with a bare return, so it
was the one stage whose rejections were invisible to the funnel.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5 : Migration — diagnostics et table des refus

**Files:**
- Create: `migrations/alembic/versions/0008_signal_diagnostics.py`
- Modify: `libs/cmi_common/cmi_common/db/models.py`

- [ ] **Step 1: Write the migration**

Create `migrations/alembic/versions/0008_signal_diagnostics.py`:

```python
"""signal diagnostics columns + pipeline_rejections

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive only: server defaults keep rows written by an older api-gateway
    # valid during a rolling deploy.
    op.add_column(
        "signals",
        sa.Column("ambiguous", sa.Boolean, server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "signals",
        sa.Column(
            "block_reason", sa.String(32), server_default="unknown", nullable=False
        ),
    )
    op.add_column(
        "signals",
        sa.Column("factors_present", sa.Integer, server_default="0", nullable=False),
    )
    op.create_index("ix_signals_block_reason", "signals", ["block_reason"])

    op.create_table(
        "pipeline_rejections",
        sa.Column("time", sa.DateTime, primary_key=True),
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
    )
    op.create_index(
        "ix_pipeline_rejections_stage_time", "pipeline_rejections", ["stage", "time"]
    )
    op.execute(
        "SELECT create_hypertable('pipeline_rejections', 'time', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )


def downgrade() -> None:
    op.drop_table("pipeline_rejections")
    op.drop_index("ix_signals_block_reason", table_name="signals")
    op.drop_column("signals", "factors_present")
    op.drop_column("signals", "block_reason")
    op.drop_column("signals", "ambiguous")
```

- [ ] **Step 2: Add the ORM models**

In `libs/cmi_common/cmi_common/db/models.py`, add three columns to `Signal`
(after `escalated`):

```python
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reason: Mapped[str] = mapped_column(String(32), default="unknown")
    factors_present: Mapped[int] = mapped_column(Integer, default=0)
```

Then add a new model right after the `Signal` class:

```python
class PipelineRejection(Base):
    """Where a signal died, per stage -> hypertable on ``time``.

    The funnel answers "why do I get no decisions?" and that question is only
    answerable if every stage records its refusals. Sourced from
    RiskRejectedEvent, which both the decision engine and the risk engine emit.
    """

    __tablename__ = "pipeline_rejections"

    time: Mapped[datetime] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stage: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32))
    correlation_id: Mapped[str | None] = mapped_column(String(64), default=None)
    reason: Mapped[str] = mapped_column(Text)
```

Export it in `libs/cmi_common/cmi_common/db/__init__.py`: add
`PipelineRejection` to the import list from `.models` and to `__all__`.

- [ ] **Step 3: Verify the migration applies**

Run: `make migrate`

Expected: `Running upgrade 0007 -> 0008, signal diagnostics columns + pipeline_rejections`.

Then verify the table is a hypertable:

Run: `docker compose exec postgres psql -U cmi -d cmi -c "SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_name = 'pipeline_rejections';"`

Expected: one row, `pipeline_rejections`.

- [ ] **Step 4: Commit**

```bash
git add migrations/alembic/versions/0008_signal_diagnostics.py libs/cmi_common/cmi_common/db/models.py libs/cmi_common/cmi_common/db/__init__.py
git commit -m "feat(db): signal diagnostics columns + pipeline_rejections hypertable

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6 : Le persister écrit diagnostics et refus

L'api-gateway consomme déjà `Topic.DECISION`, donc les `RiskRejectedEvent` des
deux étages y arrivent déjà — il les ignore silencieusement.

**Files:**
- Modify: `services/api-gateway/app/persister.py`
- Test: `tests/test_rejection_persister.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_rejection_persister.py`:

```python
"""Persister routes RiskRejectedEvent into pipeline_rejections, stage-tagged."""

from __future__ import annotations

import pytest

from cmi_common.events.base import Source
from cmi_common.events.risk import RiskRejectedEvent

from service_modules import load_service_module

persister_mod = load_service_module("api-gateway", "persister", "gateway_app")


def test_stage_from_source_maps_both_producers() -> None:
    assert persister_mod.stage_for(Source.DECISION_ENGINE) == "decision_engine"
    assert persister_mod.stage_for(Source.RISK_ENGINE) == "risk_engine"


def test_unknown_source_falls_back_to_its_value() -> None:
    """An unmapped producer must stay visible rather than be silently dropped."""
    assert persister_mod.stage_for(Source.AI_HAIKU) == Source.AI_HAIKU.value


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


@pytest.mark.asyncio
async def test_rejection_event_is_persisted() -> None:
    session = FakeSession()
    p = persister_mod.Persister(FakeDb(session))
    await p.handle(
        RiskRejectedEvent(
            source=Source.RISK_ENGINE,
            symbol="BTC",
            reason="score 22 below floor",
        )
    )
    assert session.committed is True
    assert len(session.executed) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rejection_persister.py -v`

Expected: FAIL — `AttributeError: module 'app.persister' has no attribute 'stage_for'`.

- [ ] **Step 3: Implement**

In `services/api-gateway/app/persister.py`, extend the imports:

```python
from cmi_common.db import Database, Decision, PipelineRejection, Price, Signal, Trade
from cmi_common.events import (
    AnalysisEvent,
    BaseEvent,
    DecisionEvent,
    PriceEvent,
    RiskApprovedEvent,
)
from cmi_common.events.base import Source
from cmi_common.events.risk import RiskRejectedEvent
```

Add the pure mapping helper just below `_naive_utc`:

```python
_STAGE_BY_SOURCE = {
    Source.DECISION_ENGINE: "decision_engine",
    Source.RISK_ENGINE: "risk_engine",
}


def stage_for(source: Source) -> str:
    """Which pipeline stage refused. Unmapped producers keep their raw source
    name rather than being dropped — an unexpected rejector must stay visible."""
    return _STAGE_BY_SOURCE.get(source, source.value)
```

In `Persister.handle`, add a branch **before** the `DecisionEvent` branch —
ordering matters, both travel on `decision.events`:

```python
        elif isinstance(event, RiskRejectedEvent):
            await self._save_rejection(event)
```

Add the method:

```python
    async def _save_rejection(self, e: RiskRejectedEvent) -> None:
        EVENTS_CONSUMED.labels(SERVICE, Topic.DECISION.value, e.event_type).inc()
        async with self._db._sessionmaker() as s:  # noqa: SLF001
            stmt = insert(PipelineRejection).values(
                time=_naive_utc(e.occurred_at),
                event_id=e.event_id,
                stage=stage_for(e.source),
                symbol=e.symbol,
                correlation_id=e.correlation_id,
                reason=e.reason,
            ).on_conflict_do_nothing()
            await s.execute(stmt)
            await s.commit()
```

Extend `_save_signal` to persist the diagnostics — add these three lines inside
the `insert(Signal).values(...)` call, after `escalated=e.escalate,`:

```python
                ambiguous=e.ambiguous,
                block_reason=e.block_reason,
                factors_present=e.factors_present,
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_rejection_persister.py tests/test_execution_persister.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/persister.py tests/test_rejection_persister.py
git commit -m "feat(api-gateway): persist triage diagnostics and pipeline rejections

RiskRejectedEvent already reached the persister on decision.events and was
silently ignored. Routes it to pipeline_rejections, tagged by producing stage.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7 : Agrégation de l'entonnoir (pure, testable sans DB)

**Files:**
- Create: `services/api-gateway/app/funnel.py`
- Test: `tests/test_funnel_aggregation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_funnel_aggregation.py`:

```python
"""Funnel aggregation: pure shaping of already-queried counts."""

from __future__ import annotations

from service_modules import load_service_module

funnel = load_service_module("api-gateway", "funnel", "gateway_app")


def test_stages_are_ordered_and_complete() -> None:
    out = funnel.build_funnel(
        analyses=1000,
        escalated=0,
        decisions=0,
        approved=0,
        executed=0,
        score_buckets={0: 400, 10: 500, 20: 100},
        block_reasons=[("haiku", "score_below_threshold", 980), ("haiku", "gate_not_met", 20)],
        factors_presence={2: 900, 3: 100},
    )
    assert [s["stage"] for s in out["stages"]] == [
        "analyses", "escalated", "decisions", "approved", "executed",
    ]
    assert out["stages"][0]["count"] == 1000


def test_conversion_pct_is_relative_to_previous_stage() -> None:
    out = funnel.build_funnel(
        analyses=1000, escalated=100, decisions=50, approved=25, executed=5,
        score_buckets={}, block_reasons=[], factors_presence={},
    )
    by = {s["stage"]: s for s in out["stages"]}
    assert by["escalated"]["conversion_pct"] == 10.0
    assert by["decisions"]["conversion_pct"] == 50.0
    assert by["executed"]["conversion_pct"] == 20.0


def test_zero_upstream_does_not_divide_by_zero() -> None:
    """The current production state: nothing flows at all."""
    out = funnel.build_funnel(
        analyses=0, escalated=0, decisions=0, approved=0, executed=0,
        score_buckets={}, block_reasons=[], factors_presence={},
    )
    assert all(s["conversion_pct"] == 0.0 for s in out["stages"])


def test_score_histogram_fills_missing_buckets() -> None:
    """A sparse GROUP BY must still render a full 0-100 histogram."""
    out = funnel.build_funnel(
        analyses=10, escalated=0, decisions=0, approved=0, executed=0,
        score_buckets={20: 7, 50: 3}, block_reasons=[], factors_presence={},
    )
    assert len(out["score_histogram"]) == 10
    assert out["score_histogram"][2] == {"bucket": 20, "count": 7}
    assert out["score_histogram"][0] == {"bucket": 0, "count": 0}


def test_factors_presence_covers_zero_to_four() -> None:
    out = funnel.build_funnel(
        analyses=10, escalated=0, decisions=0, approved=0, executed=0,
        score_buckets={}, block_reasons=[], factors_presence={2: 10},
    )
    assert out["factors_presence"] == {"0": 0, "1": 0, "2": 10, "3": 0, "4": 0}


def test_block_reasons_are_sorted_by_count_desc() -> None:
    out = funnel.build_funnel(
        analyses=10, escalated=0, decisions=0, approved=0, executed=0,
        score_buckets={},
        block_reasons=[("haiku", "gate_not_met", 20), ("haiku", "score_below_threshold", 980)],
        factors_presence={},
    )
    assert out["top_block_reasons"][0]["count"] == 980
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_funnel_aggregation.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.funnel'`.

- [ ] **Step 3: Implement**

Create `services/api-gateway/app/funnel.py`:

```python
"""Pipeline funnel: how many signals survive each stage, and why the rest don't.

Answers the operational question "why do I get no decisions?". The shaping below
is pure so it can be unit-tested without a database; the queries live in
``read_api.funnel``.
"""

from __future__ import annotations

from datetime import datetime, timezone

STAGE_ORDER = ["analyses", "escalated", "decisions", "approved", "executed"]
SCORE_BUCKET_WIDTH = 10


def _pct(numerator: int, denominator: int) -> float:
    """Conversion relative to the previous stage. A zero upstream is reported as
    0.0 rather than as an error: it is the normal state of a stalled pipeline."""
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100, 2)


def build_funnel(
    *,
    analyses: int,
    escalated: int,
    decisions: int,
    approved: int,
    executed: int,
    score_buckets: dict[int, int],
    block_reasons: list[tuple[str, str, int]],
    factors_presence: dict[int, int],
    window: str = "24h",
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(tz=timezone.utc)
    counts = [analyses, escalated, decisions, approved, executed]
    stages = [
        {
            "stage": name,
            "count": count,
            # First stage has no upstream, so it converts from itself: 100% when
            # non-empty, 0% when the pipeline is idle.
            "conversion_pct": _pct(count, counts[i - 1] if i else count),
        }
        for i, (name, count) in enumerate(zip(STAGE_ORDER, counts))
    ]
    return {
        "window": window,
        "stages": stages,
        "score_histogram": [
            {"bucket": b, "count": score_buckets.get(b, 0)}
            for b in range(0, 100, SCORE_BUCKET_WIDTH)
        ],
        "factors_presence": {str(k): factors_presence.get(k, 0) for k in range(5)},
        "top_block_reasons": [
            {"stage": stage, "reason": reason, "count": count}
            for stage, reason, count in sorted(
                block_reasons, key=lambda r: r[2], reverse=True
            )
        ],
        "updated_at": now.isoformat(),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_funnel_aggregation.py -v`

Expected: PASS, 6 passed.

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/funnel.py tests/test_funnel_aggregation.py
git commit -m "feat(api-gateway): pure funnel aggregation

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8 : Route `/systems/funnel`

**Files:**
- Modify: `services/api-gateway/app/read_api.py`
- Modify: `services/api-gateway/app/read_contract.py`

- [ ] **Step 1: Add the contract entry (this makes the parity test fail first)**

In `services/api-gateway/app/read_contract.py`, add inside `CONTRACT`, after the
`systems/overview` entry:

```python
    "systems/funnel": {
        "window", "stages", "score_histogram", "factors_presence",
        "top_block_reasons", "updated_at",
    },
```

- [ ] **Step 2: Run the parity test to verify it fails**

Run: `python -m pytest tests/test_read_contract.py -v`

Expected: FAIL — the contract declares `systems/funnel` but no route serves it.

- [ ] **Step 3: Implement the route**

In `services/api-gateway/app/read_api.py`, add to the imports:

```python
from cmi_common.db.models import PipelineRejection
from .funnel import SCORE_BUCKET_WIDTH, build_funnel
```

(`RawContent` is already imported from `cmi_common.db.models` on line 30 — extend
that line rather than adding a second import of the same module.)

`_utcnow_naive` (line 102), `timedelta`, `and_`, `func`, `select`, `Signal`,
`Decision` and `Trade` are all already imported in this file; do not re-import
them.

Add the route at the end of the file:

```python
@router.get("/systems/funnel")
async def systems_funnel(
    window: str = Query("24h", pattern="^(1h|24h|7d)$"),
    session: AsyncSession = Depends(get_session_dep),
) -> dict:
    """Stage-by-stage survival of signals, plus why the rest were dropped."""
    hours = {"1h": 1, "24h": 24, "7d": 168}[window]
    # Every column read here is TIMESTAMP WITHOUT TIME ZONE: Signal.time and
    # TimestampMixin.created_at are both naive. Use the module's existing helper
    # (line 102) rather than re-deriving it — mixing naive and aware datetimes is
    # the exact failure this file already guards against at line 912.
    since = _utcnow_naive() - timedelta(hours=hours)

    analyses = await session.scalar(
        select(func.count()).select_from(Signal).where(Signal.time >= since)
    )
    escalated = await session.scalar(
        select(func.count())
        .select_from(Signal)
        .where(and_(Signal.time >= since, Signal.escalated.is_(True)))
    )
    decisions = await session.scalar(
        select(func.count()).select_from(Decision).where(Decision.created_at >= since)
    )
    approved = await session.scalar(
        select(func.count()).select_from(Trade).where(Trade.created_at >= since)
    )
    executed = await session.scalar(
        select(func.count())
        .select_from(Trade)
        .where(and_(Trade.created_at >= since, Trade.status != "approved"))
    )

    bucket = (Signal.opportunity_score / SCORE_BUCKET_WIDTH) * SCORE_BUCKET_WIDTH
    score_rows = (
        await session.execute(
            select(bucket.label("b"), func.count())
            .where(Signal.time >= since)
            .group_by("b")
        )
    ).all()

    factor_rows = (
        await session.execute(
            select(Signal.factors_present, func.count())
            .where(Signal.time >= since)
            .group_by(Signal.factors_present)
        )
    ).all()

    # Haiku's own non-escalation reasons live on `signals`; the two downstream
    # stages report theirs through pipeline_rejections.
    haiku_rows = (
        await session.execute(
            select(Signal.block_reason, func.count())
            .where(and_(Signal.time >= since, Signal.escalated.is_(False)))
            .group_by(Signal.block_reason)
        )
    ).all()
    stage_rows = (
        await session.execute(
            select(PipelineRejection.stage, PipelineRejection.reason, func.count())
            .where(PipelineRejection.time >= since)
            .group_by(PipelineRejection.stage, PipelineRejection.reason)
        )
    ).all()

    block_reasons = [("haiku", str(r), int(c)) for r, c in haiku_rows]
    block_reasons += [(str(s), str(r), int(c)) for s, r, c in stage_rows]

    return build_funnel(
        window=window,
        analyses=int(analyses or 0),
        escalated=int(escalated or 0),
        decisions=int(decisions or 0),
        approved=int(approved or 0),
        executed=int(executed or 0),
        score_buckets={int(b): int(c) for b, c in score_rows},
        block_reasons=block_reasons,
        factors_presence={int(f): int(c) for f, c in factor_rows},
    )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_read_contract.py tests/test_api_gateway_read.py -v`

Expected: PASS.

Then lint:

Run: `make lint`

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/app/read_api.py services/api-gateway/app/read_contract.py
git commit -m "feat(api-gateway): GET /systems/funnel

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9 : Panneau entonnoir dans le Command Center

**Files:**
- Create: `frontend/src/components/command/FunnelPanel.tsx`
- Modify: `frontend/src/lib/types/systems.ts`
- Modify: `frontend/src/lib/api/endpoints.ts`
- Modify: `frontend/src/app/(app)/command/page.tsx`

- [ ] **Step 1: Add the type**

In `frontend/src/lib/types/systems.ts`, append:

```typescript
export interface FunnelStage {
  stage: string;
  count: number;
  conversion_pct: number;
}

export interface FunnelStats {
  window: string;
  stages: FunnelStage[];
  score_histogram: { bucket: number; count: number }[];
  factors_presence: Record<string, number>;
  top_block_reasons: { stage: string; reason: string; count: number }[];
  updated_at: string;
}
```

- [ ] **Step 2: Add the endpoint**

In `frontend/src/lib/api/endpoints.ts`, locate the `systemsApi` object and add a
method alongside the existing `overview`:

```typescript
  funnel: async (window = '24h'): Promise<FunnelStats> =>
    (await api.get(`/systems/funnel?window=${window}`)).data,
```

Add `FunnelStats` to the existing `systems.ts` type import at the top of the file.

- [ ] **Step 3: Build the panel**

Create `frontend/src/components/command/FunnelPanel.tsx`:

```tsx
'use client';
import { useQuery } from '@tanstack/react-query';
import { Box, Stack, Typography, LinearProgress, Chip } from '@mui/material';
import { SectionCard } from '@/components/systems/common';
import { systemsApi } from '@/lib/api/endpoints';

const STAGE_LABEL: Record<string, string> = {
  analyses: 'Analyses (Haiku)',
  escalated: 'Escaladés (Sonnet)',
  decisions: 'Décisions',
  approved: 'Approuvés (risque)',
  executed: 'Exécutés',
};

const REASON_LABEL: Record<string, string> = {
  score_below_threshold: 'score sous le seuil',
  gate_not_met: 'signal trop calme (gate)',
};

export function FunnelPanel() {
  const { data } = useQuery({
    queryKey: ['systems', 'funnel'],
    queryFn: () => systemsApi.funnel('24h'),
    refetchInterval: 30000,
  });

  if (!data) return null;
  const top = data.stages[0]?.count ?? 0;

  return (
    <SectionCard title="Entonnoir du pipeline" subtitle="24 h — où s'arrêtent les signaux">
      <Stack spacing={1.25} sx={{ px: 2, pb: 2 }}>
        {data.stages.map((s) => (
          <Box key={s.stage}>
            <Stack direction="row" justifyContent="space-between" alignItems="baseline">
              <Typography variant="body2">{STAGE_LABEL[s.stage] ?? s.stage}</Typography>
              <Typography variant="caption" className="mono" color="text.secondary">
                {s.count} · {s.conversion_pct}%
              </Typography>
            </Stack>
            <LinearProgress
              variant="determinate"
              value={top > 0 ? Math.min(100, (s.count / top) * 100) : 0}
              sx={{ height: 6, borderRadius: 3 }}
            />
          </Box>
        ))}

        {data.top_block_reasons.length > 0 && (
          <Box sx={{ pt: 1 }}>
            <Typography variant="caption" color="text.secondary">
              Principales causes de blocage
            </Typography>
            <Stack direction="row" flexWrap="wrap" gap={0.5} sx={{ mt: 0.5 }}>
              {data.top_block_reasons.slice(0, 4).map((r) => (
                <Chip
                  key={`${r.stage}-${r.reason}`}
                  size="small"
                  label={`${r.stage} · ${REASON_LABEL[r.reason] ?? r.reason} (${r.count})`}
                  sx={{ fontSize: 11 }}
                />
              ))}
            </Stack>
          </Box>
        )}

        <Box sx={{ pt: 1 }}>
          <Typography variant="caption" color="text.secondary">
            Facteurs disponibles par analyse (sur 4)
          </Typography>
          <Stack direction="row" gap={0.5} sx={{ mt: 0.5 }}>
            {Object.entries(data.factors_presence).map(([k, v]) => (
              <Chip key={k} size="small" label={`${k} : ${v}`} sx={{ fontSize: 11 }} />
            ))}
          </Stack>
        </Box>
      </Stack>
    </SectionCard>
  );
}
```

- [ ] **Step 4: Mount it**

In `frontend/src/app/(app)/command/page.tsx`, add the import:

```typescript
import { FunnelPanel } from '@/components/command/FunnelPanel';
```

and place it in the right-hand column, between `<AiDecisionFeed />` and
`<GuardrailPanel />` (line 49-50):

```tsx
          <AiDecisionFeed />
          <FunnelPanel />
          <GuardrailPanel />
```

- [ ] **Step 5: Verify the build**

Run: `cd frontend && npm run build`

Expected: build succeeds, no TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/command/FunnelPanel.tsx frontend/src/lib/types/systems.ts frontend/src/lib/api/endpoints.ts "frontend/src/app/(app)/command/page.tsx"
git commit -m "feat(frontend): pipeline funnel panel on the command center

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10 : Exposer les seuils dans le compose VPS

Les seuils sont ajustables sans rebuild seulement s'ils traversent le compose.

**Files:**
- Modify: `docker-compose.vps.yml`

- [ ] **Step 1: Add the env passthroughs**

Under the `ai-worker-haiku` service `environment:` block (around line 227):

```yaml
      HAIKU_ESCALATE_SCORE: ${HAIKU_ESCALATE_SCORE:-60}
```

Under `decision-engine` (around line 259):

```yaml
      DECISION_THRESHOLD: ${DECISION_THRESHOLD:-70}
```

Under `risk-engine` (around line 266):

```yaml
      RISK_MIN_SCORE: ${RISK_MIN_SCORE:-70}
      RISK_MIN_CONFIDENCE: ${RISK_MIN_CONFIDENCE:-0.55}
```

- [ ] **Step 2: Verify the compose file parses**

Run: `docker compose -f docker-compose.vps.yml config --quiet`

Expected: no output (valid).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.vps.yml
git commit -m "chore(deploy): expose pipeline thresholds as env overrides

Defaults match current hardcoded values, so this changes no behaviour; it makes
the funnel-driven calibration possible without a rebuild.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 11 : Vérification de bout en bout

- [ ] **Step 1: Full test suite**

Run: `make test`

Expected: **collection completes** (no `Interrupted: N error during collection`)
and the tests added by this phase pass.

Caveat inherited from Task 0: the suite never ran end-to-end before this phase,
so failures unrelated to phase 1 may surface here for the first time. Triage each
one — `git stash` the phase's commits and re-run to confirm whether it predates
this work. Report pre-existing failures to the operator; **do not fix them inside
this phase** and do not weaken a test to make it green.

- [ ] **Step 2: Lint**

Run: `make lint`

Expected: ruff, black --check and mypy all clean.

- [ ] **Step 3: Bring the stack up and apply the migration**

Run: `make up && make migrate`

Expected: `Running upgrade 0007 -> 0008`.

- [ ] **Step 4: Confirm the funnel answers**

Run: `curl -s http://localhost:8000/systems/funnel?window=24h | python -m json.tool`

Expected: a JSON object with the five stages. On a stalled pipeline you should
see a large `analyses` count, `escalated: 0`, and `top_block_reasons` dominated
by `haiku · score_below_threshold` — this is the diagnosis rendered as data.

- [ ] **Step 5: Confirm no behaviour changed**

Run: `docker compose logs ai-worker-haiku --tail 50`

Expected: analyses still published at the same rate; no new errors.

---

## Point de décision (fin de phase 1a/1b)

**Ne pas enchaîner sur 1c automatiquement.** Laisser tourner, puis lire
`/systems/funnel` et rapporter à l'opérateur :

1. `analyses` par 24 h — le pipeline reçoit-il assez de données ?
2. `factors_presence` — si la masse est sur 2 facteurs, la 1c (enrichissement
   volume/liquidité) est justifiée ; si elle est sur 3-4, ce sont les seuils qui
   sont trop hauts.
3. `score_histogram` — où se situe réellement la distribution par rapport à 60.
4. `top_block_reasons` — `score_below_threshold` dominant ⇒ baisser
   `HAIKU_ESCALATE_SCORE` ; `gate_not_met` dominant ⇒ le gate momentum/volume est
   le vrai verrou.

Les valeurs de seuil se décident sur ces chiffres, avec l'opérateur. C'est
l'objet de la phase 1c, qui fera l'objet de son propre plan.

---

## Self-review

**Couverture de la spec (phases 1a + 1b) :**

| Exigence spec | Tâche |
|---|---|
| Colonnes `ambiguous`, `block_reason`, `factors_present` sur `signals` | 5, 6 |
| Facteurs normalisés dans le `payload` JSONB | déjà fait — `persister.py:78` écrit `e.model_dump()`, qui inclut `meta.factors` |
| Table `pipeline_rejections` | 5, 6 |
| decision-engine instrumenté | 4 |
| `factors_present` dès 1a | 1 |
| `liquidity_source` | 1 (valeurs `dex`/`unknown` ; `volume_proxy` arrive en 1c avec le proxy) |
| Endpoint `/systems/funnel` | 7, 8 |
| Panneau entonnoir | 9 |
| `HAIKU_ESCALATE_SCORE` | 3, 10 |
| Confiance découplée de l'ambiguïté | 1 |
| Seuils existants exposés au compose | 10 |

**Hors périmètre de ce plan** (phase 1c, plan séparé) : émission systématique du
`volume_spike_ratio` par collector-coingecko, proxy de liquidité depuis
`volume_24h_usd`, et le choix des valeurs de seuil.

**Cohérence des types :** `block_reason` prend les trois mêmes valeurs partout
(`escalated` | `score_below_threshold` | `gate_not_met`) — scorer (T1),
`AnalysisEvent` (T2), colonne DB (T5), agrégation (T7), libellés frontend (T9).
`stage` vaut `haiku` | `decision_engine` | `risk_engine`, produit par `stage_for`
(T6) et par la requête du funnel (T8), consommé tel quel par le frontend (T9).
