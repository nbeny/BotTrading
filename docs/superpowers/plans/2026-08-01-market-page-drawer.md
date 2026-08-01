# Refonte `/market` — tableau borné + drawer dossier token — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Borner la hauteur de `/market` à un écran et déplacer tout le contenu spécifique à un token dans un drawer latéral alimenté par un nouvel endpoint authentifié `GET /market/tokens/{symbol}/dossier`.

**Architecture :** Les fonctions pures d'assemblage (score par axe, verdict pipeline) vivent dans un nouveau module `services/api-gateway/app/dossier.py` ; `read_api.py` n'y ajoute que le handler et ses requêtes. Côté frontend, la page ne garde que le balayage du marché, et un `Drawer` MUI ancré à droite affiche le dossier complet du token sélectionné, la sélection étant portée par la query string.

**Tech Stack :** FastAPI · SQLAlchemy 2.0 async · pytest (`asyncio_mode = "auto"`) · Next.js 14 App Router · MUI v6 · TanStack Query · Vitest + Testing Library (introduits par la Task 7)

**Spec :** `docs/superpowers/specs/2026-08-01-market-page-drawer-design.md`

---

## Structure des fichiers

### Backend — créés

| Fichier | Responsabilité |
|---|---|
| `services/api-gateway/app/dossier.py` | fonctions **pures** : `build_score`, `build_pipeline`, constantes `AXIS_KEYS`. Aucune dépendance à `read_api` (sinon cycle d'import). |
| `tests/test_dossier_assembly.py` | tests unitaires des fonctions pures, dont la règle axe-absent |
| `tests/test_market_dossier_endpoint.py` | test du handler avec session factice |

### Backend — modifiés

| Fichier | Changement |
|---|---|
| `services/api-gateway/app/read_api.py` | import de `dossier`, handler `market_token_dossier` |
| `services/api-gateway/app/read_contract.py` | entrées `market/dossier`, `market/dossier.score`, `market/dossier.pipeline` |
| `tests/test_read_contract.py` | assertions des trois nouvelles entrées |
| `scripts/verify_read_live.py` | le dossier dans la liste des endpoints vérifiés |

### Frontend — créés

| Fichier | Responsabilité |
|---|---|
| `frontend/src/lib/types/dossier.ts` | types `TokenDossier`, `TokenScore`, `PipelineVerdict`, helper `axisValue` |
| `frontend/src/lib/market/tokensView.ts` | fonction pure `filterAndSortTokens` |
| `frontend/src/components/market/ScoreBreakdown.tsx` | les 7 axes, `—` pour un axe absent |
| `frontend/src/components/market/PipelineVerdictPanel.tsx` | étage atteint / étage de mort |
| `frontend/src/components/market/TokenExposurePanel.tsx` | positions et trades du symbole |
| `frontend/src/components/market/TokenDossierDrawer.tsx` | conteneur, assemble les sections |
| `frontend/src/app/api/mock/market/tokens/[symbol]/dossier/route.ts` | parité mock |
| `frontend/src/lib/mock/dossier.ts` | générateur de dossier factice, **avec un axe absent** |
| `frontend/vitest.config.ts`, `frontend/src/test/setup.ts` | outillage de test (Task 7) |
| `frontend/src/lib/types/__tests__/dossier.test.ts` | test du helper `axisValue` |
| `frontend/src/lib/market/__tests__/tokensView.test.ts` | test de `filterAndSortTokens` |
| `frontend/src/components/market/__tests__/ScoreBreakdown.test.tsx` | un axe absent rend `—`, jamais `0` |

### Frontend — modifiés

| Fichier | Changement |
|---|---|
| `frontend/src/lib/types/domain.ts` | ré-export des types dossier |
| `frontend/src/lib/api/endpoints.ts` | `marketApi.dossier(symbol)` |
| `frontend/src/components/market/TokensTable.tsx` | hauteur fixe, recherche, tri, bascule « voir tout » |
| `frontend/src/components/market/WorkerDecisionsPanel.tsx` | justification tronquée + expansion, mode compact |
| `frontend/src/components/market/TokenPricePanel.tsx` | variante sans en-tête (le drawer porte le titre) |
| `frontend/src/app/(app)/market/page.tsx` | nouvelle disposition, sélection dans l'URL, `LiveEventStream` retiré |
| `frontend/package.json` | scripts `test` / `test:run`, devDependencies vitest |

---

## Task 1 : module `dossier.py` — décomposition du score

**Files:**
- Create: `services/api-gateway/app/dossier.py`
- Test: `tests/test_dossier_assembly.py`

- [ ] **Step 1 : écrire le test qui échoue**

Créer `tests/test_dossier_assembly.py` :

```python
"""Assemblage du dossier token — fonctions pures, sans base.

Le test central est celui de l'axe absent : le scoring v2 renormalise sur le
poids présent, donc un axe non mesuré doit être *exclu* du dict, jamais présent
à 0.0. Une valeur non mesurée qui fuit en lecture confiante déplace toujours le
score dans la direction de cette lecture.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from service_modules import load_service_module

dossier = load_service_module("api-gateway", "dossier")

NOW = datetime(2026, 8, 1, 9, 12, tzinfo=timezone.utc)


def _decision(**kw):
    """Une ligne `decisions`. `payload` est le DecisionEvent sérialisé, dont
    `meta.breakdown` porte la décomposition v2 — voir engine.py:190."""
    breakdown = kw.pop(
        "breakdown",
        {
            "volume_growth": 0.81,
            "social_score": 0.74,
            "news_score": 0.60,
            "market_trend": 0.88,
            "liquidity_score": 0.70,
            "positioning": 0.93,
        },
    )
    base = dict(
        symbol="SOL",
        created_at=NOW,
        opportunity_score=84,
        confidence=0.62,
        payload={"meta": {"breakdown": breakdown}},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_measured_axes_are_reported_with_their_value() -> None:
    score = dossier.build_score(_decision())
    assert score["value"] == 84
    assert score["confidence"] == 0.62
    assert score["axes"]["positioning"] == 0.93
    assert score["axes_total"] == 7
    assert score["insufficient_evidence"] is False


def test_an_unmeasured_axis_is_absent_not_zero() -> None:
    score = dossier.build_score(_decision())
    assert "fundamentals" not in score["axes"], (
        "un axe non mesuré doit être absent du dict : présent à 0.0 il serait "
        "compté comme une mesure au pire, ce que la renormalisation interdit"
    )
    assert len(score["axes"]) == 6


def test_an_axis_explicitly_null_is_treated_as_absent() -> None:
    score = dossier.build_score(_decision(breakdown={"volume_growth": None}))
    assert score["axes"] == {}


def test_a_measured_zero_is_kept() -> None:
    score = dossier.build_score(_decision(breakdown={"volume_growth": 0.0}))
    assert score["axes"] == {"volume_growth": 0.0}


def test_the_haiku_four_factor_keys_are_not_mistaken_for_axes() -> None:
    """`DecisionJournal.factors` porte momentum/volume/sentiment/liquidity — le
    triage Haiku, pas les sept axes. Lire cet espace-là donnerait sept tirets en
    permanence ; ce test fige la distinction."""
    score = dossier.build_score(
        _decision(breakdown={"momentum": 0.9, "volume": 0.8, "sentiment": 0.7})
    )
    assert score["axes"] == {}
    assert score["insufficient_evidence"] is True


def test_an_empty_breakdown_is_insufficient_evidence_not_a_zero_score() -> None:
    """Sous `_MIN_PRESENT_WEIGHT`, scoring.py renvoie `ScoreResult(0, 0.0, {})`.
    Ce 0 n'est pas une mesure et ne doit jamais s'afficher comme telle."""
    score = dossier.build_score(_decision(breakdown={}, opportunity_score=0, confidence=0.0))
    assert score["insufficient_evidence"] is True
    assert score["value"] is None
    assert score["confidence"] is None
    assert score["computed_at"] is not None


def test_no_decision_reports_unknown_not_zero() -> None:
    score = dossier.build_score(None)
    assert score["value"] is None
    assert score["confidence"] is None
    assert score["axes"] == {}
    assert score["axes_total"] == 7
    assert score["computed_at"] is None
    assert score["insufficient_evidence"] is False, (
        "aucune décision n'est pas la même chose que des preuves insuffisantes : "
        "dans le premier cas rien n'a été tenté"
    )
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

```bash
pytest tests/test_dossier_assembly.py -v
```
Attendu : `ModuleNotFoundError` / `FileNotFoundError` — `dossier` n'existe pas.

- [ ] **Step 3 : écrire l'implémentation minimale**

Créer `services/api-gateway/app/dossier.py` :

```python
"""Assemblage du dossier d'un token — fonctions pures.

Séparé de ``read_api`` pour deux raisons : ce module ne touche ni la base ni
FastAPI et se teste donc sans fixture, et ``read_api`` dépasse déjà 1500 lignes.
Il ne doit jamais importer ``read_api`` en retour (cycle d'import) — d'où le
``_iso`` local plutôt qu'un import du helper homonyme.

La règle qui gouverne tout ce fichier : une valeur non mesurée est ``None`` ou
une clé absente, jamais un ``0``. Le scoring v2 renormalise sur le poids des
axes *présents*, donc un axe absent est exclu du calcul ; le rapporter à 0.0 le
transformerait en mesure au pire, ce qui déplace le score vers le bas sans que
rien ne le signale.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

#: Les sept axes de decision-engine/app/scoring.py::WEIGHTS, dans l'ordre
#: d'affichage. Dupliqués ici et non importés : api-gateway ne dépend pas du
#: decision-engine, et cette liste ne bouge que lors d'un changement de modèle
#: de scoring, qui touchera de toute façon les deux fichiers.
AXIS_KEYS: tuple[str, ...] = (
    "volume_growth",
    "social_score",
    "news_score",
    "market_trend",
    "liquidity_score",
    "positioning",
    "fundamentals",
)


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def build_score(decision: Any | None) -> dict:
    """Décomposition par axe du dernier score connu pour un symbole.

    La source est ``Decision.payload["meta"]["breakdown"]`` : ``engine.py`` y
    publie le ``breakdown`` du scoring v2, et le persister sérialise
    l'événement entier dans la colonne ``payload``.

    **Pas** ``DecisionJournal.factors`` : celui-là porte le triage Haiku à
    quatre facteurs (``momentum``/``volume``/``sentiment``/``liquidity``), un
    espace de noms disjoint. L'y lire renverrait ``{}`` en permanence, soit
    sept tirets à l'écran indiscernables d'un vrai « rien mesuré ».

    ``axes`` ne contient que les axes **mesurés**. L'absence d'une clé est
    l'information : elle dit « non mesuré », pas « nul ».
    """
    if decision is None:
        return {
            "value": None,
            "confidence": None,
            "axes": {},
            "axes_total": len(AXIS_KEYS),
            "insufficient_evidence": False,
            "computed_at": None,
        }

    breakdown = ((decision.payload or {}).get("meta") or {}).get("breakdown") or {}
    # `is not None` et non un test de vérité : un axe mesuré à 0.0 est une
    # mesure et doit être conservé.
    axes = {k: float(breakdown[k]) for k in AXIS_KEYS if breakdown.get(k) is not None}

    # Un breakdown vide sur une décision existante veut dire que le poids
    # présent était sous `_MIN_PRESENT_WEIGHT` : scoring.py renvoie alors
    # `ScoreResult(0, 0.0, {})`. Ce 0 n'est pas une mesure, et le publier comme
    # `value` en ferait une — la faute exacte que ce module existe pour éviter.
    insufficient = not axes
    return {
        "value": None if insufficient else decision.opportunity_score,
        "confidence": None if insufficient else decision.confidence,
        "axes": axes,
        "axes_total": len(AXIS_KEYS),
        "insufficient_evidence": insufficient,
        "computed_at": _iso(decision.created_at),
    }
```

- [ ] **Step 4 : lancer le test, vérifier qu'il passe**

```bash
pytest tests/test_dossier_assembly.py -v
```
Attendu : 7 passed.

- [ ] **Step 5 : commit**

```bash
git add services/api-gateway/app/dossier.py tests/test_dossier_assembly.py
git commit -m "feat(api-gateway): décomposition du score par axe, absent ≠ zéro"
```

---

## Task 2 : verdict du pipeline

**Files:**
- Modify: `services/api-gateway/app/dossier.py`
- Test: `tests/test_dossier_assembly.py`

- [ ] **Step 1 : écrire le test qui échoue**

Ajouter à la fin de `tests/test_dossier_assembly.py` :

```python
def _journal(**kw):
    """Une ligne `decision_journal`. Source du *parcours* uniquement — sa
    colonne `factors` porte le triage Haiku à quatre facteurs, pas les sept
    axes, et n'est donc jamais lue par le dossier."""
    base = dict(
        symbol="SOL",
        time=NOW,
        escalated=True,
        sonnet_called=True,
        sonnet_validated=False,
        skip_reason=None,
        decision_event_id=None,
        risk_verdict=None,
        risk_reason=None,
        execution_event_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _rejection(**kw):
    """`stage` porte la SOURCE de l'événement (`decision_engine`/`risk_engine`),
    jamais un id d'étage : c'est ce que `persister.stage_for` écrit réellement."""
    base = dict(symbol="SOL", time=NOW, stage="risk_engine", reason="max_exposure")
    base.update(kw)
    return SimpleNamespace(**base)


def test_execution_reached_is_reported_as_execute() -> None:
    v = dossier.build_pipeline(_journal(execution_event_id="x1"), None)
    assert v["reached_stage"] == "execute"
    assert v["blocked_at"] is None
    assert v["block_reason"] is None


def test_risk_rejection_names_the_stage_and_the_reason() -> None:
    v = dossier.build_pipeline(
        _journal(risk_verdict="rejected", risk_reason="score_below_threshold"), None
    )
    assert v["reached_stage"] == "risk"
    assert v["blocked_at"] == "risk"
    assert v["block_reason"] == "score_below_threshold"


def test_risk_approval_is_not_a_block() -> None:
    v = dossier.build_pipeline(_journal(risk_verdict="approved"), None)
    assert v["reached_stage"] == "risk"
    assert v["blocked_at"] is None
    assert v["block_reason"] is None


def test_triage_refusal_is_a_block_at_triage() -> None:
    """`skip_reason` reste `None` sur un refus de triage réel — ai-worker-sonnet
    ne le renseigne que sur la branche escaladée. C'est donc le repli
    `not_escalated` qui s'observe en production, et lui qu'il faut couvrir."""
    v = dossier.build_pipeline(
        _journal(escalated=False, sonnet_called=False, skip_reason=None), None
    )
    assert v["reached_stage"] == "triage"
    assert v["blocked_at"] == "triage"
    assert v["block_reason"] == "not_escalated"


def test_an_explicit_skip_reason_wins_over_the_fallback() -> None:
    v = dossier.build_pipeline(
        _journal(escalated=False, sonnet_called=False, skip_reason="score_too_low"),
        None,
    )
    assert v["block_reason"] == "score_too_low"


def test_a_published_decision_awaiting_risk_reports_the_decision_stage() -> None:
    v = dossier.build_pipeline(_journal(decision_event_id="d1"), None)
    assert v["reached_stage"] == "decision"
    assert v["blocked_at"] is None


def test_escalated_without_a_sonnet_call_stops_at_triage_without_a_block() -> None:
    """Le cas `cooldown_or_budget` : Haiku a escaladé, Sonnet n'a pas été
    appelé. Rien n'a refusé le signal — il a été mis de côté, ce qui n'est pas
    la même chose et ne doit pas s'afficher comme un blocage."""
    v = dossier.build_pipeline(_journal(escalated=True, sonnet_called=False), None)
    assert v["reached_stage"] == "triage"
    assert v["blocked_at"] is None
    assert v["block_reason"] is None


def test_escalated_but_undecided_claims_no_block() -> None:
    """Sonnet appelé sans décision en aval : en vol ou abandonné, on ne peut pas
    trancher. Affirmer un blocage serait inventer une mesure."""
    v = dossier.build_pipeline(_journal(), None)
    assert v["reached_stage"] == "senior"
    assert v["blocked_at"] is None
    assert v["block_reason"] is None


def test_rejection_without_journal_is_the_fallback() -> None:
    v = dossier.build_pipeline(None, _rejection())
    assert v["reached_stage"] == "risk", "risk_engine doit être normalisé en risk"
    assert v["blocked_at"] == "risk"
    assert v["block_reason"] == "max_exposure"
    assert v["escalated"] is None, (
        "sans ligne de journal on ignore si Haiku avait escaladé : `False` "
        "serait une supposition déguisée en mesure"
    )
    assert v["sonnet_called"] is None


def test_a_decision_engine_rejection_is_normalised_too() -> None:
    v = dossier.build_pipeline(None, _rejection(stage="decision_engine"))
    assert v["reached_stage"] == "decision"


def test_an_unmapped_rejector_stays_visible_under_its_own_name() -> None:
    """`stage_for` laisse passer une source inconnue plutôt que de la masquer ;
    la normalisation doit avoir le même réflexe."""
    v = dossier.build_pipeline(None, _rejection(stage="some_new_service"))
    assert v["reached_stage"] == "some_new_service"


def test_nothing_known_reports_nulls() -> None:
    v = dossier.build_pipeline(None, None)
    assert v == {
        "reached_stage": None,
        "blocked_at": None,
        "block_reason": None,
        "escalated": None,
        "sonnet_called": None,
        "sonnet_validated": None,
        "last_event_at": None,
    }
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

```bash
pytest tests/test_dossier_assembly.py -v -k pipeline
```
Attendu : `AttributeError: module ... has no attribute 'build_pipeline'`.

- [ ] **Step 3 : écrire l'implémentation minimale**

Ajouter à `services/api-gateway/app/dossier.py` :

```python
#: `PipelineRejection.stage` porte la *source* de l'événement
#: (`persister.py::_STAGE_BY_SOURCE`), pas l'id d'étage. Le reste de la
#: plateforme — dont le graphe du Command Center — parle le vocabulaire de
#: `systems_pipeline.py::STAGE_SPECS`, et c'est celui que le frontend sait
#: libeller. Sans cette table, un rejet du decision-engine s'afficherait
#: « decision_engine » en brut dans le drawer.
_STAGE_BY_REJECTION_SOURCE = {
    "decision_engine": "decision",
    "risk_engine": "risk",
}


def _normalise_stage(stage: str) -> str:
    """Vocabulaire de rejet -> id d'étage.

    Une source non mappée passe telle quelle, pour la raison exacte que
    `stage_for` invoque déjà : un rejeteur inattendu doit rester visible plutôt
    que d'être silencieusement renommé ou masqué.
    """
    return _STAGE_BY_REJECTION_SOURCE.get(stage, stage)


def _verdict(j: Any) -> tuple[str, str | None, str | None]:
    """``(reached_stage, blocked_at, block_reason)`` pour une ligne de journal.

    Le persister complète la ligne de journal en aval (``risk_verdict``,
    ``execution_event_id``), donc un seul enregistrement porte tout le parcours.

    On ne déclare un blocage que sur preuve positive. « Sonnet appelé, pas de
    décision » peut être un vol en cours autant qu'un abandon : afficher
    « bloqué » y serait une mesure inventée, exactement la faute que ce projet
    cherche à ne plus commettre.
    """
    if j.execution_event_id:
        return "execute", None, None
    if j.risk_verdict == "rejected":
        return "risk", "risk", j.risk_reason
    if j.risk_verdict == "approved":
        return "risk", None, None
    if j.decision_event_id:
        return "decision", None, None
    if j.sonnet_called:
        return "senior", None, None
    if not j.escalated:
        return "triage", "triage", j.skip_reason or "not_escalated"
    return "triage", None, None


def build_pipeline(journal: Any | None, rejection: Any | None) -> dict:
    """Parcours du dernier signal connu pour un symbole.

    ``journal`` fait autorité quand il existe. ``rejection`` n'est qu'un repli,
    pour les refus qui n'ont jamais eu de ligne de journal.
    """
    if journal is None:
        if rejection is None:
            return {
                "reached_stage": None,
                "blocked_at": None,
                "block_reason": None,
                "escalated": None,
                "sonnet_called": None,
                "sonnet_validated": None,
                "last_event_at": None,
            }
        stage = _normalise_stage(rejection.stage)
        return {
            "reached_stage": stage,
            "blocked_at": stage,
            "block_reason": rejection.reason,
            # `None`, pas `False` : sans ligne de journal on ignore si Haiku
            # avait escaladé. Le decision-engine consomme les analyses en
            # parallèle de Sonnet, donc un rejet déterministe ne dit rien du
            # chemin d'escalade. Répondre `False` serait une supposition
            # déguisée en mesure.
            "escalated": None,
            "sonnet_called": None,
            "sonnet_validated": None,
            "last_event_at": _iso(rejection.time),
        }

    reached, blocked, reason = _verdict(journal)
    return {
        "reached_stage": reached,
        "blocked_at": blocked,
        "block_reason": reason,
        "escalated": bool(journal.escalated),
        "sonnet_called": bool(journal.sonnet_called),
        "sonnet_validated": journal.sonnet_validated,
        "last_event_at": _iso(journal.time),
    }
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

```bash
pytest tests/test_dossier_assembly.py -v
```
Attendu : 20 passed.

- [ ] **Step 5 : commit**

```bash
git add services/api-gateway/app/dossier.py tests/test_dossier_assembly.py
git commit -m "feat(api-gateway): verdict du pipeline par symbole, sans blocage inventé"
```

---

## Task 3 : endpoint `/market/tokens/{symbol}/dossier`

**Files:**
- Modify: `services/api-gateway/app/read_api.py`
- Test: `tests/test_market_dossier_endpoint.py`

- [ ] **Step 1 : écrire le test qui échoue**

Créer `tests/test_market_dossier_endpoint.py` :

```python
"""Handler du dossier token — session factice, pas de base.

Le cas qui compte est le symbole sans historique : il doit répondre 200 avec des
`null` honnêtes, pas 404 et pas des zéros. Un 404 dirait « ce token n'existe
pas » là où la vérité est « rien n'a encore été analysé ».
"""

from __future__ import annotations

from service_modules import load_service_module

read_api = load_service_module("api-gateway", "read_api")


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _EmptySession:
    async def execute(self, _stmt, _params=None):
        return _Result()

    async def scalar(self, _stmt):
        return 0


async def test_unknown_symbol_returns_an_honest_empty_dossier() -> None:
    resp = await read_api.market_token_dossier(symbol="sol", session=_EmptySession())

    assert resp["symbol"] == "SOL"
    assert resp["score"]["value"] is None
    assert resp["score"]["axes"] == {}
    assert resp["pipeline"]["reached_stage"] is None
    assert resp["decisions"] == []
    assert resp["content"] == []
    assert resp["exposure"]["open_positions"] == []
    assert resp["exposure"]["recent_trades"] == []


async def test_symbol_is_upper_cased() -> None:
    resp = await read_api.market_token_dossier(symbol="eth", session=_EmptySession())
    assert resp["symbol"] == "ETH"
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

```bash
pytest tests/test_market_dossier_endpoint.py -v
```
Attendu : `AttributeError: module ... has no attribute 'market_token_dossier'`.

- [ ] **Step 3 : écrire l'implémentation minimale**

Dans `services/api-gateway/app/read_api.py`, ajouter `DecisionJournal` à l'import depuis `cmi_common.db` (bloc de la ligne 28) :

```python
from cmi_common.db import (
    AccountSnapshot,
    Decision,
    DecisionJournal,
    Price,
    ServiceHealth,
    Signal,
    Token,
    Trade,
)
```

Ajouter l'import du module d'assemblage, à côté de `from .funnel import ...` :

```python
from .dossier import build_pipeline, build_score
```

Puis, juste après `market_token_prices` (qui se termine ligne 424), insérer :

```python
#: Bornage des listes du dossier. Le drawer est une lecture de contexte, pas un
#: explorateur : au-delà, l'utilisateur passe par /data.
DOSSIER_LIMIT = 20


@router.get("/market/tokens/{symbol}/dossier")
async def market_token_dossier(
    symbol: str, session: AsyncSession = Depends(get_session_dep)
) -> dict:
    """Tout ce que la plateforme sait d'un token, en un aller-retour.

    Sert le drawer de /market. Monté sur ce routeur, donc authentifié :
    ``main.py`` inclut ``read_api.router`` avec ``Depends(require_principal)``.

    Répond 200 même pour un symbole sans historique — un 404 dirait « inconnu »
    là où la réponse est « rien d'analysé pour l'instant ».
    """
    sym = symbol.upper()

    # Deux sources distinctes, à ne pas confondre : la décomposition en sept
    # axes vit dans `Decision.payload["meta"]["breakdown"]`, tandis que le
    # journal porte le parcours (escalade, verdict Sonnet, verdict risque).
    scored = (
        (
            await session.execute(
                select(Decision)
                .where(Decision.symbol == sym)
                .order_by(Decision.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    journal = (
        (
            await session.execute(
                select(DecisionJournal)
                .where(DecisionJournal.symbol == sym)
                .order_by(DecisionJournal.time.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    rejection = (
        (
            await session.execute(
                select(PipelineRejection)
                .where(PipelineRejection.symbol == sym)
                .order_by(PipelineRejection.time.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    decisions = (
        (
            await session.execute(
                select(Decision)
                .where(Decision.symbol == sym)
                .order_by(Decision.created_at.desc())
                .limit(DOSSIER_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    # news ET social : le drawer montre tout ce qui nomme le symbole, là où
    # /market/news filtre sur kind == "news".
    #
    # Tri sur `fetched_at`, comme `data_content`, et non sur `published_at` :
    # quatre des sept sources sociales (bluesky, reddit, mastodon, fourchan) ne
    # renseignent jamais `published_at`. Trier dessus reléguerait tout ce social
    # en fin de liste quelle que soit sa fraîcheur, et la section n'afficherait
    # que des news — sans erreur ni test rouge.
    content = (
        (
            await session.execute(
                select(RawContent)
                .where(RawContent.symbols.contains([sym]))
                .order_by(RawContent.fetched_at.desc())
                .limit(DOSSIER_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    trades = (
        (
            await session.execute(
                select(Trade)
                .where(Trade.symbol == sym)
                .order_by(Trade.created_at.desc())
                .limit(DOSSIER_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    # Réutilisé et non re-dérivé : `map_position` convertit une *fraction* de
    # taille en quantité via le capital de référence, donc recalculer ici ferait
    # afficher au drawer une taille différente de /portfolio pour la même
    # position.
    positions, _snapshot, capital = await _portfolio_basis(session)

    return {
        "symbol": sym,
        "score": build_score(scored),
        "pipeline": build_pipeline(journal, rejection),
        "decisions": [map_decision(d) for d in decisions],
        "content": [map_news(c) for c in content],
        "exposure": {
            "open_positions": [p for p in positions if p["symbol"] == sym],
            "recent_trades": [map_portfolio_trade(t, capital) for t in trades],
        },
    }
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

```bash
pytest tests/test_market_dossier_endpoint.py tests/test_api_gateway_read.py -v
```
Attendu : les 2 nouveaux tests passent, aucune régression sur `test_api_gateway_read.py`.

- [ ] **Step 5 : vérifier le lint**

```bash
ruff check services/api-gateway && black --check services/api-gateway && mypy services/api-gateway
```
Attendu : aucune erreur.

- [ ] **Step 6 : commit**

```bash
git add services/api-gateway/app/read_api.py tests/test_market_dossier_endpoint.py
git commit -m "feat(api-gateway): GET /market/tokens/{symbol}/dossier"
```

---

## Task 4 : contrat et harnais live

**Files:**
- Modify: `services/api-gateway/app/read_contract.py`
- Modify: `tests/test_read_contract.py`
- Modify: `scripts/verify_read_live.py`

- [ ] **Step 1 : écrire le test qui échoue**

Ajouter à `tests/test_read_contract.py`, avant la section « manifest coverage » (donc avant `test_every_contract_entry_is_actually_asserted`, qui doit rester le dernier test du fichier) :

```python
def _scored_decision(**kw):
    base = dict(
        symbol="SOL",
        created_at=NOW,
        opportunity_score=84,
        confidence=0.62,
        payload={"meta": {"breakdown": {"volume_growth": 0.8, "positioning": 0.9}}},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _journal_row(**kw):
    base = dict(
        symbol="SOL",
        time=NOW,
        escalated=True,
        sonnet_called=True,
        sonnet_validated=False,
        skip_reason=None,
        decision_event_id=None,
        risk_verdict=None,
        risk_reason=None,
        execution_event_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_dossier_score_contract() -> None:
    _assert_exact_keys(
        "market/dossier.score", read_api.build_score(_scored_decision())
    )


def test_dossier_pipeline_contract() -> None:
    _assert_exact_keys(
        "market/dossier.pipeline", read_api.build_pipeline(_journal_row(), None)
    )


async def test_dossier_contract() -> None:
    resp = await read_api.market_token_dossier(symbol="SOL", session=_FakeSession(8))
    _assert_keys("market/dossier", resp)


def test_an_absent_axis_never_reaches_the_wire_as_zero() -> None:
    """Garde-fou de bout en bout : le contrat autorise n'importe quel contenu
    dans `axes`, donc seule cette assertion empêche un axe non mesuré de partir
    à 0.0 vers le navigateur."""
    score = read_api.build_score(_scored_decision())
    assert set(score["axes"]) == {"volume_growth", "positioning"}
    assert "fundamentals" not in score["axes"]
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

```bash
pytest tests/test_read_contract.py -v
```
Attendu : `KeyError: 'market/dossier.score'` — l'entrée n'existe pas au manifeste.

- [ ] **Step 3 : ajouter les entrées au manifeste**

Dans `services/api-gateway/app/read_contract.py`, après l'entrée `"market/decisions"` (ligne 115) :

```python
    # Le dossier d'un token — les trois formes sont déclarées séparément parce
    # que `score` et `pipeline` sont des objets imbriqués : une dérive à
    # l'intérieur est invisible d'un contrôle de clés au premier niveau et
    # n'apparaîtrait que comme `undefined` dans le navigateur.
    "market/dossier": {
        "symbol",
        "score",
        "pipeline",
        "decisions",
        "content",
        "exposure",
    },
    "market/dossier.score": {
        "value",
        "confidence",
        "axes",
        "axes_total",
        "insufficient_evidence",
        "computed_at",
    },
    "market/dossier.pipeline": {
        "reached_stage",
        "blocked_at",
        "block_reason",
        "escalated",
        "sonnet_called",
        "sonnet_validated",
        "last_event_at",
    },
```

- [ ] **Step 4 : exposer les fonctions pures via `read_api`**

Elles sont déjà importées par la Task 3 (`from .dossier import build_pipeline, build_score`), donc `read_api.build_score` et `read_api.build_pipeline` résolvent. Aucun code à écrire ; vérifier :

```bash
pytest tests/test_read_contract.py -v
```
Attendu : tous les tests passent, y compris `test_every_contract_entry_is_actually_asserted`.

- [ ] **Step 5 : verrouiller l'appartenance au routeur authentifié**

Ajouter à `tests/test_market_dossier_endpoint.py` :

```python
def test_the_dossier_route_lives_on_the_authenticated_router() -> None:
    """``main.py`` monte ``read_api.router`` derrière ``require_principal``.

    Un déplacement ultérieur du dossier vers un routeur non authentifié
    exposerait positions et raisonnement IA à qui connaît l'URL, sans qu'aucun
    test générique d'authentification ne s'en aperçoive : ils portent sur une
    application miroir, pas sur ce routeur-ci.
    """
    paths = {getattr(r, "path", None) for r in read_api.router.routes}
    assert "/market/tokens/{symbol}/dossier" in paths
```

Vérifier :

```bash
pytest tests/test_market_dossier_endpoint.py tests/test_api_gateway_auth.py -v
```
Attendu : tout passe.

- [ ] **Step 6 : ajouter l'endpoint au harnais live**

Dans `scripts/verify_read_live.py`, ajouter à la liste des vérifications (à côté de la ligne 114, `("market/decisions", ...)`) :

```python
            (
                "market/dossier",
                read_api.market_token_dossier(symbol="BTC", session=s),
            ),
```

- [ ] **Step 7 : lancer la suite complète et le lint**

```bash
pytest tests/ -q && ruff check services libs && black --check services libs && mypy services libs
```
Attendu : tout vert.

- [ ] **Step 8 : commit**

```bash
git add services/api-gateway/app/read_contract.py tests/test_read_contract.py tests/test_market_dossier_endpoint.py scripts/verify_read_live.py
git commit -m "test(api-gateway): verrouiller le contrat et l'authentification du dossier"
```

---

## Task 5 : types et client frontend

**Files:**
- Create: `frontend/src/lib/types/dossier.ts`
- Modify: `frontend/src/lib/types/domain.ts`
- Modify: `frontend/src/lib/api/endpoints.ts`

- [ ] **Step 1 : écrire les types**

Créer `frontend/src/lib/types/dossier.ts` :

```ts
import type { NewsItem, Position, Trade, WorkerDecision } from './domain';

/** Les sept axes de decision-engine/app/scoring.py::WEIGHTS, dans l'ordre
 *  d'affichage du drawer. */
export const SCORE_AXES = [
  'volume_growth',
  'social_score',
  'news_score',
  'market_trend',
  'liquidity_score',
  'positioning',
  'fundamentals',
] as const;

export type ScoreAxis = (typeof SCORE_AXES)[number];

export const AXIS_LABELS: Record<ScoreAxis, string> = {
  volume_growth: 'Volume',
  social_score: 'Social',
  news_score: 'News',
  market_trend: 'Tendance',
  liquidity_score: 'Liquidité',
  positioning: 'Positionnement',
  fundamentals: 'Fondamentaux',
};

export interface TokenScore {
  value: number | null;
  confidence: number | null;
  /**
   * Seuls les axes **mesurés** sont présents. Une clé absente signifie « non
   * mesuré » : le scoring renormalise sur le poids présent, donc l'axe est
   * exclu du calcul, pas noté zéro. Le type est `Partial` exprès — il force
   * l'appelant à traiter `undefined`, ce qu'un `Record` complet masquerait.
   */
  axes: Partial<Record<ScoreAxis, number>>;
  axes_total: number;
  /**
   * `true` : une décision existe, mais le poids des axes présents était sous le
   * seuil de renormalisation — le back renvoie alors `value: null` plutôt que le
   * `0` que le moteur de scoring produit dans ce cas. `false` quand aucune
   * décision n'existe : rien n'a été tenté, ce n'est pas la même chose.
   */
  insufficient_evidence: boolean;
  computed_at: string | null;
}

export interface PipelineVerdict {
  reached_stage: string | null;
  /** `null` = aucun blocage observé. Ce n'est pas « passé » : ce peut être un
   *  signal encore en vol. L'UI doit distinguer les deux. */
  blocked_at: string | null;
  block_reason: string | null;
  /** `null` quand la seule trace est un rejet sans ligne de journal : on ignore
   *  alors si Haiku avait escaladé. Ne pas rendre `null` comme « non ». */
  escalated: boolean | null;
  sonnet_called: boolean | null;
  sonnet_validated: boolean | null;
  last_event_at: string | null;
}

export interface TokenExposure {
  open_positions: Position[];
  recent_trades: Trade[];
}

export interface TokenDossier {
  symbol: string;
  score: TokenScore;
  pipeline: PipelineVerdict;
  decisions: WorkerDecision[];
  /** News **et** social mentionnant le symbole. */
  content: NewsItem[];
  exposure: TokenExposure;
}

/**
 * Valeur d'un axe, `undefined` normalisé en `null`.
 *
 * Exister sous cette forme est le point : `axes[axis] ?? 0` est l'erreur que ce
 * projet a déjà commise quatorze fois sous d'autres formes, et elle ne lève
 * rien. Passer par ce helper rend l'absence explicite au site d'appel.
 */
export function axisValue(
  axes: TokenScore['axes'],
  axis: ScoreAxis,
): number | null {
  const v = axes[axis];
  return v === undefined ? null : v;
}
```

- [ ] **Step 2 : ré-exporter depuis `domain.ts`**

Ajouter en fin de `frontend/src/lib/types/domain.ts` :

```ts
export type {
  PipelineVerdict,
  ScoreAxis,
  TokenDossier,
  TokenExposure,
  TokenScore,
} from './dossier';
```

- [ ] **Step 3 : ajouter l'appel API**

Dans `frontend/src/lib/api/endpoints.ts`, à l'intérieur de `marketApi` (après `prices`, ligne 46) :

```ts
  dossier: (symbol: string) =>
    api
      .get<TokenDossier>(`/market/tokens/${symbol}/dossier`)
      .then((r) => r.data),
```

et ajouter `TokenDossier` à l'import de types en tête de fichier.

- [ ] **Step 4 : vérifier la compilation**

```bash
cd frontend && npm run typecheck
```
Attendu : aucune erreur.

- [ ] **Step 5 : commit**

```bash
git add frontend/src/lib/types/dossier.ts frontend/src/lib/types/domain.ts frontend/src/lib/api/endpoints.ts
git commit -m "feat(frontend): types du dossier token, axe absent typé absent"
```

---

## Task 6 : route mock du dossier

**Files:**
- Create: `frontend/src/lib/mock/dossier.ts`
- Create: `frontend/src/app/api/mock/market/tokens/[symbol]/dossier/route.ts`

- [ ] **Step 1 : écrire le générateur**

Créer `frontend/src/lib/mock/dossier.ts` :

```ts
import type { TokenDossier } from '@/lib/types/dossier';
import { getDecisions, getNews, getPositions, getToken, getTrades } from './store';

/**
 * Dossier factice pour un symbole.
 *
 * `fundamentals` est volontairement **absent** de `axes` : c'est le seul cas
 * que le développement front ne verrait jamais autrement, et c'est celui dont
 * le rendu (`—`, pas `0`) est le plus facile à casser sans s'en apercevoir.
 */
export function getDossier(symbol: string): TokenDossier | null {
  const sym = symbol.toUpperCase();
  const token = getToken(sym);
  if (!token) return null;

  return {
    symbol: sym,
    score: {
      value: token.opportunity_score,
      confidence: 0.62,
      axes: {
        volume_growth: 0.81,
        social_score: 0.74,
        news_score: 0.6,
        market_trend: 0.88,
        liquidity_score: 0.7,
        positioning: 0.93,
        // fundamentals : non mesuré — clé absente exprès, voir la docstring
      },
      axes_total: 7,
      insufficient_evidence: false,
      computed_at: new Date(Date.now() - 12 * 60_000).toISOString(),
    },
    pipeline: {
      reached_stage: 'risk',
      blocked_at: 'risk',
      block_reason: 'score_below_threshold',
      escalated: true,
      sonnet_called: true,
      sonnet_validated: false,
      last_event_at: new Date(Date.now() - 12 * 60_000).toISOString(),
    },
    decisions: getDecisions(30).filter((d) => d.symbol === sym),
    content: getNews(50).filter((n) => n.symbols.includes(sym)),
    exposure: {
      open_positions: getPositions().filter((p) => p.symbol === sym),
      recent_trades: getTrades(50).filter((t) => t.symbol === sym),
    },
  };
}
```

- [ ] **Step 2 : écrire la route**

Créer `frontend/src/app/api/mock/market/tokens/[symbol]/dossier/route.ts` :

```ts
import { NextResponse } from 'next/server';
import { getDossier } from '@/lib/mock/dossier';

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const dossier = getDossier(symbol);
  if (!dossier) {
    return NextResponse.json({ detail: 'Token not found' }, { status: 404 });
  }
  return NextResponse.json(dossier);
}
```

- [ ] **Step 3 : vérifier la compilation et la route**

```bash
cd frontend && npm run typecheck
```
Attendu : aucune erreur.

```bash
cd frontend && NEXT_PUBLIC_USE_MOCK=1 npm run dev
```
Puis dans un autre terminal :
```bash
curl -s http://localhost:3000/api/mock/market/tokens/BTC/dossier | head -40
```
Attendu : un JSON dont `score.axes` contient 6 clés et **pas** `fundamentals`.

- [ ] **Step 4 : commit**

```bash
git add frontend/src/lib/mock/dossier.ts "frontend/src/app/api/mock/market/tokens/[symbol]/dossier/route.ts"
git commit -m "feat(frontend): route mock du dossier, avec un axe non mesuré"
```

---

## Task 7 : outillage de test frontend (Vitest)

> Le frontend n'a aujourd'hui **aucun** exécuteur de test. Cette tâche en ajoute un, parce que les tâches 8 à 13 encodent la règle « axe absent ≠ zéro » dans du rendu, et que c'est précisément la classe de défaut que ce projet a rencontrée quatorze fois sans qu'aucun test n'échoue. Si le choix est fait de s'en passer, sauter cette tâche et supprimer les tâches de test de la 8 — la vérification devient alors manuelle via le mock.

**Files:**
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/test/setup.ts`
- Modify: `frontend/package.json`

- [ ] **Step 1 : installer les dépendances**

```bash
cd frontend && npm install -D vitest@^2 @vitejs/plugin-react@^4 jsdom@^25 @testing-library/react@^16 @testing-library/jest-dom@^6
```

- [ ] **Step 2 : écrire la configuration**

Créer `frontend/vitest.config.ts` :

```ts
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
});
```

Créer `frontend/src/test/setup.ts` :

```ts
import '@testing-library/jest-dom/vitest';
```

- [ ] **Step 3 : ajouter les scripts**

Dans `frontend/package.json`, dans `"scripts"` :

```json
    "test": "vitest",
    "test:run": "vitest run",
```

- [ ] **Step 4 : écrire un test de fumée qui doit passer**

Créer `frontend/src/lib/types/__tests__/dossier.test.ts` :

```ts
import { describe, expect, it } from 'vitest';
import { axisValue } from '../dossier';

describe('axisValue', () => {
  it('rend la valeur d’un axe mesuré', () => {
    expect(axisValue({ positioning: 0.93 }, 'positioning')).toBe(0.93);
  });

  it('rend null — et non 0 — pour un axe absent', () => {
    expect(axisValue({ positioning: 0.93 }, 'fundamentals')).toBeNull();
  });

  it('conserve un zéro mesuré', () => {
    expect(axisValue({ volume_growth: 0 }, 'volume_growth')).toBe(0);
  });
});
```

- [ ] **Step 5 : lancer les tests**

```bash
cd frontend && npm run test:run
```
Attendu : 3 passed.

- [ ] **Step 6 : commit**

```bash
git add frontend/vitest.config.ts frontend/src/test/setup.ts frontend/package.json frontend/package-lock.json frontend/src/lib/types/__tests__/dossier.test.ts
git commit -m "test(frontend): mettre en place vitest, couvrir axisValue"
```

---

## Task 7 bis : l'échelle de `ScoreChip`

> Découvert en implémentant la Task 6, hors du périmètre initial. Ce n'est pas une
> régression de ce chantier : le bug est en production aujourd'hui, sur quatre pages.

**Le défaut.** `ScoreChip` (`frontend/src/components/common/index.tsx:127`) rend
`Math.round(score)` et colore via `scoreColor` (`frontend/src/lib/format.ts:91`), dont les
seuils sont `>= 75` / `>= 50` / `> 0` — donc une échelle 0–100. Or les trois mappers qui
alimentent ses appelants divisent par 100 :

| Source | Échelle produite |
|---|---|
| `map_token` (`read_api.py:167`) | 0–1 |
| `map_decision` (`read_api.py:224`) | 0–1 |
| Store mock (6 sites) | 0–1 |

Un token scoré 0.79 affiche donc **`1`**, en **rouge** (`0.79 > 0` tombe dans la branche
`error`). Tous les tokens scorés affichent `1` en rouge, les non scorés `0` en gris. La
colonne « Score opp. » ne transmet aucune information.

Quatre sites d'appel : `TokensTable`, `WorkerDecisionsPanel`, `SignalsTable`,
`OpportunitiesSection` — pages market, dashboard et trading.

**Le choix de correction.** On corrige `ScoreChip`, pas les quatre appelants : les quatre
reçoivent la même échelle 0–1, donc le défaut est dans le composant qui la mésinterprète, et
le corriger là évite d'avoir à se souvenir de multiplier à chaque nouvel appel.

**Files:**
- Modify: `frontend/src/components/common/index.tsx`
- Test: `frontend/src/components/common/__tests__/ScoreChip.test.tsx`

- [ ] **Step 1 : écrire le test qui échoue**

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ScoreChip } from '../index';

describe('ScoreChip', () => {
  it('rend un score 0–1 en pourcentage entier', () => {
    render(<ScoreChip score={0.79} />);
    expect(screen.getByText('79')).toBeInTheDocument();
  });

  it('colore selon le score réel, pas selon la valeur brute', () => {
    // 0.79 -> 79 -> success. Avant correction, scoreColor(0.79) tombait dans
    // la branche `> 0` et rendait tout en rouge.
    const { container } = render(<ScoreChip score={0.79} />);
    expect(container.querySelector('.MuiChip-colorSuccess')).not.toBeNull();
  });

  it('distingue un zéro mesuré d’un score faible', () => {
    render(<ScoreChip score={0} />);
    expect(screen.getByText('0')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2 : lancer, vérifier l'échec**

```bash
cd frontend && npm run test:run -- ScoreChip
```
Attendu : le premier test échoue en trouvant `1` au lieu de `79`.

- [ ] **Step 3 : corriger**

Dans `frontend/src/components/common/index.tsx` :

```tsx
/**
 * Badge de score d'opportunité.
 *
 * `score` arrive sur **0–1** : les trois mappers du backend (`map_token`,
 * `map_decision`) et le store mock divisent tous par 100. `scoreColor` raisonne
 * en revanche sur 0–100. Sans cette conversion, tout score non nul s'affichait
 * `1` en rouge — la colonne ne transmettait aucune information.
 */
export function ScoreChip({ score, size = 'small' }: { score: number; size?: ChipProps['size'] }) {
  const pct = Math.round(score * 100);
  return <Chip label={pct} color={scoreColor(pct)} size={size} variant="filled" />;
}
```

- [ ] **Step 4 : vérifier**

```bash
cd frontend && npm run test:run && npm run typecheck && npm run lint
```
Attendu : tout vert.

Vérifier aussi visuellement en mock que les quatre pages affichent des scores plausibles :

```bash
cd frontend && NEXT_PUBLIC_USE_MOCK=1 npm run dev
```
puis `/market`, `/dashboard`, `/trading`.

- [ ] **Step 5 : commit**

```bash
git add frontend/src/components/common/index.tsx "frontend/src/components/common/__tests__/ScoreChip.test.tsx"
git commit -m "fix(frontend): ScoreChip interprétait une échelle 0-1 comme 0-100

Les trois mappers qui l'alimentent divisent par 100, scoreColor raisonne sur
0-100 : tout score non nul s'affichait 1 en rouge, sur market, dashboard et
trading. La colonne Score ne transmettait aucune information."
```

---

## Task 8 : composant `ScoreBreakdown`

**Files:**
- Create: `frontend/src/components/market/ScoreBreakdown.tsx`
- Test: `frontend/src/components/market/__tests__/ScoreBreakdown.test.tsx`

- [ ] **Step 1 : écrire le test qui échoue**

Créer `frontend/src/components/market/__tests__/ScoreBreakdown.test.tsx` :

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ScoreBreakdown } from '../ScoreBreakdown';
import type { TokenScore } from '@/lib/types/dossier';

const score: TokenScore = {
  value: 84,
  confidence: 0.62,
  axes: { volume_growth: 0.81, positioning: 0.93 },
  axes_total: 7,
  insufficient_evidence: false,
  computed_at: '2026-08-01T09:12:00Z',
};

describe('ScoreBreakdown', () => {
  it('affiche les axes mesurés avec leur valeur', () => {
    render(<ScoreBreakdown score={score} />);
    expect(screen.getByTestId('axis-positioning')).toHaveTextContent('93');
  });

  it('rend un axe non mesuré en tiret, jamais en zéro', () => {
    render(<ScoreBreakdown score={score} />);
    const cell = screen.getByTestId('axis-fundamentals');
    expect(cell).toHaveTextContent('—');
    expect(cell).not.toHaveTextContent('0');
  });

  it('annonce combien d’axes sont mesurés', () => {
    render(<ScoreBreakdown score={score} />);
    expect(screen.getByText(/2 axes sur 7/)).toBeInTheDocument();
  });

  it('dit que les preuves étaient insuffisantes plutôt que d’afficher un score', () => {
    render(
      <ScoreBreakdown
        score={{
          value: null,
          confidence: null,
          axes: {},
          axes_total: 7,
          insufficient_evidence: true,
          computed_at: '2026-08-01T09:12:00Z',
        }}
      />,
    );
    expect(screen.getByText(/Preuves insuffisantes/)).toBeInTheDocument();
  });

  it('sans score, rend tous les axes en tiret sans planter', () => {
    render(
      <ScoreBreakdown
        score={{
          value: null,
          confidence: null,
          axes: {},
          axes_total: 7,
          insufficient_evidence: false,
          computed_at: null,
        }}
      />,
    );
    expect(screen.getByTestId('axis-volume_growth')).toHaveTextContent('—');
    expect(screen.getByText(/0 axe sur 7/)).toBeInTheDocument();
    expect(screen.queryByText(/Preuves insuffisantes/)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

```bash
cd frontend && npm run test:run -- ScoreBreakdown
```
Attendu : échec de résolution du module `../ScoreBreakdown`.

- [ ] **Step 3 : écrire l'implémentation minimale**

Créer `frontend/src/components/market/ScoreBreakdown.tsx` :

```tsx
'use client';

import { Box, LinearProgress, Stack, Tooltip, Typography } from '@mui/material';
import {
  AXIS_LABELS,
  SCORE_AXES,
  axisValue,
  type TokenScore,
} from '@/lib/types/dossier';

interface Props {
  score: TokenScore;
}

/**
 * Les sept axes du scoring, mesurés et non mesurés.
 *
 * Un axe non mesuré rend `—`, jamais `0`. Ce n'est pas une préférence de style :
 * le score renormalise sur le poids des axes présents, donc un axe absent est
 * exclu du calcul. L'afficher à 0 dirait « mesuré, et mauvais » — la lecture
 * exactement inverse de la vérité.
 */
export function ScoreBreakdown({ score }: Props) {
  const measured = SCORE_AXES.filter(
    (a) => axisValue(score.axes, a) !== null,
  ).length;

  return (
    <Box>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 1 }}>
        <Typography variant="h6" className="mono" sx={{ fontWeight: 800 }}>
          {score.value ?? '—'}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          / 100
          {score.confidence !== null &&
            ` · confiance ${(score.confidence * 100).toFixed(0)} %`}
        </Typography>
      </Stack>

      <Stack spacing={0.75}>
        {SCORE_AXES.map((axis) => {
          const v = axisValue(score.axes, axis);
          const absent = v === null;
          return (
            <Stack
              key={axis}
              direction="row"
              alignItems="center"
              spacing={1.5}
              data-testid={`axis-${axis}`}
              sx={{ opacity: absent ? 0.45 : 1 }}
            >
              <Typography variant="caption" sx={{ width: 110, flexShrink: 0 }}>
                {AXIS_LABELS[axis]}
              </Typography>
              <Box sx={{ flex: 1 }}>
                <LinearProgress
                  variant="determinate"
                  value={absent ? 0 : v * 100}
                  sx={{ height: 5, borderRadius: 3 }}
                />
              </Box>
              <Typography
                variant="caption"
                className="mono"
                sx={{ width: 34, textAlign: 'right' }}
              >
                {absent ? '—' : (v * 100).toFixed(0)}
              </Typography>
            </Stack>
          );
        })}
      </Stack>

      {/* Le moteur renvoie un score de 0 quand le poids présent est sous son
          seuil de renormalisation. Le back le convertit en `value: null` ; ici
          on dit pourquoi, sinon « — » se lit comme « jamais analysé ». */}
      {score.insufficient_evidence && (
        <Typography variant="caption" color="warning.main" sx={{ display: 'block', mt: 1 }}>
          Preuves insuffisantes — trop peu d&apos;axes mesurés pour calculer un score
          honnête.
        </Typography>
      )}

      <Tooltip title="Un axe non mesuré est exclu du calcul du score — il n'est pas compté zéro.">
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: 'block', mt: 1 }}
        >
          {measured} axe{measured > 1 ? 's' : ''} sur {score.axes_total} mesuré
          {measured > 1 ? 's' : ''} · « — » = non mesuré, exclu du calcul
        </Typography>
      </Tooltip>
    </Box>
  );
}
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

```bash
cd frontend && npm run test:run -- ScoreBreakdown
```
Attendu : 5 passed.

- [ ] **Step 5 : commit**

```bash
git add frontend/src/components/market/ScoreBreakdown.tsx "frontend/src/components/market/__tests__/ScoreBreakdown.test.tsx"
git commit -m "feat(frontend): panneau de décomposition du score, axe absent en tiret"
```

---

## Task 9 : composant `PipelineVerdictPanel`

**Files:**
- Create: `frontend/src/components/market/PipelineVerdictPanel.tsx`
- Test: `frontend/src/components/market/__tests__/PipelineVerdictPanel.test.tsx`

- [ ] **Step 1 : écrire le test qui échoue**

Créer `frontend/src/components/market/__tests__/PipelineVerdictPanel.test.tsx` :

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PipelineVerdictPanel } from '../PipelineVerdictPanel';
import type { PipelineVerdict } from '@/lib/types/dossier';

const base: PipelineVerdict = {
  reached_stage: 'risk',
  blocked_at: null,
  block_reason: null,
  escalated: true,
  sonnet_called: true,
  sonnet_validated: true,
  last_event_at: '2026-08-01T09:12:00Z',
};

describe('PipelineVerdictPanel', () => {
  it('nomme l’étage de blocage et son motif', () => {
    render(
      <PipelineVerdictPanel
        verdict={{
          ...base,
          blocked_at: 'risk',
          block_reason: 'score_below_threshold',
        }}
      />,
    );
    expect(screen.getByText(/Risque/)).toBeInTheDocument();
    expect(screen.getByText(/score_below_threshold/)).toBeInTheDocument();
  });

  it('sans blocage observé, ne prétend pas que le signal est passé', () => {
    render(<PipelineVerdictPanel verdict={base} />);
    expect(screen.getByText(/Aucun blocage observé/)).toBeInTheDocument();
  });

  it('sans historique, rend un tiret', () => {
    render(
      <PipelineVerdictPanel
        verdict={{
          reached_stage: null,
          blocked_at: null,
          block_reason: null,
          escalated: false,
          sonnet_called: false,
          sonnet_validated: null,
          last_event_at: null,
        }}
      />,
    );
    expect(screen.getByText(/Jamais analysé/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

```bash
cd frontend && npm run test:run -- PipelineVerdictPanel
```
Attendu : échec de résolution du module.

- [ ] **Step 3 : écrire l'implémentation minimale**

Créer `frontend/src/components/market/PipelineVerdictPanel.tsx` :

```tsx
'use client';

import { Box, Chip, Stack, Typography } from '@mui/material';
import type { PipelineVerdict } from '@/lib/types/dossier';

/** Vocabulaire aligné sur api-gateway/app/systems_pipeline.py::STAGE_SPECS. */
const STAGE_LABELS: Record<string, string> = {
  collect: 'Collecte',
  sentiment: 'Sentiment',
  triage: 'Triage (Haiku)',
  senior: 'Analyse (Sonnet)',
  decision: 'Décision',
  risk: 'Risque',
  execute: 'Exécution',
};

function label(stage: string | null): string {
  if (stage === null) return '—';
  return STAGE_LABELS[stage] ?? stage;
}

interface Props {
  verdict: PipelineVerdict;
}

/**
 * Où le dernier signal du token s'est arrêté, et pourquoi.
 *
 * `blocked_at === null` ne veut pas dire « passé » : le signal peut être encore
 * en vol. Les deux états sont rendus différemment — affirmer un succès non
 * observé serait la même faute que rapporter un zéro non mesuré.
 */
export function PipelineVerdictPanel({ verdict }: Props) {
  if (verdict.reached_stage === null) {
    return (
      <Typography variant="body2" color="text.secondary">
        Jamais analysé — aucun signal enregistré pour ce token.
      </Typography>
    );
  }

  const blocked = verdict.blocked_at !== null;

  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Chip size="small" label={`Atteint : ${label(verdict.reached_stage)}`} />
        {verdict.escalated && (
          <Chip size="small" color="warning" variant="outlined" label="Escaladé" />
        )}
        {verdict.sonnet_called && (
          <Chip
            size="small"
            color={verdict.sonnet_validated ? 'success' : 'default'}
            variant="outlined"
            label={
              verdict.sonnet_validated === null
                ? 'Sonnet — verdict inconnu'
                : verdict.sonnet_validated
                  ? 'Sonnet — validé'
                  : 'Sonnet — non validé'
            }
          />
        )}
      </Stack>

      <Typography variant="body2" sx={{ mt: 1 }}>
        {blocked ? (
          <>
            Bloqué à <b>{label(verdict.blocked_at)}</b>
            {verdict.block_reason && (
              <>
                {' — motif : '}
                <code>{verdict.block_reason}</code>
              </>
            )}
          </>
        ) : (
          'Aucun blocage observé — le signal a pu poursuivre, ou être encore en cours de traitement.'
        )}
      </Typography>
    </Box>
  );
}
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

```bash
cd frontend && npm run test:run -- PipelineVerdictPanel
```
Attendu : 3 passed.

- [ ] **Step 5 : commit**

```bash
git add frontend/src/components/market/PipelineVerdictPanel.tsx "frontend/src/components/market/__tests__/PipelineVerdictPanel.test.tsx"
git commit -m "feat(frontend): panneau verdict pipeline, sans succès présumé"
```

---

## Task 10 : composant `TokenExposurePanel`

**Files:**
- Create: `frontend/src/components/market/TokenExposurePanel.tsx`

- [ ] **Step 1 : écrire le composant**

Créer `frontend/src/components/market/TokenExposurePanel.tsx` :

```tsx
'use client';

import { Stack, Typography } from '@mui/material';
import type { TokenExposure } from '@/lib/types/dossier';
import { DeltaText, EmptyState } from '@/components/common';
import { fmtUsd } from '@/lib/format';

interface Props {
  exposure: TokenExposure;
}

/** Positions ouvertes et trades récents sur ce symbole uniquement. */
export function TokenExposurePanel({ exposure }: Props) {
  const { open_positions: positions, recent_trades: trades } = exposure;

  if (positions.length === 0 && trades.length === 0) {
    return <EmptyState message="Aucune position ouverte, aucun trade récent." />;
  }

  return (
    <Stack spacing={1.5}>
      {positions.map((p) => (
        <Stack
          key={p.position_id}
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Typography variant="body2" className="mono">
            {p.direction.toUpperCase()} · {p.quantity} @ {fmtUsd(p.entry_price)}
          </Typography>
          <DeltaText value={p.unrealized_pnl_pct} variant="body2" />
        </Stack>
      ))}

      {trades.length > 0 && (
        <Typography variant="caption" color="text.secondary">
          {trades.length} trade{trades.length > 1 ? 's' : ''} sur ce symbole
        </Typography>
      )}
    </Stack>
  );
}
```

- [ ] **Step 2 : vérifier la compilation**

```bash
cd frontend && npm run typecheck
```
Attendu : aucune erreur. Si `Position` n'expose pas `position_id`, `direction`, `quantity`, `entry_price` ou `unrealized_pnl_pct`, aligner les noms sur `frontend/src/lib/types/domain.ts` — le manifeste `portfolio/positions` de `read_contract.py` fait foi.

- [ ] **Step 3 : commit**

```bash
git add frontend/src/components/market/TokenExposurePanel.tsx
git commit -m "feat(frontend): panneau exposition par token"
```

---

## Task 11 : `WorkerDecisionsPanel` — justification tronquée et mode compact

**Files:**
- Modify: `frontend/src/components/market/WorkerDecisionsPanel.tsx`

- [ ] **Step 1 : rendre la justification repliable**

Dans `DecisionCard`, remplacer le bloc « Justification » (lignes 104-127) par :

```tsx
      {/* Justification — repliée à 3 lignes : une seule justification complète
          remplit la largeur du drawer, ce qui remettait les décisions hors de
          vue, le problème même que cette refonte corrige. */}
      <Box
        onClick={() => setExpanded((v) => !v)}
        sx={{
          p: 1.5,
          borderRadius: 1.5,
          bgcolor: 'rgba(0,0,0,0.2)',
          border: '1px solid rgba(255,255,255,0.05)',
          cursor: 'pointer',
        }}
      >
        <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6, display: 'block', mb: 0.75 }}>
          Justification {expanded ? '▲' : '▼'}
        </Typography>
        <Typography
          variant="body2"
          sx={{
            lineHeight: 1.6,
            color: 'text.primary',
            fontSize: 13,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            ...(expanded
              ? {}
              : {
                  display: '-webkit-box',
                  WebkitLineClamp: 3,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }),
          }}
        >
          {d.justification}
        </Typography>
      </Box>
```

et en tête de `DecisionCard` :

```tsx
function DecisionCard({ d, now }: { d: WorkerDecision; now: number }) {
  const [expanded, setExpanded] = useState(false);
  const meta = WORKER_META[d.worker];
```

Ajouter `useState` à l'import React en tête de fichier :

```tsx
import { useState } from 'react';
```

- [ ] **Step 2 : ajouter le mode sans carte**

Le drawer fournit déjà son propre titre et son propre fond ; la `Card` du panneau ferait une boîte dans une boîte. Remplacer la signature et le retour de `WorkerDecisionsPanel` :

```tsx
interface Props {
  decisions: WorkerDecision[];
  loading: boolean;
  now: number;
  /** `true` dans le drawer : pas de Card ni de titre, le conteneur les porte. */
  bare?: boolean;
}
```

et à la fin du composant :

```tsx
  const body = (
    <Stack spacing={3} divider={<Divider sx={{ borderColor: 'rgba(255,255,255,0.06)' }} />}>
      {sonnetDecisions.length > 0 && (
        <WorkerGroup worker="sonnet" decisions={sonnetDecisions} now={now} />
      )}
      {haikuDecisions.length > 0 && (
        <WorkerGroup worker="haiku" decisions={haikuDecisions} now={now} />
      )}
    </Stack>
  );

  if (bare) return body;

  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Décisions des workers Claude
        </Typography>
        {body}
      </CardContent>
    </Card>
  );
```

- [ ] **Step 3 : vérifier la compilation**

```bash
cd frontend && npm run typecheck
```
Attendu : aucune erreur.

- [ ] **Step 4 : commit**

```bash
git add frontend/src/components/market/WorkerDecisionsPanel.tsx
git commit -m "feat(frontend): justifications repliables, mode sans carte pour le drawer"
```

---

## Task 12 : `TokenDossierDrawer`

**Files:**
- Create: `frontend/src/components/market/TokenDossierDrawer.tsx`

- [ ] **Step 1 : écrire le composant**

Créer `frontend/src/components/market/TokenDossierDrawer.tsx` :

```tsx
'use client';

import { useState } from 'react';
import {
  Box,
  Chip,
  Divider,
  Drawer,
  IconButton,
  Skeleton,
  Stack,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import OpenInFullIcon from '@mui/icons-material/OpenInFull';
import CloseFullscreenIcon from '@mui/icons-material/CloseFullscreen';
import WhatshotIcon from '@mui/icons-material/Whatshot';
import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api/endpoints';
import type { MarketToken } from '@/lib/types/domain';
import { DeltaText, EmptyState } from '@/components/common';
import { fmtUsd } from '@/lib/format';
import { TokenPricePanel } from './TokenPricePanel';
import { WorkerDecisionsPanel } from './WorkerDecisionsPanel';
import { NewsPanel } from './NewsPanel';
import { ScoreBreakdown } from './ScoreBreakdown';
import { PipelineVerdictPanel } from './PipelineVerdictPanel';
import { TokenExposurePanel } from './TokenExposurePanel';

interface Props {
  token: MarketToken | null;
  onClose: () => void;
  now: number;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Box>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ textTransform: 'uppercase', letterSpacing: 0.6, display: 'block', mb: 1 }}
      >
        {title}
      </Typography>
      {children}
    </Box>
  );
}

/**
 * Tout ce que la plateforme sait d'un token, dans un panneau latéral.
 *
 * L'en-tête est rendu depuis le `MarketToken` déjà en mémoire, donc il
 * s'affiche au clic sans attendre le réseau ; seules les sections en aval
 * dépendent de la requête.
 */
export function TokenDossierDrawer({ token, onClose, now }: Props) {
  const [wide, setWide] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ['market', 'dossier', token?.symbol],
    queryFn: () => marketApi.dossier(token!.symbol),
    enabled: !!token,
    refetchInterval: 60_000,
  });

  return (
    <Drawer
      anchor="right"
      open={!!token}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: { xs: '100%', sm: wide ? 1040 : 640 },
          maxWidth: '100%',
          bgcolor: 'rgba(8,11,20,0.92)',
          backdropFilter: 'blur(16px)',
          p: 2.5,
          transition: 'width 0.2s',
        },
      }}
    >
      {token && (
        <Stack spacing={2.5}>
          {/* En-tête */}
          <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
            <Box>
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="h6" className="mono" sx={{ fontWeight: 800 }}>
                  {token.symbol}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {token.name}
                </Typography>
                {token.is_trending && (
                  <Chip
                    icon={<WhatshotIcon />}
                    label="Hot"
                    color="warning"
                    size="small"
                    variant="outlined"
                  />
                )}
              </Stack>
              <Stack direction="row" spacing={1.5} alignItems="baseline">
                <Typography variant="h5" className="mono" sx={{ fontWeight: 800 }}>
                  {fmtUsd(token.price_usd)}
                </Typography>
                <DeltaText value={token.price_change_pct_24h} variant="body2" />
              </Stack>
            </Box>
            <Stack direction="row" spacing={0.5}>
              <IconButton
                size="small"
                onClick={() => setWide((v) => !v)}
                aria-label={wide ? 'Réduire' : 'Élargir'}
                sx={{ display: { xs: 'none', sm: 'inline-flex' } }}
              >
                {wide ? <CloseFullscreenIcon fontSize="small" /> : <OpenInFullIcon fontSize="small" />}
              </IconButton>
              <IconButton size="small" onClick={onClose} aria-label="Fermer">
                <CloseIcon fontSize="small" />
              </IconButton>
            </Stack>
          </Stack>

          <Divider />

          <TokenPricePanel token={token} bare />

          {isError ? (
            <EmptyState message="Le dossier n'a pas pu être chargé." />
          ) : isLoading || !data ? (
            <Stack spacing={2}>
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} variant="rectangular" height={120} sx={{ borderRadius: 2 }} />
              ))}
            </Stack>
          ) : (
            <Stack spacing={2.5} divider={<Divider />}>
              <Section title="Décomposition du score">
                <ScoreBreakdown score={data.score} />
              </Section>

              <Section title="Verdict du pipeline">
                <PipelineVerdictPanel verdict={data.pipeline} />
              </Section>

              <Section title={`Décisions des workers · ${token.symbol}`}>
                {data.decisions.length === 0 ? (
                  <EmptyState message="Aucune décision worker sur ce token." />
                ) : (
                  <WorkerDecisionsPanel
                    decisions={data.decisions}
                    loading={false}
                    now={now}
                    bare
                  />
                )}
              </Section>

              <Section title={`News & social · ${token.symbol}`}>
                <NewsPanel news={data.content} loading={false} now={now} bare />
              </Section>

              <Section title="Exposition">
                <TokenExposurePanel exposure={data.exposure} />
              </Section>
            </Stack>
          )}
        </Stack>
      )}
    </Drawer>
  );
}
```

- [ ] **Step 2 : ajouter le mode `bare` à `TokenPricePanel`**

Dans `frontend/src/components/market/TokenPricePanel.tsx`, remplacer le bloc `interface Props` (lignes 39-41) et tout le corps du composant (lignes 43-132) par :

```tsx
interface Props {
  token: MarketToken;
  /** `true` dans le drawer : ni Card ni en-tête, le conteneur les porte déjà. */
  bare?: boolean;
}

export function TokenPricePanel({ token, bare = false }: Props) {
  const [range, setRange] = useState<Range>('1d');

  const { data: prices = [], isLoading } = useQuery({
    queryKey: ['market', 'prices', token.symbol, range],
    queryFn: () => marketApi.prices(token.symbol, range),
    refetchInterval: 60_000,
  });

  const body = (
    <>
      {/* Header — masqué dans le drawer, qui affiche déjà symbole, nom et prix */}
      <Stack
        direction="row"
        justifyContent={bare ? 'flex-end' : 'space-between'}
        alignItems="flex-start"
        flexWrap="wrap"
        gap={1}
        sx={{ mb: 2 }}
      >
        {!bare && (
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              {token.symbol}
              <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                {token.name}
              </Typography>
            </Typography>
            <Typography variant="h5" className="mono" sx={{ fontWeight: 800, mt: 0.25 }}>
              {fmtUsd(token.price_usd)}
            </Typography>
          </Box>
        )}
        <ToggleButtonGroup
          size="small"
          exclusive
          value={range}
          onChange={(_, v: Range | null) => v && setRange(v)}
        >
          {(['1d', '7d', '30d'] as Range[]).map((r) => (
            <ToggleButton key={r} value={r} sx={{ px: 1.5, py: 0.25, fontSize: 12 }}>
              {r}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Stack>

      {/* Stat strip */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(100px, 1fr))',
          gap: 2,
          mb: 2,
          p: 1.5,
          borderRadius: 1.5,
          bgcolor: 'rgba(255,255,255,0.03)',
          border: '1px solid rgba(255,255,255,0.06)',
        }}
      >
        <StatItem
          label="Variation 24h"
          value={<DeltaText value={token.price_change_pct_24h} variant="body2" />}
        />
        <StatItem
          label="Volume 24h"
          value={
            <Typography variant="body2" className="mono">
              {fmtUsdCompact(token.volume_24h_usd)}
            </Typography>
          }
        />
        <StatItem
          label="Liquidité"
          value={
            <Typography variant="body2" className="mono">
              {fmtUsdCompact(token.liquidity_usd)}
            </Typography>
          }
        />
        <StatItem label="Sentiment" value={<SentimentChip score={token.sentiment_score} />} />
      </Box>

      {/* Chart */}
      {isLoading ? (
        <Skeleton variant="rectangular" height={240} sx={{ borderRadius: 1 }} />
      ) : prices.length === 0 ? (
        <EmptyState message="Pas de données de prix disponibles." />
      ) : (
        <PriceAreaChart data={prices} height={240} color="#5b8def" dataKey="price" />
      )}
    </>
  );

  if (bare) return <Box>{body}</Box>;

  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>{body}</CardContent>
    </Card>
  );
}
```

- [ ] **Step 3 : ajouter le mode `bare` à `NewsPanel`**

Dans `frontend/src/components/market/NewsPanel.tsx`, remplacer `interface Props` (lignes 18-22) et le composant exporté (lignes 75-114) par :

```tsx
interface Props {
  news: NewsItem[];
  loading: boolean;
  now: number;
  /** `true` dans le drawer : ni Card ni titre, le conteneur les porte déjà. */
  bare?: boolean;
}
```

```tsx
export function NewsPanel({ news, loading, now, bare = false }: Props) {
  const body = loading ? (
    <Stack spacing={1}>
      {Array.from({ length: 5 }).map((_, i) => (
        <Box key={i} sx={{ py: 1 }}>
          <Skeleton variant="text" height={20} width="90%" />
          <Skeleton variant="text" height={16} width="50%" sx={{ mt: 0.5 }} />
        </Box>
      ))}
    </Stack>
  ) : news.length === 0 ? (
    <EmptyState message="Aucune news disponible." />
  ) : (
    <Box>
      {news.map((item) => (
        <NewsCard key={item.id} item={item} now={now} />
      ))}
    </Box>
  );

  if (bare) return <Box>{body}</Box>;

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 2 }}>
          News importantes
        </Typography>
        {body}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 4 : vérifier la compilation**

```bash
cd frontend && npm run typecheck && npm run test:run
```
Attendu : aucune erreur, tests au vert.

- [ ] **Step 5 : commit**

```bash
git add frontend/src/components/market/
git commit -m "feat(frontend): drawer dossier token"
```

---

## Task 13 : tableau des tokens borné

**Files:**
- Create: `frontend/src/lib/market/tokensView.ts`
- Create: `frontend/src/lib/market/__tests__/tokensView.test.ts`
- Modify: `frontend/src/components/market/TokensTable.tsx`

- [ ] **Step 1 : écrire le test qui échoue**

Créer `frontend/src/lib/market/__tests__/tokensView.test.ts` :

```ts
import { describe, expect, it } from 'vitest';
import { filterAndSortTokens } from '../tokensView';
import type { MarketToken } from '@/lib/types/domain';

function token(over: Partial<MarketToken>): MarketToken {
  return {
    symbol: 'BTC',
    coin_id: 'bitcoin',
    name: 'Bitcoin',
    price_usd: 1,
    price_change_pct_24h: 0,
    volume_24h_usd: 0,
    liquidity_usd: 0,
    market_cap_usd: 0,
    sentiment_score: 0,
    opportunity_score: 0,
    is_trending: false,
    updated_at: '2026-08-01T00:00:00Z',
    ...over,
  };
}

const tokens = [
  token({ symbol: 'BTC', name: 'Bitcoin', opportunity_score: 60 }),
  token({ symbol: 'SOL', name: 'Solana', opportunity_score: 84 }),
  token({ symbol: 'ETH', name: 'Ethereum', opportunity_score: 79 }),
];

describe('filterAndSortTokens', () => {
  it('trie par score décroissant', () => {
    const out = filterAndSortTokens(tokens, '', 'opportunity_score');
    expect(out.map((t) => t.symbol)).toEqual(['SOL', 'ETH', 'BTC']);
  });

  it('filtre sur le symbole, insensible à la casse', () => {
    const out = filterAndSortTokens(tokens, 'sol', 'opportunity_score');
    expect(out.map((t) => t.symbol)).toEqual(['SOL']);
  });

  it('filtre sur le nom', () => {
    const out = filterAndSortTokens(tokens, 'ether', 'opportunity_score');
    expect(out.map((t) => t.symbol)).toEqual(['ETH']);
  });

  it('ne mute pas le tableau reçu', () => {
    const before = tokens.map((t) => t.symbol);
    filterAndSortTokens(tokens, '', 'opportunity_score');
    expect(tokens.map((t) => t.symbol)).toEqual(before);
  });
});
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

```bash
cd frontend && npm run test:run -- tokensView
```
Attendu : échec de résolution du module `../tokensView`.

- [ ] **Step 3 : écrire l'implémentation minimale**

Créer `frontend/src/lib/market/tokensView.ts` :

```ts
import type { MarketToken } from '@/lib/types/domain';

export type TokenSortKey =
  | 'opportunity_score'
  | 'price_change_pct_24h'
  | 'volume_24h_usd'
  | 'liquidity_usd';

export const SORT_LABELS: Record<TokenSortKey, string> = {
  opportunity_score: 'Score',
  price_change_pct_24h: 'Variation 24h',
  volume_24h_usd: 'Volume 24h',
  liquidity_usd: 'Liquidité',
};

/**
 * Vue triée et filtrée du tableau des tokens.
 *
 * Le filtre est client : la liste entière est déjà en mémoire (le tableau est
 * alimenté par un seul `GET /market/tokens`), donc chercher n'appelle pas le
 * réseau. Rend toujours un nouveau tableau — `Array.sort` mute en place, et la
 * source vient d'un cache TanStack Query qu'on ne doit pas réordonner.
 */
export function filterAndSortTokens(
  tokens: MarketToken[],
  query: string,
  sortKey: TokenSortKey,
): MarketToken[] {
  const q = query.trim().toLowerCase();
  const filtered = q
    ? tokens.filter(
        (t) =>
          t.symbol.toLowerCase().includes(q) || t.name.toLowerCase().includes(q),
      )
    : tokens;
  return [...filtered].sort((a, b) => b[sortKey] - a[sortKey]);
}
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

```bash
cd frontend && npm run test:run -- tokensView
```
Attendu : 4 passed.

- [ ] **Step 5 : borner le tableau**

Dans `frontend/src/components/market/TokensTable.tsx`, remplacer le composant exporté (lignes 122-169) par :

```tsx
/** 15 lignes visibles : au-delà, le tableau repousse le reste de la page hors
 *  écran — le défaut que cette refonte corrige. `autoHeight` est retiré pour
 *  que la hauteur ne dépende jamais du nombre de tokens suivis. */
const VISIBLE_ROWS = 15;
const ROW_HEIGHT = 56;
const HEADER_HEIGHT = 56;
const GRID_HEIGHT = HEADER_HEIGHT + VISIBLE_ROWS * ROW_HEIGHT;

export function TokensTable({ tokens, loading, selectedSymbol, onSelect }: Props) {
  const [query, setQuery] = useState('');
  const [sortKey, setSortKey] = useState<TokenSortKey>('opportunity_score');
  const [showAll, setShowAll] = useState(false);

  const view = useMemo(
    () => filterAndSortTokens(tokens, query, sortKey),
    [tokens, query, sortKey],
  );
  const rows = showAll || query ? view : view.slice(0, VISIBLE_ROWS);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} variant="rectangular" height={52} sx={{ borderRadius: 1 }} />
        ))}
      </Box>
    );
  }

  if (tokens.length === 0) {
    return <EmptyState message="Aucun token disponible." />;
  }

  return (
    <Box>
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
        sx={{ mb: 1.5 }}
      >
        <TextField
          size="small"
          placeholder="Rechercher un token…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          sx={{ minWidth: 200 }}
        />
        <TextField
          size="small"
          select
          label="Trier par"
          value={sortKey}
          onChange={(e) => setSortKey(e.target.value as TokenSortKey)}
          sx={{ minWidth: 160 }}
        >
          {(Object.keys(SORT_LABELS) as TokenSortKey[]).map((k) => (
            <MenuItem key={k} value={k}>
              {SORT_LABELS[k]}
            </MenuItem>
          ))}
        </TextField>
        <Box sx={{ flex: 1 }} />
        <Typography variant="caption" color="text.secondary">
          {rows.length} sur {tokens.length}
        </Typography>
        {!query && tokens.length > VISIBLE_ROWS && (
          <Button size="small" onClick={() => setShowAll((v) => !v)}>
            {showAll ? 'Réduire' : `Voir les ${tokens.length}`}
          </Button>
        )}
      </Stack>

      {/* Hauteur constante : en mode « voir tout », c'est la grille qui scrolle
          en interne, jamais la page. */}
      <Box sx={{ height: GRID_HEIGHT }}>
        <DataGrid<MarketToken>
          rows={rows}
          columns={columns}
          getRowId={(row) => row.symbol}
          rowHeight={ROW_HEIGHT}
          columnHeaderHeight={HEADER_HEIGHT}
          density="compact"
          disableColumnMenu
          hideFooter
          hideFooterSelectedRowCount
          rowSelectionModel={selectedSymbol ? [selectedSymbol] : []}
          onRowClick={(params: GridRowParams<MarketToken>) => onSelect(params.row.symbol)}
          sx={{
            border: 'none',
            '& .MuiDataGrid-row': { cursor: 'pointer' },
            '& .MuiDataGrid-row.Mui-selected': { bgcolor: 'rgba(91,141,239,0.12)' },
            '& .MuiDataGrid-row:hover': { bgcolor: 'rgba(255,255,255,0.04)' },
            '& .MuiDataGrid-columnHeaders': {
              borderBottom: '1px solid rgba(255,255,255,0.08)',
            },
            '& .MuiDataGrid-cell': {
              borderBottom: '1px solid rgba(255,255,255,0.04)',
            },
          }}
        />
      </Box>
    </Box>
  );
}
```

Mettre à jour les imports en tête du fichier :

```tsx
import { useMemo, useState } from 'react';
import {
  Box,
  Button,
  Chip,
  MenuItem,
  Skeleton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import { DataGrid, type GridColDef, type GridRowParams } from '@mui/x-data-grid';
import WhatshotIcon from '@mui/icons-material/Whatshot';
import type { MarketToken } from '@/lib/types/domain';
import { fmtUsd, fmtUsdCompact } from '@/lib/format';
import { DeltaText, ScoreChip, SentimentChip, EmptyState } from '@/components/common';
import {
  SORT_LABELS,
  filterAndSortTokens,
  type TokenSortKey,
} from '@/lib/market/tokensView';
```

- [ ] **Step 6 : réduire les colonnes sur petit écran**

Huit colonnes ne tiennent pas sous 900 px : la grille scrolle horizontalement et le symbole sort du champ. Ajouter dans `TokensTable`, avant le `return` :

```tsx
  // Sur petit écran, seules les quatre colonnes qui servent au balayage
  // survivent — le détail est de toute façon dans le drawer.
  const compact = useMediaQuery(theme.breakpoints.down('md'));
  const columnVisibilityModel = compact
    ? {
        volume_24h_usd: false,
        liquidity_usd: false,
        sentiment_score: false,
        is_trending: false,
      }
    : {};
```

Passer la prop à la grille, juste après `columns={columns}` :

```tsx
          columnVisibilityModel={columnVisibilityModel}
```

et compléter les imports :

```tsx
import { useMediaQuery, useTheme } from '@mui/material';
```

avec, en tête du composant :

```tsx
  const theme = useTheme();
```

- [ ] **Step 7 : vérifier**

```bash
cd frontend && npm run typecheck && npm run test:run
```
Attendu : aucune erreur, tests au vert.

- [ ] **Step 8 : commit**

```bash
git add frontend/src/lib/market/ frontend/src/components/market/TokensTable.tsx
git commit -m "feat(frontend): tableau des tokens borné à 15 lignes, recherche et tri"
```

---

## Task 14 : recomposition de la page

**Files:**
- Modify: `frontend/src/app/(app)/market/page.tsx`

- [ ] **Step 1 : réécrire la page**

Remplacer intégralement `frontend/src/app/(app)/market/page.tsx` par :

```tsx
'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Box, Card, CardContent, Typography } from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api/endpoints';
import { PageHeader } from '@/components/common';
import { TokensTable } from '@/components/market/TokensTable';
import { WorkerDecisionsPanel } from '@/components/market/WorkerDecisionsPanel';
import { NewsPanel } from '@/components/market/NewsPanel';
import { TokenDossierDrawer } from '@/components/market/TokenDossierDrawer';

/** Hauteur des deux colonnes de flux : elles scrollent en interne pour que la
 *  page garde une hauteur constante, quel que soit le volume de contenu. */
const FEED_HEIGHT = 420;

export default function MarketPage() {
  const router = useRouter();
  const params = useSearchParams();
  // La sélection vit dans l'URL : le dossier devient partageable, et le bouton
  // retour du navigateur le referme au lieu de quitter la page.
  const selectedSymbol = params.get('token');

  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const { data: tokens = [], isLoading: tokensLoading } = useQuery({
    queryKey: ['market', 'tokens'],
    queryFn: marketApi.tokens,
    refetchInterval: 30_000,
  });

  const { data: news = [], isLoading: newsLoading } = useQuery({
    queryKey: ['market', 'news'],
    queryFn: () => marketApi.news(20),
    refetchInterval: 60_000,
  });

  const { data: decisions = [], isLoading: decisionsLoading } = useQuery({
    queryKey: ['market', 'decisions'],
    queryFn: () => marketApi.decisions(30),
    refetchInterval: 30_000,
  });

  const select = useCallback(
    (symbol: string) => router.push(`/market?token=${symbol}`, { scroll: false }),
    [router],
  );
  const close = useCallback(
    () => router.push('/market', { scroll: false }),
    [router],
  );

  const selectedToken = tokens.find((t) => t.symbol === selectedSymbol) ?? null;

  return (
    <Box>
      <PageHeader
        title="Intelligence de marché"
        subtitle="Balayez le marché, cliquez un token pour ouvrir son dossier complet"
      />

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" sx={{ mb: 2 }}>
            Tokens surveillés
          </Typography>
          <TokensTable
            tokens={tokens}
            loading={tokensLoading}
            selectedSymbol={selectedSymbol}
            onSelect={select}
          />
        </CardContent>
      </Card>

      {/* Flux globaux. Le contenu filtré par token vit dans le drawer — le
          garder aussi ici ferait deux chemins pour la même donnée. */}
      <Box
        sx={{
          display: 'grid',
          gap: 3,
          gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' },
          alignItems: 'start',
        }}
      >
        <Box sx={{ maxHeight: FEED_HEIGHT, overflowY: 'auto' }}>
          <WorkerDecisionsPanel
            decisions={decisions}
            loading={decisionsLoading}
            now={now}
          />
        </Box>
        <Box sx={{ maxHeight: FEED_HEIGHT, overflowY: 'auto' }}>
          <NewsPanel news={news} loading={newsLoading} now={now} />
        </Box>
      </Box>

      <TokenDossierDrawer token={selectedToken} onClose={close} now={now} />
    </Box>
  );
}
```

- [ ] **Step 2 : vérifier la compilation et le build**

```bash
cd frontend && npm run typecheck && npm run lint && npm run build
```
Attendu : aucune erreur. `useSearchParams` impose une frontière `Suspense` en App Router ; si le build s'en plaint, envelopper le corps de la page dans un composant enfant rendu sous `<Suspense fallback={null}>`.

- [ ] **Step 3 : vérifier à l'écran, en mock**

```bash
cd frontend && NEXT_PUBLIC_USE_MOCK=1 npm run dev
```

Ouvrir `http://localhost:3000/market` et vérifier :
- la page ne dépasse pas un écran en 1920×1080 ;
- le tableau affiche 15 lignes et un bouton « Voir les N » ;
- la recherche filtre sans recharger ;
- un clic sur une ligne ouvre le drawer, et l'URL devient `/market?token=…` ;
- dans le drawer, l'axe **Fondamentaux** affiche `—` et non `0` ;
- le retour navigateur referme le drawer.

- [ ] **Step 4 : commit**

```bash
git add "frontend/src/app/(app)/market/page.tsx"
git commit -m "feat(frontend): /market tient dans un écran, dossier token en drawer"
```

---

## Task 15 : vérification finale

**Files:** aucun

- [ ] **Step 1 : suite backend complète**

```bash
pytest tests/ -q
```
Attendu : tout passe, aucune régression.

- [ ] **Step 2 : lint et types backend**

```bash
make lint
```
Attendu : ruff, black et mypy sans erreur.

- [ ] **Step 3 : suite frontend**

```bash
cd frontend && npm run test:run && npm run typecheck && npm run lint && npm run build
```
Attendu : tout vert.

- [ ] **Step 4 : vérifier que `LiveEventStream` a bien quitté `/market`**

```bash
grep -rn "LiveEventStream" "frontend/src/app/(app)/market/"
```
Attendu : aucun résultat. Le composant reste utilisé par `/command`, ce qui est son seul emplacement légitime.

- [ ] **Step 5 : harnais live (si une base est joignable)**

```bash
python scripts/verify_read_live.py
```
Attendu : `all read endpoints conform to CONTRACT`, `market/dossier` inclus.

---

## Notes d'exécution

- **Ordre des tâches.** 1 → 4 (backend complet et verrouillé), puis 5 → 7 (types, mock, outillage), puis 8 → 14 (composants puis page). Les tâches 8, 9 et 10 sont indépendantes entre elles et peuvent partir en parallèle.
- **Pas de migration.** Aucun changement de schéma : tout ce qu'affiche le drawer est déjà en base.
- **`make lint` ne couvre pas le frontend.** `npm run typecheck` et `npm run lint` doivent être lancés depuis `frontend/`, comme indiqué dans chaque tâche.
