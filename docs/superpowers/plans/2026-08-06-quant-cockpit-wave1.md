# Quant Cockpit — Vague 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Livrer les trois zones de la vague 1 du Quant Cockpit — bandeau régime global, Decision Inspector URL-adressable, page `/journal` — avec leurs quatre endpoints de lecture dans api-gateway.

**Architecture:** api-gateway gagne un accès Redis **en lecture seule** (clés `features:*` et `market:regime`, celles que le pipeline consomme déjà) et quatre endpoints assemblés par des modules purs façon `dossier.py`, ajoutés au manifeste `read_contract.py`. Le frontend monte deux composants globaux dans `AppShell` (strip + inspecteur) et une nouvelle page `/journal`, tous nourris par le mock BFF en dev.

**Tech Stack:** FastAPI + SQLAlchemy async + redis-py (backend) ; Next 15 App Router, MUI 6, TanStack Query 5, vitest (frontend).

**Spec :** `docs/superpowers/specs/2026-08-06-quant-cockpit-design.md`

---

## Écarts à la spec, constatés dans le code (à reporter dans la spec — Task 0)

1. **Funding/OI ne vivent qu'en Redis** (`features:{SYM}`, TTL 900 s, réécrits ~300 s) — aucune table SQL. D'où le câblage Redis lecture seule dans api-gateway (précédent : risk-engine et trading-engine lisent déjà `features:*` par clé littérale).
2. **La dominance BTC n'est pas collectée.** On la dérive de `prices.market_cap_usd` sur l'univers suivi (~200 tokens CoinGecko) — approximation nommée dans `detail`.
3. **Le seuil funding de la spec (0,02 %/8h = 0.0002 en fraction) est au-delà du p95 mesuré** (p95 = 0.000159 sur 854 perps). Recalibré à ±0.0001 (≈ 2× la médiane).
4. **Le funding vient de Binance uniquement** (le collecteur Kraken Futures est spécifié, pas implémenté).
5. **L'attribution par les 8 axes est impossible sur le journal** : le breakdown n'existe que dans `decisions` (score ≥ seuil — quasi vide à seuil 101). L'attribution v1 porte sur les **4 facteurs de triage Haiku** (`decision_journal.factors`), étiquetée comme telle.
6. `/decisions/{id}/explain` réutilise `assemble_trace` via le handler `/trace` existant (DRY) ; il reste un seul appel frontend.

## Structure de fichiers

**Backend (créés)** : `services/api-gateway/app/regime.py` (règles pures), `services/api-gateway/app/regime_api.py` (route + fetchers + cache 30 s), `services/api-gateway/app/explain.py` (assemblage pur), `services/api-gateway/app/journal_calibration.py` (math pure). **Modifiés** : `app/main.py` (Cache + router), `app/read_api.py` (route explain), `app/journal_api.py` (3 routes), `app/read_contract.py`, `tests/test_read_contract.py`, `tests/conftest.py`, `scripts/verify_read_live.py`. **Tests créés** : `tests/test_regime_rules.py`, `tests/test_explain_assembly.py`, `tests/test_journal_calibration.py`.

**Frontend (créés)** : `src/lib/types/{regime,explain,journal}.ts`, `src/lib/hooks/useDecisionParam.ts`, `src/components/layout/RegimeStrip.tsx`, `src/components/inspector/DecisionInspector.tsx`, `src/app/(app)/journal/page.tsx`, composants `src/components/journal/{JournalTable,CalibrationPanel,AttributionPanel}.tsx`, mocks `src/lib/mock/{regime,journal}.ts`, routes mock `src/app/api/mock/market/regime/route.ts`, `.../decisions/[id]/explain/route.ts`, `.../systems/journal/{decisions,calibration,attribution}/route.ts`, tests `__tests__`. **Modifiés** : `endpoints.ts`, `navItems.ts`, `AppShell.tsx`, `AiDecisionFeed.tsx`.

**Règles transverses** (chaque tâche) : inconnu = `null` = « — », jamais un faux zéro ; api-gateway n'importe **jamais** decision-engine ; l'inspecteur importe `SCORE_AXES` (pas de 4ᵉ copie) ; tables bornées, jamais `autoHeight` ; lancer les commandes depuis la racine du repo (backend) ou `frontend/` (npm).

---

### Task 0 : Amender la spec et CLAUDE.md (réalité des données)

**Files:**
- Modify: `docs/superpowers/specs/2026-08-06-quant-cockpit-design.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1 : Corriger la table des drivers dans la spec** — remplacer les lignes `funding`, `btc_dominance` du tableau § RegimeStrip par :

```markdown
| `funding` | agrégat funding Binance (`features:*` Redis ; Kraken Futures quand le collecteur existera) | médiane > +0.0001 (fraction/8h, ≈2× la médiane mesurée) → crowded-long (contrarien : bearish) |
| `btc_dominance` | dérivée de `prices.market_cap_usd` sur l'univers suivi (~200 tokens) — approximation nommée dans `detail` | dérive 7 j > +0.5 pt → risk-off des alts |
```

- [ ] **Step 2 : Corriger le panneau attribution dans la spec** — dans § `/journal`, remplacer la phrase « corrélation simple entre contribution d'un axe et résultat simulé » par : « corrélation simple entre chaque **facteur de triage Haiku** (`momentum`/`volume`/`sentiment`/`liquidity`, seuls présents pour toutes les décisions du journal) et le résultat simulé ; l'attribution par les 8 axes exige des décisions passées et viendra quand le seuil laissera passer un échantillon. » Ajouter dans § Backend la phrase : « api-gateway gagne un accès Redis **en lecture seule** (`features:*`, `market:regime`) — précédent existant chez risk-engine et trading-engine. »

- [ ] **Step 3 : CLAUDE.md** — dans la table du plan de contrôle, ligne api-gateway, colonne State, remplacer `persists → Postgres (Signal/Decision/Trade)` par `persists → Postgres; lit Redis features:*/market:regime (RO)`. Remplacer les deux mentions « seven axes »/« sept axes » du paragraphe scoring par « huit axes » (WEIGHTS en a 8 depuis `developer_activity`).

- [ ] **Step 4 : Commit**

```bash
git add docs/superpowers/specs/2026-08-06-quant-cockpit-design.md CLAUDE.md
git commit -m "docs(spec): quant cockpit, corrections constatees dans le code"
```

---

### Task 1 : Moteur de règles régime (pur)

**Files:**
- Create: `services/api-gateway/app/regime.py`
- Test: `tests/test_regime_rules.py`

- [ ] **Step 1 : Écrire les tests qui échouent**

```python
"""Rules engine for GET /market/regime — pure, mirrors dossier.py testing style."""

from service_modules import load_service_module

regime = load_service_module("api-gateway", "regime")


def test_all_absent_yields_null_regime() -> None:
    drivers = [
        regime.funding_driver(None),
        regime.oi_delta_driver(None, None),
        regime.sentiment_driver(None, None),
        regime.dominance_driver(None, None, None),
        regime.breadth_driver(None, 0, None),
    ]
    out = regime.build_regime(drivers, computed_at="2026-08-06T00:00:00+00:00")
    assert out["regime"] is None
    assert out["confidence"] == 0.0          # zéro mesuré : 0/5 drivers présents
    assert len(out["drivers"]) == 5
    assert all(d["state"] is None and d["value"] is None for d in out["drivers"])


def test_funding_is_contrarian() -> None:
    assert regime.funding_driver(0.0002)["state"] == "bearish"    # crowded-long
    assert regime.funding_driver(-0.0002)["state"] == "bullish"   # crowded-short
    assert regime.funding_driver(0.0001)["state"] == "neutral"    # borne : strictement >
    assert regime.funding_driver(0.00005)["state"] == "neutral"


def test_oi_needs_price_direction() -> None:
    d = regime.oi_delta_driver(8.0, None)
    assert d["value"] == 8.0 and d["state"] is None               # mesuré mais non votable
    assert regime.oi_delta_driver(8.0, 2.0)["state"] == "bullish"
    assert regime.oi_delta_driver(8.0, -2.0)["state"] == "bearish"
    assert regime.oi_delta_driver(-8.0, 2.0)["state"] == "neutral"  # délevier
    assert regime.oi_delta_driver(1.0, 2.0)["state"] == "neutral"


def test_min_drivers_gate() -> None:
    two = [
        regime.funding_driver(-0.0002),
        regime.sentiment_driver(0.5, None),
        regime.oi_delta_driver(None, None),
        regime.dominance_driver(None, None, None),
        regime.breadth_driver(None, 0, None),
    ]
    assert regime.build_regime(two, computed_at="t")["regime"] is None
    assert regime.build_regime(two, computed_at="t")["confidence"] == 0.4


def test_net_vote_mapping() -> None:
    def build(states: list[str | None]) -> dict:
        keys = ["funding", "oi_delta", "market_sentiment", "btc_dominance", "breadth"]
        drivers = [
            {"key": k, "value": 1.0 if s else None, "state": s, "detail": "", "as_of": None}
            for k, s in zip(keys, states, strict=True)
        ]
        return regime.build_regime(drivers, computed_at="t")

    assert build(["bullish"] * 3 + ["neutral"] * 2)["regime"] == "RISK_ON"
    assert build(["bullish", "bullish", "neutral", "neutral", "neutral"])["regime"] == "ACCUMULATION"
    assert build(["bullish", "bearish", "neutral", "neutral", "neutral"])["regime"] == "NEUTRAL"
    assert build(["bearish", "bearish", "neutral", "neutral", "neutral"])["regime"] == "DISTRIBUTION"
    assert build(["bearish"] * 3 + ["neutral"] * 2)["regime"] == "RISK_OFF"


def test_detail_is_auditable() -> None:
    d = regime.funding_driver(0.0002)
    assert "0.0001" in d["detail"]            # le seuil appliqué est restitué
    dom = regime.dominance_driver(54.1, 53.2, "2026-08-06T00:00:00+00:00")
    assert "univers suivi" in dom["detail"]   # l'approximation est nommée
    assert dom["value"] == 0.9                # la valeur est la dérive en points
```

- [ ] **Step 2 : Vérifier l'échec** — `pytest tests/test_regime_rules.py -q` → FAIL (`FileNotFoundError` ou `AttributeError`, module absent).

- [ ] **Step 3 : Implémenter `services/api-gateway/app/regime.py`**

```python
"""Pure rules for GET /market/regime — no I/O, mirror of dossier.py.

Each driver votes bullish/bearish/neutral from transparent thresholds; the
`detail` string restitutes the rule and raw value so the strip's popover can
show *why*. The project's axis rule applies unchanged: an unmeasured driver is
value None / state None and is excluded — never scored neutral. Fewer than
MIN_DRIVERS measured drivers means regime None: a guessed regime is worth less
than no regime.
"""

from __future__ import annotations

from typing import Any

DRIVER_KEYS: tuple[str, ...] = (
    "funding",
    "oi_delta",
    "market_sentiment",
    "btc_dominance",
    "breadth",
)

#: Funding is a raw 8h fraction (0.0001 == 0.01%/8h). Distribution measured on
#: 854 Binance perps (see 2026-07-31 derivatives spec): p5 −0.000156, median
#: +0.000050, p95 +0.000159. ±0.0001 ≈ 2× median — crossed often enough to be
#: a signal, rarely enough to mean crowding. The quant-cockpit spec's first
#: guess (0.0002) sits past p95 and would almost never fire.
FUNDING_CROWDED = 0.0001
OI_DELTA_PCT = 5.0
SENTIMENT_BAND = 0.2
DOMINANCE_DRIFT_PTS = 0.5
BREADTH_HIGH = 0.60
BREADTH_LOW = 0.40
MIN_DRIVERS = 3


def _driver(
    key: str,
    value: float | None,
    state: str | None,
    detail: str,
    as_of: str | None,
) -> dict[str, Any]:
    return {"key": key, "value": value, "state": state, "detail": detail, "as_of": as_of}


def funding_driver(median_8h: float | None, as_of: str | None = None) -> dict[str, Any]:
    if median_8h is None:
        return _driver("funding", None, None, "médiane funding 8h indisponible", as_of)
    if median_8h > FUNDING_CROWDED:
        state, verdict = "bearish", "crowded-long"
    elif median_8h < -FUNDING_CROWDED:
        state, verdict = "bullish", "crowded-short"
    else:
        state, verdict = "neutral", "équilibré"
    detail = (
        f"médiane funding {median_8h:+.6f}/8h (Binance, univers suivi) : {verdict}. "
        f"Contrarien : > +{FUNDING_CROWDED} → bearish, < -{FUNDING_CROWDED} → bullish."
    )
    return _driver("funding", median_8h, state, detail, as_of)


def oi_delta_driver(
    median_delta_pct: float | None,
    btc_price_change_pct: float | None,
    as_of: str | None = None,
) -> dict[str, Any]:
    if median_delta_pct is None:
        return _driver("oi_delta", None, None, "delta OI 24h indisponible (majors seulement)", as_of)
    if median_delta_pct > OI_DELTA_PCT:
        if btc_price_change_pct is None:
            return _driver(
                "oi_delta", median_delta_pct, None,
                f"OI +{median_delta_pct:.1f}% mais direction prix BTC inconnue : vote impossible",
                as_of,
            )
        state = "bullish" if btc_price_change_pct >= 0 else "bearish"
        verdict = "levier suit la hausse" if state == "bullish" else "build-up short"
    elif median_delta_pct < -OI_DELTA_PCT:
        state, verdict = "neutral", "délevier"
    else:
        state, verdict = "neutral", "stable"
    detail = (
        f"médiane ΔOI 24h {median_delta_pct:+.1f}% (majors Binance), "
        f"prix BTC 24h {btc_price_change_pct:+.1f}%" if btc_price_change_pct is not None
        else f"médiane ΔOI 24h {median_delta_pct:+.1f}% (majors Binance)"
    )
    return _driver("oi_delta", median_delta_pct, state, f"{detail} : {verdict}. Seuil ±{OI_DELTA_PCT}%.", as_of)


def sentiment_driver(score: float | None, as_of: str | None) -> dict[str, Any]:
    if score is None:
        return _driver(
            "market_sentiment", None, None,
            "lecture market-wide indisponible (cadence irrégulière mesurée : médiane 19 min, p95 71 min)",
            as_of,
        )
    if score > SENTIMENT_BAND:
        state = "bullish"
    elif score < -SENTIMENT_BAND:
        state = "bearish"
    else:
        state = "neutral"
    detail = (
        f"sentiment market-wide {score:+.2f} [-1,1] (contenu crypto sans ticker). "
        f"Bande neutre ±{SENTIMENT_BAND}."
    )
    return _driver("market_sentiment", score, state, detail, as_of)


def dominance_driver(
    now_pct: float | None,
    week_ago_pct: float | None,
    as_of: str | None,
) -> dict[str, Any]:
    if now_pct is None or week_ago_pct is None:
        return _driver("btc_dominance", None, None, "dominance indisponible", as_of)
    delta = round(now_pct - week_ago_pct, 2)
    if delta > DOMINANCE_DRIFT_PTS:
        state, verdict = "bearish", "rotation vers BTC, risk-off des alts"
    elif delta < -DOMINANCE_DRIFT_PTS:
        state, verdict = "bullish", "rotation vers les alts"
    else:
        state, verdict = "neutral", "stable"
    detail = (
        f"BTC.D {now_pct:.1f}% (univers suivi ~200 tokens, pas le marché entier), "
        f"dérive 7j {delta:+.2f} pt : {verdict}. Seuil ±{DOMINANCE_DRIFT_PTS} pt."
    )
    return _driver("btc_dominance", delta, state, detail, as_of)


def breadth_driver(
    share_positive: float | None,
    n_symbols: int,
    as_of: str | None,
) -> dict[str, Any]:
    if share_positive is None:
        return _driver("breadth", None, None, "breadth indisponible", as_of)
    if share_positive > BREADTH_HIGH:
        state = "bullish"
    elif share_positive < BREADTH_LOW:
        state = "bearish"
    else:
        state = "neutral"
    detail = (
        f"{share_positive:.0%} des {n_symbols} tokens suivis en hausse sur 24h. "
        f"Seuils : > {BREADTH_HIGH:.0%} bullish, < {BREADTH_LOW:.0%} bearish."
    )
    return _driver("breadth", share_positive, state, detail, as_of)


def build_regime(drivers: list[dict[str, Any]], *, computed_at: str) -> dict[str, Any]:
    measured = [d for d in drivers if d["state"] is not None]
    confidence = round(len(measured) / len(DRIVER_KEYS), 2)
    if len(measured) < MIN_DRIVERS:
        label = None
    else:
        net = sum(1 for d in measured if d["state"] == "bullish") - sum(
            1 for d in measured if d["state"] == "bearish"
        )
        if net >= 3:
            label = "RISK_ON"
        elif net >= 1:
            label = "ACCUMULATION"
        elif net <= -3:
            label = "RISK_OFF"
        elif net <= -1:
            label = "DISTRIBUTION"
        else:
            label = "NEUTRAL"
    return {
        "regime": label,
        "confidence": confidence,
        "drivers": drivers,
        "computed_at": computed_at,
    }
```

- [ ] **Step 4 : Vérifier** — `pytest tests/test_regime_rules.py -q` → tous PASS. Puis `make lint` (mypy strict : les annotations ci-dessus sont complètes).

Note signature `build_regime(drivers, computed_at=...)` : les tests l'appellent en keyword — conforme.

- [ ] **Step 5 : Commit**

```bash
git add services/api-gateway/app/regime.py tests/test_regime_rules.py
git commit -m "feat(api-gateway): moteur de regles pur pour /market/regime"
```

---

### Task 2 : Câblage Redis + route `/market/regime`

**Files:**
- Create: `services/api-gateway/app/regime_api.py`
- Modify: `services/api-gateway/app/main.py` (startup/shutdown + router)
- Modify: `services/api-gateway/app/read_contract.py`
- Modify: `tests/test_read_contract.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1 : Tests de contrat qui échouent** — dans `tests/test_read_contract.py`, après le chargement des modules existants, ajouter :

```python
regime_api = load_service_module("api-gateway", "regime_api")
```

et, **avant** `test_every_contract_entry_is_actually_asserted`, ajouter :

```python
class _FakeCacheClient:
    async def mget(self, keys):  # noqa: ANN001, ANN201
        return [None] * len(keys)


class _FakeCache:
    client = _FakeCacheClient()

    async def get_json(self, _key):  # noqa: ANN001, ANN201
        return None


async def test_market_regime_contract() -> None:
    regime_api.REGIME_CACHE.clear()
    resp = await regime_api.market_regime(session=_FakeSession(8), cache=_FakeCache())
    _assert_exact_keys("market/regime", resp)
    assert len(resp["drivers"]) == 5
    for d in resp["drivers"]:
        _assert_exact_keys("market/regime.drivers[]", d)
    assert resp["regime"] is None          # rien de mesuré → pas de régime deviné
```

Et dans `services/api-gateway/app/read_contract.py`, ajouter au dict `CONTRACT` :

```python
    "market/regime": {"regime", "confidence", "drivers", "computed_at"},
    "market/regime.drivers[]": {"key", "value", "state", "detail", "as_of"},
```

- [ ] **Step 2 : Vérifier l'échec** — `pytest tests/test_read_contract.py -q` → FAIL (module `regime_api` absent).

- [ ] **Step 3 : Implémenter `services/api-gateway/app/regime_api.py`**

```python
"""GET /market/regime — gathering + route; the rules live in regime.py (pure).

api-gateway reads two Redis key families here, read-only: features:{SYM} and
market:regime — the exact keys the pipeline itself consumes (written by
ai-worker-haiku, already read by risk-engine and trading-engine). Slow context
(dominance, breadth) comes from Postgres `prices`. An upstream failure yields
an absent driver — never a confident zero.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cmi_common.cache import Cache
from cmi_common.db import Price
from cmi_common.db.models import ContentSentimentAgg
from cmi_common.db.universe import priced_symbols

from . import regime
from .routers import get_session_dep

logger = logging.getLogger(__name__)
router = APIRouter(tags=["regime"])

CACHE_TTL_S = 30.0


def get_cache_dep(request: Request) -> Cache:
    # Bound in main.py: app.state.cache = Cache(settings.redis)
    return request.app.state.cache


class _RegimeCache:
    """Single-value TTL cache, same shape as systems_pipeline._StageCache."""

    def __init__(self, ttl_s: float = CACHE_TTL_S) -> None:
        self._ttl = ttl_s
        self._entry: tuple[float, dict[str, Any]] | None = None

    def fresh(self, now: float) -> dict[str, Any] | None:
        if self._entry is None:
            return None
        at, value = self._entry
        return dict(value) if now - at < self._ttl else None

    def put(self, now: float, value: dict[str, Any]) -> None:
        self._entry = (now, value)

    def clear(self) -> None:
        self._entry = None


REGIME_CACHE = _RegimeCache()


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


async def _feature_rows(cache: Cache, symbols: set[str]) -> list[dict[str, Any]]:
    keys = [f"features:{s}" for s in sorted(symbols)]
    if not keys:
        return []
    raw = await cache.client.mget(keys)
    out: list[dict[str, Any]] = []
    for item in raw:
        if not item:
            continue
        try:
            parsed = json.loads(item)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def _floats(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [float(r[key]) for r in rows if isinstance(r.get(key), (int, float))]


async def _market_sentiment(
    cache: Cache, session: AsyncSession
) -> tuple[float | None, str | None]:
    data = await cache.get_json("market:regime") or {}
    value = data.get("sentiment_score")
    stmt = select(func.max(ContentSentimentAgg.bucket_start)).where(
        ContentSentimentAgg.symbol == "MARKET"
    )
    row = (await session.execute(stmt)).first()
    as_of = _iso(row[0]) if row and row[0] else None
    return (float(value) if isinstance(value, (int, float)) else None), as_of


async def _dominance_at(
    session: AsyncSession, upper: datetime
) -> tuple[float | None, str | None]:
    lower = upper - timedelta(hours=24)
    sub = (
        select(Price.symbol, func.max(Price.time).label("t"))
        .where(
            Price.time >= lower,
            Price.time <= upper,
            Price.market_cap_usd.is_not(None),
        )
        .group_by(Price.symbol)
        .subquery()
    )
    stmt = select(Price.symbol, Price.market_cap_usd, Price.time).join(
        sub, (Price.symbol == sub.c.symbol) & (Price.time == sub.c.t)
    )
    rows = (await session.execute(stmt)).all()
    caps = [(s, float(mc)) for s, mc, _ in rows if mc]
    total = sum(mc for _, mc in caps)
    btc = next((mc for s, mc in caps if s == "BTC"), None)
    if not caps or not total or btc is None:
        return None, None
    return round(100 * btc / total, 2), _iso(max(t for _, _, t in rows))


async def _breadth(
    session: AsyncSession,
) -> tuple[float | None, int, str | None]:
    upper = datetime.now(tz=UTC)
    sub = (
        select(Price.symbol, func.max(Price.time).label("t"))
        .where(
            Price.time >= upper - timedelta(hours=24),
            Price.price_change_pct_24h.is_not(None),
        )
        .group_by(Price.symbol)
        .subquery()
    )
    stmt = select(Price.price_change_pct_24h, Price.time).join(
        sub, (Price.symbol == sub.c.symbol) & (Price.time == sub.c.t)
    )
    rows = (await session.execute(stmt)).all()
    vals = [float(p) for p, _ in rows if p is not None]
    if not vals:
        return None, 0, None
    share = sum(1 for v in vals if v > 0) / len(vals)
    return round(share, 4), len(vals), _iso(max(t for _, t in rows))


@router.get("/market/regime")
async def market_regime(
    session: AsyncSession = Depends(get_session_dep),
    cache: Cache = Depends(get_cache_dep),
) -> dict:
    now = time.monotonic()
    hit = REGIME_CACHE.fresh(now)
    if hit is not None:
        return hit

    # Every gather is guarded: a failed upstream yields an absent driver.
    feats: list[dict[str, Any]] = []
    btc_feat: dict[str, Any] = {}
    try:
        symbols = await priced_symbols(session)
        feats = await _feature_rows(cache, symbols)
        btc_feat = await cache.get_json("features:BTC") or {}
    except Exception:
        logger.exception("regime: live features unavailable")

    sent_value: float | None = None
    sent_as_of: str | None = None
    try:
        sent_value, sent_as_of = await _market_sentiment(cache, session)
    except Exception:
        logger.exception("regime: market sentiment unavailable")

    dom_now: float | None = None
    dom_week: float | None = None
    dom_as_of: str | None = None
    try:
        upper = datetime.now(tz=UTC)
        dom_now, dom_as_of = await _dominance_at(session, upper)
        dom_week, _ = await _dominance_at(session, upper - timedelta(days=7))
    except Exception:
        logger.exception("regime: dominance unavailable")

    breadth_share: float | None = None
    breadth_n = 0
    breadth_as_of: str | None = None
    try:
        breadth_share, breadth_n, breadth_as_of = await _breadth(session)
    except Exception:
        logger.exception("regime: breadth unavailable")

    btc_change = btc_feat.get("price_change_pct_24h")
    drivers = [
        regime.funding_driver(_median(_floats(feats, "funding_rate_8h"))),
        regime.oi_delta_driver(
            _median(_floats(feats, "open_interest_change_pct_24h")),
            float(btc_change) if isinstance(btc_change, (int, float)) else None,
        ),
        regime.sentiment_driver(sent_value, sent_as_of),
        regime.dominance_driver(dom_now, dom_week, dom_as_of),
        regime.breadth_driver(breadth_share, breadth_n, breadth_as_of),
    ]
    payload = regime.build_regime(
        drivers, computed_at=datetime.now(tz=UTC).isoformat()
    )
    REGIME_CACHE.put(now, payload)
    return payload
```

- [ ] **Step 4 : Câbler dans `services/api-gateway/app/main.py`** — trois éditions (le patron est `services/control-api/app/main.py:23,29,44`) :

1. Import : `from cmi_common.cache import Cache` et `from . import regime_api` (à côté des imports `read_api`/`journal_api` existants).
2. Dans `_startup`, après la création de `db` : `app.state.cache = Cache(settings.redis)`.
3. Dans `_shutdown` : `await app.state.cache.close()`.
4. Au montage des routers (bloc `app.include_router(...)`, main.py:116-131) : `app.include_router(regime_api.router, dependencies=_authed)`.

- [ ] **Step 5 : Étendre la fixture cache de `tests/conftest.py`** — dans `_clear_pipeline_stage_cache`, ajouter à côté du clear existant :

```python
    regime_api = load_service_module("api-gateway", "regime_api")
    regime_api.REGIME_CACHE.clear()
```

(en réutilisant l'import `load_service_module` déjà présent dans ce fichier ; ajouter le clear **avant et après** le yield, comme pour `STAGE_CACHE`).

- [ ] **Step 6 : Vérifier** — `pytest tests/test_read_contract.py tests/test_regime_rules.py -q` → PASS. `make lint` → propre.

- [ ] **Step 7 : Commit**

```bash
git add services/api-gateway/app/regime_api.py services/api-gateway/app/main.py \
        services/api-gateway/app/read_contract.py tests/test_read_contract.py tests/conftest.py
git commit -m "feat(api-gateway): endpoint /market/regime (Redis RO + regles pures, cache 30s)"
```

---

### Task 3 : Assemblage pur de l'explication (`explain.py`)

**Files:**
- Create: `services/api-gateway/app/explain.py`
- Test: `tests/test_explain_assembly.py`

- [ ] **Step 1 : Tests qui échouent**

```python
"""Pure assembly for /decisions/{id}/explain — SimpleNamespace rows, no DB."""

from types import SimpleNamespace

from service_modules import load_service_module

explain = load_service_module("api-gateway", "explain")


def _decision(**kw):
    base = dict(
        event_id="d-1", symbol="SOL", direction="long", opportunity_score=64,
        confidence=0.58, payload={"meta": {"breakdown": {"volume_growth": 0.8}}},
        correlation_id="cid-1", created_at=None, rationale="ok",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _journal(**kw):
    base = dict(
        event_id="j-1", symbol="SOL", score=64, confidence=0.58,
        factors={"momentum": 0.7, "volume": 0.5}, dominant_factor="momentum",
        escalated=True, sonnet_called=True, sonnet_validated=True,
        sonnet_score=70, sonnet_direction="long", skip_reason=None,
        risk_verdict="rejected", risk_reason="score 64 < floor 70",
        correlation_id="cid-1", decision_event_id="d-1", time=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_full_row_assembly() -> None:
    out = explain.build_explain(
        "d-1", decision=_decision(), journal=_journal(), rejection=None,
        trace={"correlation_id": "cid-1", "symbol": "SOL", "stages": []},
        counterfactual={"horizon": "4h", "pnl_pct": 2.1, "outcome": "take_profit"},
    )
    assert out["id"] == "d-1"
    assert out["symbol"] == "SOL"
    assert out["score"]["value"] == 64.0            # échelle brute 0-100, jamais /100
    assert out["triage"]["factors"] == {"momentum": 0.7, "volume": 0.5}
    assert out["risk"] == {"verdict": "rejected", "reason": "score 64 < floor 70"}
    assert out["counterfactual"]["pnl_pct"] == 2.1
    assert out["correlation_id"] == "cid-1"


def test_journal_only_row_pre_v2() -> None:
    """Décision rejetée : pas de ligne decisions, donc pas de breakdown."""
    out = explain.build_explain(
        "j-1", decision=None, journal=_journal(decision_event_id=None),
        rejection=None, trace=None, counterfactual=None,
    )
    assert out["score"]["insufficient_evidence"] is True
    assert out["score"]["axes"] == {}
    assert out["symbol"] == "SOL"
    assert out["direction"] is None
    assert out["trace"] is None


def test_nothing_found_is_callers_problem() -> None:
    """build_explain n'invente rien : au moins une source non nulle requise."""
    out = explain.build_explain("x", decision=None, journal=None, rejection=None,
                                trace=None, counterfactual=None)
    assert out["symbol"] is None
    assert out["triage"] is None
    assert out["risk"] is None
```

- [ ] **Step 2 : Vérifier l'échec** — `pytest tests/test_explain_assembly.py -q` → FAIL.

- [ ] **Step 3 : Implémenter `services/api-gateway/app/explain.py`**

```python
"""Pure assembly for GET /decisions/{event_id}/explain.

Reuses dossier.build_score / build_pipeline so the inspector can never drift
from the /market drawer. The Haiku triage factors are a DISJOINT namespace
from the eight scoring axes (see CLAUDE.md) — they are surfaced under
`triage`, never merged into `score`.
"""

from __future__ import annotations

from typing import Any

from .dossier import build_pipeline, build_score


def build_explain(
    event_id: str,
    *,
    decision: Any | None,
    journal: Any | None,
    rejection: Any | None,
    trace: dict[str, Any] | None,
    counterfactual: dict[str, Any] | None,
) -> dict[str, Any]:
    symbol = getattr(decision, "symbol", None) or getattr(journal, "symbol", None)
    triage = None
    risk = None
    if journal is not None:
        triage = {
            "score": journal.score,
            "confidence": journal.confidence,
            "factors": journal.factors or {},
            "dominant_factor": journal.dominant_factor,
            "escalated": bool(journal.escalated),
            "sonnet_called": bool(journal.sonnet_called),
            "sonnet_validated": journal.sonnet_validated,
            "sonnet_score": journal.sonnet_score,
            "sonnet_direction": journal.sonnet_direction,
            "skip_reason": journal.skip_reason,
        }
        risk = {"verdict": journal.risk_verdict, "reason": journal.risk_reason}
    return {
        "id": event_id,
        "symbol": symbol,
        "direction": getattr(decision, "direction", None),
        "score": build_score(decision),
        "triage": triage,
        "risk": risk,
        "pipeline": build_pipeline(journal, rejection),
        "counterfactual": counterfactual,
        "trace": trace,
        "correlation_id": (
            getattr(decision, "correlation_id", None)
            or getattr(journal, "correlation_id", None)
        ),
    }
```

- [ ] **Step 4 : Vérifier** — `pytest tests/test_explain_assembly.py -q` → PASS.

- [ ] **Step 5 : Commit**

```bash
git add services/api-gateway/app/explain.py tests/test_explain_assembly.py
git commit -m "feat(api-gateway): assemblage pur de l'explication de decision"
```

---

### Task 4 : Route `GET /decisions/{event_id}/explain`

**Files:**
- Modify: `services/api-gateway/app/read_api.py`
- Modify: `services/api-gateway/app/read_contract.py`
- Modify: `tests/test_read_contract.py`

- [ ] **Step 1 : Contrat + test qui échoue** — dans `read_contract.py` :

```python
    "decisions/explain": {"id", "symbol", "direction", "score", "triage", "risk",
                          "pipeline", "counterfactual", "trace", "correlation_id"},
```

Dans `tests/test_read_contract.py` (avant le méta-test), un fake session séquentiel + le test :

```python
class _SeqSession:
    """File de résultats préparés : un execute = un résultat, puis vide."""

    def __init__(self, results):  # noqa: ANN001
        self._results = list(results)

    async def execute(self, _stmt, _params=None):  # noqa: ANN001, ANN201
        return self._results.pop(0) if self._results else _Result()

    async def scalar(self, _stmt):  # noqa: ANN001, ANN201
        return 0


async def test_decision_explain_contract() -> None:
    from types import SimpleNamespace

    decision = SimpleNamespace(
        event_id="d-1", symbol="BTC", direction="long", opportunity_score=72,
        confidence=0.6, payload={}, correlation_id=None, created_at=None,
        rationale="",
    )
    session = _SeqSession([_Result(rows=[decision]), _Result(), _Result()])
    resp = await read_api.decision_explain(event_id="d-1", session=session)
    _assert_exact_keys("decisions/explain", resp)
```

- [ ] **Step 2 : Vérifier l'échec** — `pytest tests/test_read_contract.py -q` → FAIL (`decision_explain` absent).

- [ ] **Step 3 : Implémenter la route dans `read_api.py`** — à placer après la route `/trace/{cid}` (~ligne 990), pour réutiliser le handler `trace` :

```python
@router.get("/decisions/{event_id}/explain")
async def decision_explain(
    event_id: str,
    session: AsyncSession = Depends(get_session_dep),
) -> dict:
    """Aggregate one decision's whole story: score, triage, verdicts, trace.

    The id accepted is any of the ids the terminal actually holds:
    Decision.event_id (feeds, dossier), DecisionJournal.event_id (journal
    rows) or the journal's decision_event_id / signal_event_id links.
    """
    from .explain import build_explain
    from .journal_api import PRIMARY_HORIZON, attach_outcome
    from .journal_query import price_path

    decision = (
        (await session.execute(select(Decision).where(Decision.event_id == event_id)))
        .scalars()
        .first()
    )
    journal = (
        (
            await session.execute(
                select(DecisionJournal)
                .where(
                    or_(
                        DecisionJournal.event_id == event_id,
                        DecisionJournal.decision_event_id == event_id,
                        DecisionJournal.signal_event_id == event_id,
                    )
                )
                .order_by(DecisionJournal.time.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if decision is None and journal is None:
        raise HTTPException(status_code=404, detail=f"unknown decision {event_id!r}")

    symbol = getattr(decision, "symbol", None) or journal.symbol
    rejection = (
        (
            await session.execute(
                select(PipelineRejection)
                .where(PipelineRejection.symbol == symbol)
                .order_by(PipelineRejection.time.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    cid = getattr(decision, "correlation_id", None) or getattr(
        journal, "correlation_id", None
    )
    trace_data = None
    if cid:
        try:
            trace_data = await trace(cid=cid, session=session)
        except HTTPException:
            trace_data = None

    counterfactual = None
    if (
        journal is not None
        and journal.entry_price
        and journal.stop_loss is not None
        and journal.take_profit is not None
    ):
        path = await price_path(session, journal.symbol, journal.time, PRIMARY_HORIZON)
        judged = attach_outcome(
            {
                "entry_price": journal.entry_price,
                "stop_loss": journal.stop_loss,
                "take_profit": journal.take_profit,
                "sonnet_direction": journal.sonnet_direction,
            },
            path=path,
            horizon=PRIMARY_HORIZON,
        )
        counterfactual = {
            "horizon": PRIMARY_HORIZON,
            "pnl_pct": judged[f"pnl_{PRIMARY_HORIZON}"],
            "outcome": judged[f"outcome_{PRIMARY_HORIZON}"],
        }

    return build_explain(
        event_id,
        decision=decision,
        journal=journal,
        rejection=rejection,
        trace=trace_data,
        counterfactual=counterfactual,
    )
```

Les imports locaux (`explain`, `journal_api`, `journal_query`) restent **dans la fonction** : `read_api` est importé par `journal_api`-adjacents dans les tests et un import module-level croisé `read_api ↔ journal_api` créerait un cycle si `journal_api` venait à importer un mapper de `read_api` plus tard. Vérifier la signature réelle de `price_path` dans `app/journal_query.py` avant d'écrire (attendu : `price_path(session, symbol, start, horizon)`) ; si l'ordre diffère, adapter l'appel, pas le helper.

- [ ] **Step 4 : Vérifier** — `pytest tests/test_read_contract.py -q` → PASS (y compris le méta-test). `make lint`.

- [ ] **Step 5 : Commit**

```bash
git add services/api-gateway/app/read_api.py services/api-gateway/app/read_contract.py tests/test_read_contract.py
git commit -m "feat(api-gateway): endpoint /decisions/{id}/explain"
```

---

### Task 5 : Math pure calibration + attribution

**Files:**
- Create: `services/api-gateway/app/journal_calibration.py`
- Test: `tests/test_journal_calibration.py`

- [ ] **Step 1 : Tests qui échouent**

```python
"""Pure math for /systems/journal/{calibration,attribution}."""

from service_modules import load_service_module

jc = load_service_module("api-gateway", "journal_calibration")


def _rows(n: int, *, score: int = 80, pnl: float = 1.0) -> list[dict]:
    return [
        {"score": score, "pnl_4h": pnl, "factors": {"momentum": 0.5 + 0.01 * i}}
        for i in range(n)
    ]


def test_calibrate_below_min_n_reports_null_not_zero() -> None:
    out = jc.calibrate(_rows(5), threshold=70, field="pnl_4h")
    assert out["judged"] == 5 and out["sufficient"] is False
    assert out["win_rate"] is None and out["avg_pnl_pct"] is None
    assert out["total_pnl_pct"] is None


def test_calibrate_counts_and_stats() -> None:
    rows = _rows(15, pnl=2.0) + _rows(10, pnl=-1.0) + _rows(5, score=40, pnl=9.9)
    out = jc.calibrate(rows, threshold=70, field="pnl_4h")
    assert out["selected"] == 25          # les score=40 sont hors seuil
    assert out["judged"] == 25 and out["sufficient"] is True
    assert out["win_rate"] == 0.6
    assert out["total_pnl_pct"] == 20.0   # 15*2 - 10*1


def test_calibrate_ignores_unjudged_rows() -> None:
    rows = _rows(30) + [{"score": 90, "pnl_4h": None, "factors": {}}] * 10
    out = jc.calibrate(rows, threshold=70, field="pnl_4h")
    assert out["selected"] == 40 and out["judged"] == 30


def test_pearson_degenerate_is_none() -> None:
    assert jc.pearson([1.0], [1.0]) is None
    assert jc.pearson([2.0] * 30, [1.0] * 30) is None   # variance nulle


def test_attribution_positive_correlation() -> None:
    rows = [
        {"score": 80, "pnl_4h": float(i), "factors": {"momentum": float(i), "volume": 0.5}}
        for i in range(25)
    ]
    out = jc.attribution(rows, factor_keys=("momentum", "volume"), field="pnl_4h")
    momentum = next(f for f in out if f["key"] == "momentum")
    volume = next(f for f in out if f["key"] == "volume")
    assert momentum["correlation"] == 1.0 and momentum["n"] == 25
    assert volume["correlation"] is None   # variance nulle → pas de fausse mesure
```

- [ ] **Step 2 : Vérifier l'échec** — `pytest tests/test_journal_calibration.py -q` → FAIL.

- [ ] **Step 3 : Implémenter `services/api-gateway/app/journal_calibration.py`**

```python
"""Pure math for the /journal panels. No I/O, no SQL.

MIN_N guards every statistic: below it the value is None (rendered '—'), not
a confident number computed on three points.
"""

from __future__ import annotations

import math
from typing import Any

MIN_N = 20

TRIAGE_FACTORS: tuple[str, ...] = ("momentum", "volume", "sentiment", "liquidity")


def calibrate(rows: list[dict[str, Any]], *, threshold: int, field: str) -> dict[str, Any]:
    eligible = [r for r in rows if r.get("score") is not None]
    selected = [r for r in eligible if r["score"] >= threshold]
    judged = [r for r in selected if r.get(field) is not None]
    n = len(judged)
    out: dict[str, Any] = {
        "threshold": threshold,
        "selected": len(selected),
        "judged": n,
        "sufficient": n >= MIN_N,
        "win_rate": None,
        "avg_pnl_pct": None,
        "total_pnl_pct": None,
    }
    if n >= MIN_N:
        pnls = [float(r[field]) for r in judged]
        wins = sum(1 for p in pnls if p > 0)
        out["win_rate"] = round(wins / n, 4)
        out["avg_pnl_pct"] = round(sum(pnls) / n, 4)
        out["total_pnl_pct"] = round(sum(pnls), 4)
    return out


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / math.sqrt(sxx * syy)


def attribution(
    rows: list[dict[str, Any]], *, factor_keys: tuple[str, ...], field: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in factor_keys:
        pairs = [
            (float((r.get("factors") or {})[key]), float(r[field]))
            for r in rows
            if isinstance((r.get("factors") or {}).get(key), (int, float))
            and r.get(field) is not None
        ]
        r_val = (
            pearson([p[0] for p in pairs], [p[1] for p in pairs])
            if len(pairs) >= MIN_N
            else None
        )
        out.append(
            {
                "key": key,
                "n": len(pairs),
                "correlation": None if r_val is None else round(r_val, 4),
            }
        )
    return out
```

- [ ] **Step 4 : Vérifier** — `pytest tests/test_journal_calibration.py -q` → PASS.

- [ ] **Step 5 : Commit**

```bash
git add services/api-gateway/app/journal_calibration.py tests/test_journal_calibration.py
git commit -m "feat(api-gateway): calibration et attribution pures pour /journal"
```

---

### Task 6 : Routes journal (`decisions`, `calibration`, `attribution`)

**Files:**
- Modify: `services/api-gateway/app/journal_api.py`
- Modify: `services/api-gateway/app/read_contract.py`
- Modify: `tests/test_read_contract.py`

- [ ] **Step 1 : Contrat + tests qui échouent** — dans `read_contract.py` :

```python
    "systems/journal/decisions": {"window", "horizon", "current_threshold", "total", "rows"},
    "systems/journal/decisions.rows[]": {
        "time", "event_id", "symbol", "score", "confidence", "escalated",
        "sonnet_called", "sonnet_validated", "direction", "passed",
        "risk_verdict", "pnl_pct", "outcome", "correlation_id",
    },
    "systems/journal/calibration": {"window", "horizon", "min_n", "requested", "current"},
    "systems/journal/calibration.requested": {
        "threshold", "selected", "judged", "sufficient",
        "win_rate", "avg_pnl_pct", "total_pnl_pct",
    },
    "systems/journal/attribution": {"window", "horizon", "n", "min_n", "sufficient", "factors"},
    "systems/journal/attribution.factors[]": {"key", "n", "correlation"},
```

Dans `tests/test_read_contract.py` (avant le méta-test) — les listes vides ne s'assertent pas, donc `rows[]` et `factors[]` passent par les fonctions pures :

```python
async def test_journal_decisions_contract() -> None:
    resp = await journal_api.journal_decisions(
        window="30d", limit=50, offset=0, session=_FakeSession(3)
    )
    _assert_exact_keys("systems/journal/decisions", resp)
    sample = journal_api._map_row(
        {
            "time": None, "event_id": "j-1", "symbol": "BTC", "score": 60,
            "confidence": 0.5, "escalated": True, "sonnet_called": False,
            "sonnet_validated": None, "sonnet_direction": None,
            "risk_verdict": None, "decision_event_id": None,
            "correlation_id": None, "pnl_4h": None, "outcome_4h": None,
        }
    )
    _assert_exact_keys("systems/journal/decisions.rows[]", sample)


async def test_journal_calibration_contract() -> None:
    resp = await journal_api.journal_calibration(
        window="30d", threshold=70, session=_FakeSession(2)
    )
    _assert_exact_keys("systems/journal/calibration", resp)
    _assert_exact_keys("systems/journal/calibration.requested", resp["requested"])


async def test_journal_attribution_contract() -> None:
    resp = await journal_api.journal_attribution(window="30d", session=_FakeSession(2))
    _assert_exact_keys("systems/journal/attribution", resp)
    for f in resp["factors"]:
        _assert_exact_keys("systems/journal/attribution.factors[]", f)
```

(`journal_api.journal_attribution` retourne toujours les 4 facteurs de `TRIAGE_FACTORS`, liste jamais vide — assertable directement.)

⚠️ Si `journal_api._map_row` attend `pnl_4h`/`outcome_4h`, c'est parce que `PRIMARY_HORIZON` vaut `4h` par défaut ; le test doit construire les clés dynamiquement : `f"pnl_{journal_api.PRIMARY_HORIZON}"`.

- [ ] **Step 2 : Vérifier l'échec** — `pytest tests/test_read_contract.py -q` → FAIL.

- [ ] **Step 3 : Implémenter dans `journal_api.py`** — ajouter après la route summary existante :

```python
import os

from .journal_calibration import MIN_N, TRIAGE_FACTORS, attribution, calibrate

_raw_threshold = os.getenv("RISK_MIN_SCORE")
#: None quand l'env n'est pas posée dans CE conteneur : afficher '—' plutôt
#: qu'un 70 par défaut qui mentirait sur le seuil réellement appliqué par
#: risk-engine (à 101 en prod au moment d'écrire).
CURRENT_THRESHOLD: int | None = int(_raw_threshold) if _raw_threshold else None

_PNL_FIELD = f"pnl_{PRIMARY_HORIZON}"
_OUTCOME_FIELD = f"outcome_{PRIMARY_HORIZON}"

_ROWS_SQL = (
    "SELECT time, event_id, symbol, score, confidence, escalated, sonnet_called,"
    " sonnet_validated, sonnet_direction, entry_price, stop_loss, take_profit,"
    " factors, risk_verdict, decision_event_id, correlation_id"
    " FROM decision_journal WHERE time >= :since ORDER BY time DESC"
)


async def _judged_rows(
    session: AsyncSession,
    since: datetime,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if limit is not None:
        stmt = text(_ROWS_SQL + " LIMIT :limit OFFSET :offset")
        params: dict[str, Any] = {"since": since, "limit": limit, "offset": offset}
    else:
        stmt = text(_ROWS_SQL)
        params = {"since": since}
    result = await session.execute(stmt, params)
    rows = [dict(r._mapping) for r in result.all()]
    judged: list[dict[str, Any]] = []
    for row in rows:
        if row.get("entry_price") and row.get("stop_loss") is not None and row.get("take_profit") is not None:
            path = await price_path(session, row["symbol"], row["time"], PRIMARY_HORIZON)
            judged.append(attach_outcome(row, path=path, horizon=PRIMARY_HORIZON))
        else:
            judged.append({**row, _PNL_FIELD: None, _OUTCOME_FIELD: None})
    return judged


def _map_row(row: dict[str, Any]) -> dict[str, Any]:
    t = row.get("time")
    return {
        "time": t.isoformat() if t else None,
        "event_id": row["event_id"],
        "symbol": row["symbol"],
        "score": row["score"],
        "confidence": row["confidence"],
        "escalated": bool(row["escalated"]),
        "sonnet_called": bool(row["sonnet_called"]),
        "sonnet_validated": row["sonnet_validated"],
        "direction": row["sonnet_direction"],
        "passed": row["decision_event_id"] is not None,
        "risk_verdict": row["risk_verdict"],
        "pnl_pct": row[_PNL_FIELD],
        "outcome": row[_OUTCOME_FIELD],
        "correlation_id": row["correlation_id"],
    }


@router.get("/systems/journal/decisions")
async def journal_decisions(
    window: str = Query("30d", pattern="^(7d|30d|90d)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session_dep),
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=_WINDOWS[window])
    total_result = await session.execute(
        text("SELECT count(*) FROM decision_journal WHERE time >= :since"),
        {"since": since},
    )
    total = int(total_result.scalar_one() or 0)
    rows = await _judged_rows(session, since, limit=limit, offset=offset)
    return {
        "window": window,
        "horizon": PRIMARY_HORIZON,
        "current_threshold": CURRENT_THRESHOLD,
        "total": total,
        "rows": [_map_row(r) for r in rows],
    }


@router.get("/systems/journal/calibration")
async def journal_calibration(
    window: str = Query("30d", pattern="^(7d|30d|90d)$"),
    threshold: int = Query(70, ge=0, le=100),
    session: AsyncSession = Depends(get_session_dep),
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=_WINDOWS[window])
    rows = await _judged_rows(session, since)
    current = (
        calibrate(rows, threshold=CURRENT_THRESHOLD, field=_PNL_FIELD)
        if CURRENT_THRESHOLD is not None
        else None
    )
    return {
        "window": window,
        "horizon": PRIMARY_HORIZON,
        "min_n": MIN_N,
        "requested": calibrate(rows, threshold=threshold, field=_PNL_FIELD),
        "current": current,
    }


@router.get("/systems/journal/attribution")
async def journal_attribution(
    window: str = Query("30d", pattern="^(7d|30d|90d)$"),
    session: AsyncSession = Depends(get_session_dep),
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=_WINDOWS[window])
    rows = await _judged_rows(session, since)
    n = sum(1 for r in rows if r.get(_PNL_FIELD) is not None)
    return {
        "window": window,
        "horizon": PRIMARY_HORIZON,
        "n": n,
        "min_n": MIN_N,
        "sufficient": n >= MIN_N,
        "factors": attribution(rows, factor_keys=TRIAGE_FACTORS, field=_PNL_FIELD),
    }
```

Vérifier en tête de `journal_api.py` que `datetime`/`timedelta`/`Any`/`text`/`Query` sont déjà importés (ils le sont pour la route summary) ; sinon compléter. Vérifier la signature réelle de `price_path` dans `journal_query.py` et l'import existant de `utcnow`/`_WINDOWS`.

- [ ] **Step 4 : Vérifier** — `pytest tests/test_read_contract.py tests/test_journal_calibration.py -q` → PASS. `make lint`.

- [ ] **Step 5 : Commit**

```bash
git add services/api-gateway/app/journal_api.py services/api-gateway/app/read_contract.py tests/test_read_contract.py
git commit -m "feat(api-gateway): endpoints journal decisions/calibration/attribution"
```

---

### Task 7 : Harness live

**Files:**
- Modify: `scripts/verify_read_live.py`

- [ ] **Step 1 : Ajouter les imports et le cache** — en tête, à côté des imports existants : `from cmi_common.cache import Cache` et `from app import journal_api, regime_api` (compléter la ligne d'import `from app import ...` existante). Dans `main()`, avant la construction de `calls` : `cache = Cache(settings.redis)`.

- [ ] **Step 2 : Ajouter les appels** dans la liste `calls` :

```python
        ("market/regime", regime_api.market_regime(session=s, cache=cache)),
        ("systems/journal/decisions", journal_api.journal_decisions(window="30d", limit=5, offset=0, session=s)),
        ("systems/journal/calibration", journal_api.journal_calibration(window="30d", threshold=70, session=s)),
        ("systems/journal/attribution", journal_api.journal_attribution(window="30d", session=s)),
```

Pour `decisions/explain`, après la boucle `calls` (même style que le cas 404 existant) : requêter le dernier `event_id` du journal (`SELECT event_id FROM decision_journal ORDER BY time DESC LIMIT 1`) ; s'il existe, appeler `read_api.decision_explain(event_id=..., session=s)` et `_check("decisions/explain", ...)` ; sinon imprimer `SKIP decisions/explain (journal vide)`. En fin de `main()` : `await cache.close()`.

- [ ] **Step 3 : Vérification statique** — `python -m py_compile scripts/verify_read_live.py` (l'exécution réelle se fait dans le conteneur, hors CI).

- [ ] **Step 4 : Commit**

```bash
git add scripts/verify_read_live.py
git commit -m "test(api-gateway): harness live etendu aux endpoints cockpit"
```

---

### Task 8 : Frontend — types, endpoints, mock BFF

**Files:**
- Create: `frontend/src/lib/types/regime.ts`, `frontend/src/lib/types/explain.ts`, `frontend/src/lib/types/journal.ts`
- Create: `frontend/src/lib/mock/regime.ts`, `frontend/src/lib/mock/journal.ts`
- Create: `frontend/src/app/api/mock/market/regime/route.ts`, `frontend/src/app/api/mock/decisions/[id]/explain/route.ts`, `frontend/src/app/api/mock/systems/journal/decisions/route.ts`, `.../calibration/route.ts`, `.../attribution/route.ts`
- Modify: `frontend/src/lib/api/endpoints.ts`

- [ ] **Step 1 : `src/lib/types/regime.ts`**

```ts
/** Contrat GET /market/regime — miroir de api-gateway app/regime.py.
 *  Règle du projet : null = non mesuré = rendu « — », jamais 0. */
export type RegimeLabel = 'RISK_ON' | 'ACCUMULATION' | 'NEUTRAL' | 'DISTRIBUTION' | 'RISK_OFF';
export type DriverState = 'bullish' | 'bearish' | 'neutral';
export type DriverKey = 'funding' | 'oi_delta' | 'market_sentiment' | 'btc_dominance' | 'breadth';

export interface RegimeDriver {
  key: DriverKey;
  value: number | null;
  state: DriverState | null;
  detail: string;
  as_of: string | null;
}

export interface MarketRegime {
  regime: RegimeLabel | null;
  confidence: number | null;
  drivers: RegimeDriver[];
  computed_at: string;
}

export const DRIVER_LABELS: Record<DriverKey, string> = {
  funding: 'Funding',
  oi_delta: 'ΔOI 24h',
  market_sentiment: 'Sent. marché',
  btc_dominance: 'BTC.D Δ7j',
  breadth: 'Breadth',
};

export const REGIME_LABELS: Record<RegimeLabel, string> = {
  RISK_ON: 'RISK-ON',
  ACCUMULATION: 'ACCUMULATION',
  NEUTRAL: 'NEUTRE',
  DISTRIBUTION: 'DISTRIBUTION',
  RISK_OFF: 'RISK-OFF',
};
```

- [ ] **Step 2 : `src/lib/types/explain.ts`**

```ts
import type { DecisionTrace } from './content';
import type { PipelineVerdict, TokenScore } from './dossier';

/** Facteurs de triage Haiku — namespace DISJOINT des 8 axes de scoring. */
export interface ExplainTriage {
  score: number | null;
  confidence: number | null;
  factors: Record<string, number>;
  dominant_factor: string | null;
  escalated: boolean;
  sonnet_called: boolean;
  sonnet_validated: boolean | null;
  sonnet_score: number | null;
  sonnet_direction: string | null;
  skip_reason: string | null;
}

export interface ExplainCounterfactual {
  horizon: string;
  pnl_pct: number | null;
  outcome: string | null;
}

export interface DecisionExplain {
  id: string;
  symbol: string | null;
  direction: string | null;
  /** Échelle brute 0–100 — l'inspecteur n'affiche jamais la 0–1. */
  score: TokenScore;
  triage: ExplainTriage | null;
  risk: { verdict: string | null; reason: string | null } | null;
  pipeline: PipelineVerdict;
  counterfactual: ExplainCounterfactual | null;
  trace: DecisionTrace | null;
  correlation_id: string | null;
}
```

- [ ] **Step 3 : `src/lib/types/journal.ts`**

```ts
export type JournalWindow = '7d' | '30d' | '90d';

export interface JournalRow {
  time: string | null;
  event_id: string;
  symbol: string;
  score: number | null;
  confidence: number | null;
  escalated: boolean;
  sonnet_called: boolean;
  sonnet_validated: boolean | null;
  direction: string | null;
  passed: boolean;
  risk_verdict: string | null;
  pnl_pct: number | null;
  outcome: string | null;
  correlation_id: string | null;
}

export interface JournalDecisionsPage {
  window: JournalWindow;
  horizon: string;
  current_threshold: number | null;
  total: number;
  rows: JournalRow[];
}

export interface CalibrationBucket {
  threshold: number;
  selected: number;
  judged: number;
  sufficient: boolean;
  win_rate: number | null;
  avg_pnl_pct: number | null;
  total_pnl_pct: number | null;
}

export interface JournalCalibration {
  window: JournalWindow;
  horizon: string;
  min_n: number;
  requested: CalibrationBucket;
  current: CalibrationBucket | null;
}

export interface AttributionFactor {
  key: string;
  n: number;
  correlation: number | null;
}

export interface JournalAttribution {
  window: JournalWindow;
  horizon: string;
  n: number;
  min_n: number;
  sufficient: boolean;
  factors: AttributionFactor[];
}
```

- [ ] **Step 4 : Endpoints** — dans `src/lib/api/endpoints.ts`, ajouter les imports de types puis :

```ts
export const regimeApi = {
  get: () => api.get<MarketRegime>('/market/regime').then((r) => r.data),
};

export const explainApi = {
  get: (id: string) => api.get<DecisionExplain>(`/decisions/${id}/explain`).then((r) => r.data),
};

export const journalApi = {
  decisions: (window: JournalWindow = '30d', limit = 50, offset = 0) =>
    api.get<JournalDecisionsPage>('/systems/journal/decisions', { params: { window, limit, offset } }).then((r) => r.data),
  calibration: (threshold: number, window: JournalWindow = '30d') =>
    api.get<JournalCalibration>('/systems/journal/calibration', { params: { window, threshold } }).then((r) => r.data),
  attribution: (window: JournalWindow = '30d') =>
    api.get<JournalAttribution>('/systems/journal/attribution', { params: { window } }).then((r) => r.data),
};
```

- [ ] **Step 5 : Générateurs mock** — `src/lib/mock/regime.ts` :

```ts
import type { MarketRegime } from '@/lib/types/regime';

/** market_sentiment volontairement null : exerce le rendu « — » du strip,
 *  comme le mock dossier laisse `fundamentals` absent. */
export function getRegime(): MarketRegime {
  return {
    regime: 'ACCUMULATION',
    confidence: 0.8,
    computed_at: new Date().toISOString(),
    drivers: [
      { key: 'funding', value: 0.00013, state: 'bearish', detail: 'médiane funding +0.000130/8h (Binance, univers suivi) : crowded-long. Contrarien : > +0.0001 → bearish, < -0.0001 → bullish.', as_of: new Date(Date.now() - 240_000).toISOString() },
      { key: 'oi_delta', value: 6.2, state: 'bullish', detail: 'médiane ΔOI 24h +6.2% (majors Binance), prix BTC 24h +2.1% : levier suit la hausse. Seuil ±5%.', as_of: new Date(Date.now() - 240_000).toISOString() },
      { key: 'market_sentiment', value: null, state: null, detail: 'lecture market-wide indisponible (cadence irrégulière mesurée : médiane 19 min, p95 71 min)', as_of: null },
      { key: 'btc_dominance', value: -0.7, state: 'bullish', detail: 'BTC.D 53.4% (univers suivi ~200 tokens, pas le marché entier), dérive 7j -0.70 pt : rotation vers les alts. Seuil ±0.5 pt.', as_of: new Date(Date.now() - 3_600_000).toISOString() },
      { key: 'breadth', value: 0.64, state: 'bullish', detail: '64% des 187 tokens suivis en hausse sur 24h. Seuils : > 60% bullish, < 40% bearish.', as_of: new Date(Date.now() - 3_600_000).toISOString() },
    ],
  };
}
```

`src/lib/mock/journal.ts` — générer un jeu **déterministe** (seed = index, pas de `Math.random` pour la stabilité des tests) :

```ts
import type { JournalAttribution, JournalCalibration, JournalDecisionsPage, JournalRow, JournalWindow } from '@/lib/types/journal';
import type { DecisionExplain } from '@/lib/types/explain';

const SYMBOLS = ['BTC', 'ETH', 'SOL', 'DOGE', 'AVAX', 'LINK'];

function row(i: number): JournalRow {
  const score = 35 + ((i * 7) % 60);
  const judged = i % 5 !== 0;
  const pnl = judged ? Math.round(((i % 11) - 5) * 8) / 10 : null;
  return {
    time: new Date(Date.now() - i * 3_600_000).toISOString(),
    event_id: `jr-${i}`,
    symbol: SYMBOLS[i % SYMBOLS.length],
    score,
    confidence: 0.4 + (i % 5) * 0.1,
    escalated: score >= 55,
    sonnet_called: score >= 60,
    sonnet_validated: score >= 60 ? i % 3 !== 0 : null,
    direction: score >= 60 ? (i % 2 ? 'long' : 'short') : null,
    passed: score >= 70,
    risk_verdict: score >= 70 ? (i % 4 === 0 ? 'rejected' : 'approved') : null,
    pnl_pct: pnl,
    outcome: pnl === null ? null : pnl > 0 ? 'take_profit' : 'stop_loss',
    correlation_id: i % 3 === 0 ? `cid-${i}` : null,
  };
}

export function getJournalDecisions(window: JournalWindow, limit: number, offset: number): JournalDecisionsPage {
  const total = 120;
  const rows = Array.from({ length: Math.min(limit, total - offset) }, (_, k) => row(offset + k));
  return { window, horizon: '4h', current_threshold: 101, total, rows };
}

export function getJournalCalibration(window: JournalWindow, threshold: number): JournalCalibration {
  const all = Array.from({ length: 120 }, (_, i) => row(i));
  const bucket = (t: number) => {
    const sel = all.filter((r) => r.score !== null && r.score >= t);
    const judged = sel.filter((r) => r.pnl_pct !== null);
    const n = judged.length;
    const suff = n >= 20;
    const wins = judged.filter((r) => (r.pnl_pct ?? 0) > 0).length;
    const totalPnl = judged.reduce((s, r) => s + (r.pnl_pct ?? 0), 0);
    return {
      threshold: t, selected: sel.length, judged: n, sufficient: suff,
      win_rate: suff ? Math.round((wins / n) * 10000) / 10000 : null,
      avg_pnl_pct: suff ? Math.round((totalPnl / n) * 10000) / 10000 : null,
      total_pnl_pct: suff ? Math.round(totalPnl * 10000) / 10000 : null,
    };
  };
  return { window, horizon: '4h', min_n: 20, requested: bucket(threshold), current: bucket(101) };
}

export function getJournalAttribution(window: JournalWindow): JournalAttribution {
  return {
    window, horizon: '4h', n: 96, min_n: 20, sufficient: true,
    factors: [
      { key: 'momentum', n: 96, correlation: 0.31 },
      { key: 'volume', n: 96, correlation: 0.12 },
      { key: 'sentiment', n: 88, correlation: -0.04 },
      { key: 'liquidity', n: 14, correlation: null },   // n < min_n → « — »
    ],
  };
}

export function getExplain(id: string): DecisionExplain {
  return {
    id, symbol: 'SOL', direction: 'long',
    score: {
      value: 64, confidence: 0.58,
      axes: { volume_growth: 0.8, market_trend: 0.65, positioning: 0.55, liquidity_score: 0.7, social_score: 0.5, news_score: 0.45 },
      axes_total: 8, insufficient_evidence: false, computed_at: new Date().toISOString(),
    },
    triage: {
      score: 64, confidence: 0.58,
      factors: { momentum: 0.7, volume: 0.6, sentiment: 0.4, liquidity: 0.55 },
      dominant_factor: 'momentum', escalated: true, sonnet_called: true,
      sonnet_validated: true, sonnet_score: 70, sonnet_direction: 'long', skip_reason: null,
    },
    risk: { verdict: 'rejected', reason: 'score 64 < floor 70' },
    pipeline: { reached_stage: 'risk', blocked_at: 'risk', block_reason: 'score 64 < floor 70', escalated: true, sonnet_called: true, sonnet_validated: true, last_event_at: new Date().toISOString() },
    counterfactual: { horizon: '4h', pnl_pct: 2.1, outcome: 'take_profit' },
    trace: { correlation_id: 'cid-1', symbol: 'SOL', stages: [
      { kind: 'price', at: new Date().toISOString(), reached: true, summary: 'PriceEvent SOL', detail: { price: 178.2 } },
      { kind: 'analysis', at: new Date().toISOString(), reached: true, summary: 'Triage Haiku 64', detail: { score: 64 } },
      { kind: 'risk', at: new Date().toISOString(), reached: true, summary: 'Rejeté : floor', detail: { floor: 70 } },
    ] },
    correlation_id: 'cid-1',
  };
}
```

Vérifier le shape exact de `PipelineVerdict` dans `dossier.ts` avant d'écrire le mock `pipeline` (clés : `reached_stage`, `blocked_at`, `block_reason`, `escalated`, `sonnet_called`, `sonnet_validated`, `last_event_at`).

- [ ] **Step 6 : Routes mock** — `src/app/api/mock/market/regime/route.ts` :

```ts
import { NextResponse } from 'next/server';
import { getRegime } from '@/lib/mock/regime';

export async function GET() {
  return NextResponse.json(getRegime());
}
```

`src/app/api/mock/decisions/[id]/explain/route.ts` (Next 15 : `params` est une Promise) :

```ts
import { NextResponse } from 'next/server';
import { getExplain } from '@/lib/mock/journal';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (id === 'unknown') return NextResponse.json({ detail: 'unknown decision' }, { status: 404 });
  return NextResponse.json(getExplain(id));
}
```

`src/app/api/mock/systems/journal/decisions/route.ts` :

```ts
import { NextResponse } from 'next/server';
import { getJournalDecisions } from '@/lib/mock/journal';
import type { JournalWindow } from '@/lib/types/journal';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const window = (searchParams.get('window') ?? '30d') as JournalWindow;
  const limit = Number(searchParams.get('limit') ?? 50);
  const offset = Number(searchParams.get('offset') ?? 0);
  return NextResponse.json(getJournalDecisions(window, limit, offset));
}
```

`.../calibration/route.ts` et `.../attribution/route.ts` sur le même modèle (`threshold` → `Number(searchParams.get('threshold') ?? 70)`).

- [ ] **Step 7 : Vérifier** — `cd frontend && npm run typecheck` → propre. `npm run test:run` → suites existantes toujours vertes.

- [ ] **Step 8 : Commit**

```bash
git add frontend/src/lib/types frontend/src/lib/mock frontend/src/app/api/mock frontend/src/lib/api/endpoints.ts
git commit -m "feat(frontend): types, endpoints et mock BFF pour regime/explain/journal"
```

---

### Task 9 : RegimeStrip global

**Files:**
- Create: `frontend/src/components/layout/RegimeStrip.tsx`
- Modify: `frontend/src/components/layout/AppShell.tsx`
- Test: `frontend/src/components/layout/__tests__/RegimeStrip.test.tsx`

- [ ] **Step 1 : Test qui échoue**

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RegimeStrip } from '../RegimeStrip';
import { getRegime } from '@/lib/mock/regime';

vi.mock('@/lib/api/endpoints', () => ({
  regimeApi: { get: vi.fn() },
  tradingApi: { status: vi.fn() },
}));

import { regimeApi, tradingApi } from '@/lib/api/endpoints';

const regimeGet = vi.mocked(regimeApi.get);
const statusGet = vi.mocked(tradingApi.status);

function renderStrip() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RegimeStrip />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('RegimeStrip', () => {
  it('affiche le régime, les drivers et « — » pour un driver non mesuré', async () => {
    regimeGet.mockResolvedValue(getRegime());
    statusGet.mockResolvedValue({ mode: 'dry_run', trading_enabled: true, auto_trading_enabled: false });
    renderStrip();
    expect(await screen.findByText('ACCUMULATION')).toBeInTheDocument();
    // market_sentiment est null dans le mock → sa cellule rend un tiret
    expect(screen.getByTestId('driver-market_sentiment')).toHaveTextContent('—');
    expect(screen.getByText(/dry_run/i)).toBeInTheDocument();
  });

  it('ouvre le popover de règle au clic sur un driver', async () => {
    regimeGet.mockResolvedValue(getRegime());
    statusGet.mockResolvedValue({ mode: 'dry_run', trading_enabled: true, auto_trading_enabled: false });
    renderStrip();
    await screen.findByText('ACCUMULATION');
    await userEvent.click(screen.getByTestId('driver-funding'));
    expect(await screen.findByText(/Contrarien/)).toBeInTheDocument();
  });

  it('rend REGIME: — quand le régime est null', async () => {
    regimeGet.mockResolvedValue({ ...getRegime(), regime: null, confidence: 0.2 });
    statusGet.mockResolvedValue({ mode: 'dry_run', trading_enabled: true, auto_trading_enabled: false });
    renderStrip();
    expect(await screen.findByTestId('regime-label')).toHaveTextContent('—');
  });
});
```

(Si `TradingStatus` a d'autres champs requis dans `domain.ts`, compléter les objets `statusGet.mockResolvedValue` pour satisfaire le type.)

- [ ] **Step 2 : Vérifier l'échec** — `cd frontend && npx vitest run src/components/layout/__tests__/RegimeStrip.test.tsx` → FAIL (composant absent).

- [ ] **Step 3 : Implémenter `RegimeStrip.tsx`** — modèle visuel : `KpiTicker` (flex, `cmi-glass`, cellules séparées, `overflowX: auto`, classe `mono`) :

```tsx
'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box, Chip, Popover, Stack, Typography } from '@mui/material';
import { regimeApi, tradingApi } from '@/lib/api/endpoints';
import { DRIVER_LABELS, REGIME_LABELS, type RegimeDriver } from '@/lib/types/regime';
import { fmtRelative } from '@/lib/format';

const STATE_COLOR: Record<string, string> = {
  bullish: 'var(--mui-palette-success-main, #26d07c)',
  bearish: 'var(--mui-palette-error-main, #ff5370)',
  neutral: 'inherit',
};
const STATE_ARROW: Record<string, string> = { bullish: '▲', bearish: '▼', neutral: '·' };

function driverValue(d: RegimeDriver): string {
  if (d.value === null) return '—';
  switch (d.key) {
    case 'funding':
      return `${(d.value * 100).toFixed(4)}%/8h`;
    case 'oi_delta':
      return `${d.value > 0 ? '+' : ''}${d.value.toFixed(1)}%`;
    case 'market_sentiment':
      return d.value.toFixed(2);
    case 'btc_dominance':
      return `${d.value > 0 ? '+' : ''}${d.value.toFixed(2)} pt`;
    case 'breadth':
      return `${Math.round(d.value * 100)}%`;
  }
}

export function RegimeStrip() {
  const regime = useQuery({ queryKey: ['market', 'regime'], queryFn: regimeApi.get, refetchInterval: 30_000 });
  const status = useQuery({ queryKey: ['trading', 'status'], queryFn: tradingApi.status, refetchInterval: 15_000 });
  const [anchor, setAnchor] = useState<{ el: HTMLElement; driver: RegimeDriver } | null>(null);

  const data = regime.data;
  return (
    <Box className="cmi-glass mono" sx={{ borderRadius: 2, px: 1.5, py: 0.75, mb: 2, display: 'flex', alignItems: 'center', gap: 2, overflowX: 'auto', whiteSpace: 'nowrap', fontSize: 13 }}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Typography variant="caption" sx={{ opacity: 0.6, letterSpacing: 1 }}>RÉGIME</Typography>
        <Typography data-testid="regime-label" sx={{ fontWeight: 700 }}>
          {data?.regime ? REGIME_LABELS[data.regime] : '—'}
        </Typography>
        <Typography variant="caption" sx={{ opacity: 0.6 }}>
          conf {data?.confidence !== null && data?.confidence !== undefined ? `${Math.round(data.confidence * 100)}%` : '—'}
        </Typography>
      </Stack>
      {(data?.drivers ?? []).map((d) => (
        <Box
          key={d.key}
          data-testid={`driver-${d.key}`}
          onClick={(e) => setAnchor({ el: e.currentTarget, driver: d })}
          sx={{ cursor: 'pointer', borderLeft: '1px solid', borderColor: 'divider', pl: 2, opacity: d.state === null ? 0.5 : 1 }}
        >
          <Typography variant="caption" sx={{ opacity: 0.6, mr: 0.5 }}>{DRIVER_LABELS[d.key]}</Typography>
          <Typography component="span" sx={{ color: d.state ? STATE_COLOR[d.state] : 'inherit' }}>
            {d.state ? `${STATE_ARROW[d.state]} ` : ''}{driverValue(d)}
          </Typography>
        </Box>
      ))}
      <Box sx={{ ml: 'auto', display: 'flex', gap: 1 }}>
        <Chip size="small" variant="outlined" label={status.data?.mode ?? '—'} />
        <Chip
          size="small"
          variant="outlined"
          color={status.data && !status.data.trading_enabled ? 'error' : 'default'}
          label={status.data ? (status.data.trading_enabled ? 'kill:off' : 'kill:on') : 'kill:—'}
        />
      </Box>
      <Popover
        open={!!anchor}
        anchorEl={anchor?.el ?? null}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
      >
        {anchor && (
          <Box sx={{ p: 1.5, maxWidth: 380 }}>
            <Typography variant="subtitle2">{DRIVER_LABELS[anchor.driver.key]}</Typography>
            <Typography variant="body2" sx={{ mt: 0.5 }}>{anchor.driver.detail}</Typography>
            <Typography variant="caption" sx={{ opacity: 0.6 }}>
              {anchor.driver.as_of ? `mesuré ${fmtRelative(anchor.driver.as_of, Date.now())}` : 'non mesuré — exclu de l’agrégat'}
            </Typography>
          </Box>
        )}
      </Popover>
    </Box>
  );
}
```

(Vérifier la signature réelle de `fmtRelative` dans `src/lib/format.ts` — attendue `(iso, now)` ; sinon adapter. Si `STATE_COLOR` via `var(--mui-...)` ne rend pas dans les tests, utiliser `success.main`/`error.main` via `sx` conditionnel.)

- [ ] **Step 4 : Monter dans `AppShell.tsx`** — dans le `<Box component="main" ...>`, insérer `<RegimeStrip />` juste avant `{children}` (import en tête : `import { RegimeStrip } from './RegimeStrip';`).

- [ ] **Step 5 : Vérifier** — `npx vitest run src/components/layout/__tests__/RegimeStrip.test.tsx` → PASS ; `npm run typecheck` ; contrôle visuel `npm run dev` → bandeau présent sur toutes les pages, driver null grisé avec « — », popover au clic.

- [ ] **Step 6 : Commit**

```bash
git add frontend/src/components/layout/RegimeStrip.tsx frontend/src/components/layout/AppShell.tsx frontend/src/components/layout/__tests__/RegimeStrip.test.tsx
git commit -m "feat(frontend): bandeau regime global dans le shell"
```

---

### Task 10 : Decision Inspector global

**Files:**
- Create: `frontend/src/lib/hooks/useDecisionParam.ts`
- Create: `frontend/src/components/inspector/DecisionInspector.tsx`
- Modify: `frontend/src/components/layout/AppShell.tsx`
- Modify: `frontend/src/components/command/AiDecisionFeed.tsx`
- Test: `frontend/src/components/inspector/__tests__/DecisionInspector.test.tsx`

- [ ] **Step 1 : Hook `useDecisionParam.ts`** — le point délicat du patron `?decision=` global : **préserver les autres params** (sinon on écrase `?token=` sur /market) :

```tsx
'use client';

import { useCallback } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

/** Pilote le Decision Inspector global via ?decision=<id>, en préservant les
 *  autres search params (?token= sur /market notamment). */
export function useDecisionParam() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const decisionId = params.get('decision');

  const open = useCallback(
    (id: string) => {
      const next = new URLSearchParams(params);
      next.set('decision', id);
      router.push(`${pathname}?${next.toString()}`, { scroll: false });
    },
    [params, pathname, router],
  );

  const close = useCallback(() => {
    const next = new URLSearchParams(params);
    next.delete('decision');
    const qs = next.toString();
    router.push(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [params, pathname, router]);

  return { decisionId, open, close };
}
```

- [ ] **Step 2 : Test qui échoue** — même patron de mock router que `market/__tests__/page.test.tsx` :

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { DecisionInspector } from '../DecisionInspector';
import { getExplain } from '@/lib/mock/journal';

vi.mock('next/navigation', () => ({
  useRouter: vi.fn(),
  usePathname: vi.fn(),
  useSearchParams: vi.fn(),
}));
vi.mock('@/lib/api/endpoints', () => ({ explainApi: { get: vi.fn() } }));

import { explainApi } from '@/lib/api/endpoints';

const routerMock = vi.mocked(useRouter);
const pathnameMock = vi.mocked(usePathname);
const searchParamsMock = vi.mocked(useSearchParams);
const explainGet = vi.mocked(explainApi.get);

function setup(search: string) {
  routerMock.mockReturnValue({ push: vi.fn() } as unknown as ReturnType<typeof useRouter>);
  pathnameMock.mockReturnValue('/command');
  searchParamsMock.mockReturnValue(new URLSearchParams(search) as unknown as ReturnType<typeof useSearchParams>);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DecisionInspector />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('DecisionInspector', () => {
  it('reste fermé sans ?decision=', () => {
    setup('');
    expect(screen.queryByText(/Inspecteur/)).not.toBeInTheDocument();
    expect(explainGet).not.toHaveBeenCalled();
  });

  it('affiche le score brut 0-100 et les axes absents en « — »', async () => {
    explainGet.mockResolvedValue(getExplain('jr-1'));
    setup('decision=jr-1');
    expect(await screen.findByText('64')).toBeInTheDocument();        // jamais 0.64
    // fundamentals et developer_activity absents du mock → deux axes en tiret
    expect((await screen.findAllByText(/absent, exclu du score/)).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Triage Haiku/)).toBeInTheDocument();
  });

  it('affiche une erreur propre sur id inconnu', async () => {
    explainGet.mockRejectedValue(new Error('404'));
    setup('decision=unknown');
    expect(await screen.findByText(/introuvable|échoué/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3 : Vérifier l'échec** — `npx vitest run src/components/inspector/__tests__/DecisionInspector.test.tsx` → FAIL.

- [ ] **Step 4 : Implémenter `DecisionInspector.tsx`** — export wrappé en `Suspense` (obligatoire : `useSearchParams` dans un composant monté par le layout) :

```tsx
'use client';

import { Suspense } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Box, Chip, CircularProgress, Divider, Drawer, IconButton, LinearProgress,
  Stack, Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { explainApi } from '@/lib/api/endpoints';
import { apiErrorMessage } from '@/lib/api/client';
import { useDecisionParam } from '@/lib/hooks/useDecisionParam';
import { AXIS_LABELS, SCORE_AXES, axisValue } from '@/lib/types/dossier';

const KIND_LABEL: Record<string, string> = {
  price: 'Prix', sentiment: 'Sentiment', analysis: 'Haiku',
  decision: 'Sonnet', risk: 'Risque', order: 'Ordre',
};

function InspectorContent() {
  const { decisionId, close } = useDecisionParam();
  const { data, isLoading, error } = useQuery({
    queryKey: ['decision', 'explain', decisionId],
    queryFn: () => explainApi.get(decisionId!),
    enabled: !!decisionId,
  });

  return (
    <Drawer
      anchor="right"
      open={!!decisionId}
      onClose={close}
      PaperProps={{ sx: { width: { xs: '100%', sm: 560 }, bgcolor: 'rgba(8,11,20,0.92)', backdropFilter: 'blur(16px)', p: 2.5 } }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="overline" sx={{ letterSpacing: 2 }}>Inspecteur de décision</Typography>
        <IconButton aria-label="Fermer" onClick={close}><CloseIcon /></IconButton>
      </Stack>
      {isLoading && (
        <Stack alignItems="center" sx={{ py: 6 }}><CircularProgress size={28} /></Stack>
      )}
      {error != null && (
        <Typography color="error" sx={{ mt: 2 }}>
          Décision introuvable ou requête échouée — {apiErrorMessage(error, 'la requête a échoué')}
        </Typography>
      )}
      {data && (
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          {/* En-tête : échelle brute 0-100 uniquement */}
          <Stack direction="row" spacing={1.5} alignItems="baseline">
            <Typography variant="h5">{data.symbol ?? '—'}</Typography>
            {data.direction && <Chip size="small" label={data.direction.toUpperCase()} />}
            <Typography variant="h4" className="mono">{data.score.value ?? '—'}</Typography>
            <Typography variant="caption" sx={{ opacity: 0.6 }}>/100 · conf {data.score.confidence ?? '—'}</Typography>
          </Stack>

          {/* Waterfall des axes — rendu depuis SCORE_AXES, agnostique au nombre */}
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>Axes de scoring</Typography>
            {data.score.insufficient_evidence ? (
              <Typography variant="body2" sx={{ opacity: 0.7 }}>
                breakdown indisponible (décision pré-v2 ou rejetée avant scoring)
              </Typography>
            ) : (
              SCORE_AXES.map((axis) => {
                const v = axisValue(data.score.axes, axis);
                return (
                  <Stack key={axis} direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                    <Typography variant="caption" sx={{ width: 120, opacity: 0.7 }}>{AXIS_LABELS[axis]}</Typography>
                    {v === null ? (
                      <Typography variant="caption" sx={{ opacity: 0.5 }}>— (absent, exclu du score)</Typography>
                    ) : (
                      <>
                        <LinearProgress variant="determinate" value={v * 100} sx={{ flex: 1, height: 6, borderRadius: 3 }} />
                        <Typography variant="caption" className="mono">{v.toFixed(2)}</Typography>
                      </>
                    )}
                  </Stack>
                );
              })
            )}
          </Box>

          {/* Triage — namespace disjoint, étiqueté comme tel */}
          {data.triage && (
            <Box>
              <Typography variant="subtitle2">Triage Haiku <Typography component="span" variant="caption" sx={{ opacity: 0.5 }}>(facteurs de triage — distincts des axes)</Typography></Typography>
              <Stack direction="row" spacing={1} sx={{ mt: 0.5, flexWrap: 'wrap' }}>
                {Object.entries(data.triage.factors).map(([k, v]) => (
                  <Chip key={k} size="small" variant="outlined" label={`${k} ${typeof v === 'number' ? v.toFixed(2) : '—'}`} />
                ))}
              </Stack>
              <Typography variant="caption" sx={{ opacity: 0.7 }}>
                escaladé : {data.triage.escalated ? 'oui' : 'non'} · Sonnet : {data.triage.sonnet_called ? (data.triage.sonnet_validated === null ? 'appelé' : data.triage.sonnet_validated ? 'validé' : 'refusé') : 'non appelé'}
                {data.triage.skip_reason ? ` · skip : ${data.triage.skip_reason}` : ''}
              </Typography>
            </Box>
          )}

          {/* Verdict risque */}
          {data.risk && (
            <Box>
              <Typography variant="subtitle2">Risque</Typography>
              <Typography variant="body2">
                {data.risk.verdict ?? '—'}{data.risk.reason ? ` — ${data.risk.reason}` : ''}
              </Typography>
            </Box>
          )}

          {/* Contrefactuel */}
          <Box>
            <Typography variant="subtitle2">Contrefactuel</Typography>
            {data.counterfactual ? (
              <Typography variant="body2" className="mono">
                {data.counterfactual.outcome ?? '—'} · {data.counterfactual.pnl_pct !== null ? `${data.counterfactual.pnl_pct > 0 ? '+' : ''}${data.counterfactual.pnl_pct}%` : '—'} @ {data.counterfactual.horizon}
              </Typography>
            ) : (
              <Typography variant="body2" sx={{ opacity: 0.6 }}>non jugé (pas de niveaux entry/SL/TP)</Typography>
            )}
          </Box>

          <Divider />

          {/* Timeline */}
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>Timeline</Typography>
            {data.trace ? (
              data.trace.stages.map((s, i) => (
                <Stack key={i} direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5, opacity: s.reached ? 1 : 0.4 }}>
                  <Chip size="small" label={`${i + 1} · ${KIND_LABEL[s.kind] ?? s.kind}`} />
                  <Typography variant="caption">{s.summary}</Typography>
                </Stack>
              ))
            ) : (
              <Typography variant="body2" sx={{ opacity: 0.6 }}>
                pas de lineage par correlation id (~95 % du flux) — lien par (symbole, temps) non disponible pour cette décision
              </Typography>
            )}
          </Box>
        </Stack>
      )}
    </Drawer>
  );
}

export function DecisionInspector() {
  return (
    <Suspense fallback={null}>
      <InspectorContent />
    </Suspense>
  );
}
```

- [ ] **Step 5 : Monter dans `AppShell.tsx`** — après `{children}` dans le `<Box component="main">` : `<DecisionInspector />` (+ import).

- [ ] **Step 6 : Câbler un ouvreur dans `AiDecisionFeed.tsx`** — ouvrir le fichier ; les lignes du feed portent des `WorkerDecision` dont `id` **est** `Decision.event_id` (cf. `map_decision`). Ajouter en tête `import { useDecisionParam } from '@/lib/hooks/useDecisionParam';`, dans le composant `const { open } = useDecisionParam();`, et sur l'élément racine de chaque ligne rendue : `onClick={() => open(d.id)}` + `sx={{ cursor: 'pointer' }}` (adapter `d` au nom de la variable de map locale). **Ne pas** modifier `LiveEventStream` : ses lignes portent des correlation ids, pas des ids de décision — il garde la trace drawer existante.

- [ ] **Step 7 : Vérifier** — `npx vitest run src/components/inspector` → PASS ; `npm run test:run` (le test de la page /command doit rester vert : si son mock `next/navigation` ne fournit pas `usePathname`, l'y ajouter) ; `npm run typecheck` ; contrôle visuel : `npm run dev`, ouvrir `/command`, cliquer une décision du feed → drawer ; vérifier sur `/market?token=BTC` que l'ouverture de l'inspecteur **conserve** `?token=`.

- [ ] **Step 8 : Commit**

```bash
git add frontend/src/lib/hooks/useDecisionParam.ts frontend/src/components/inspector frontend/src/components/layout/AppShell.tsx frontend/src/components/command/AiDecisionFeed.tsx
git commit -m "feat(frontend): decision inspector global pilote par ?decision="
```

---

### Task 11 : Page `/journal`

**Files:**
- Create: `frontend/src/app/(app)/journal/page.tsx`
- Create: `frontend/src/components/journal/JournalTable.tsx`, `CalibrationPanel.tsx`, `AttributionPanel.tsx`
- Modify: `frontend/src/components/layout/navItems.ts`
- Test: `frontend/src/app/(app)/journal/__tests__/page.test.tsx`

- [ ] **Step 1 : Entrée de navigation** — dans `navItems.ts`, après l'entrée `/trading` :

```ts
  { href: '/journal', label: 'Journal', icon: FactCheckIcon, description: 'Contrefactuel & calibration' },
```

(+ `import FactCheckIcon from '@mui/icons-material/FactCheck';`)

- [ ] **Step 2 : Test qui échoue**

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import JournalPage from '../page';
import { getJournalAttribution, getJournalCalibration, getJournalDecisions } from '@/lib/mock/journal';

vi.mock('next/navigation', () => ({
  useRouter: vi.fn(), usePathname: vi.fn(), useSearchParams: vi.fn(),
}));
vi.mock('@/lib/api/endpoints', () => ({
  journalApi: { decisions: vi.fn(), calibration: vi.fn(), attribution: vi.fn() },
}));

import { journalApi } from '@/lib/api/endpoints';

const decisionsMock = vi.mocked(journalApi.decisions);
const calibrationMock = vi.mocked(journalApi.calibration);
const attributionMock = vi.mocked(journalApi.attribution);
const pushMock = vi.fn();

function setup() {
  vi.mocked(useRouter).mockReturnValue({ push: pushMock } as unknown as ReturnType<typeof useRouter>);
  vi.mocked(usePathname).mockReturnValue('/journal');
  vi.mocked(useSearchParams).mockReturnValue(new URLSearchParams('') as unknown as ReturnType<typeof useSearchParams>);
  decisionsMock.mockResolvedValue(getJournalDecisions('30d', 50, 0));
  calibrationMock.mockResolvedValue(getJournalCalibration('30d', 70));
  attributionMock.mockResolvedValue(getJournalAttribution('30d'));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><JournalPage /></QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('/journal', () => {
  it('rend les trois panneaux', async () => {
    setup();
    expect(await screen.findByText(/Décisions jugées/)).toBeInTheDocument();
    expect(screen.getByText(/Calibration de seuil/)).toBeInTheDocument();
    expect(screen.getByText(/Attribution/)).toBeInTheDocument();
  });

  it('un facteur sous min_n rend « — », pas un zéro', async () => {
    setup();
    // liquidity a n=14 < 20 dans le mock → corrélation nulle → tiret
    const liquidityRow = await screen.findByTestId('attribution-liquidity');
    expect(liquidityRow).toHaveTextContent('—');
  });

  it('clic sur une ligne ouvre ?decision=', async () => {
    setup();
    // Les lignes DataGrid portent role="row" ; la première ligne de données
    // suit la ligne d'en-tête.
    const rows = await screen.findAllByRole('row');
    fireEvent.click(rows[1]);
    expect(pushMock).toHaveBeenCalled();
    expect(String(pushMock.mock.calls[0][0])).toContain('decision=jr-');
  });
});
```

(Ajouter `fireEvent` à l'import Testing Library. Si le DataGrid virtualise et ne rend pas les lignes en jsdom, remplacer ce test par un test unitaire de `JournalTable` qui appelle directement la prop `onSelect` via `onRowClick` — l'intention à préserver : une ligne cliquée pousse `?decision=<event_id>`.)

- [ ] **Step 3 : Vérifier l'échec** — `npx vitest run "src/app/(app)/journal"` → FAIL.

- [ ] **Step 4 : Implémenter les trois composants** — `JournalTable.tsx` (patron exact `TokensTable` : DataGrid **sans** `autoHeight`, hauteur calculée) :

```tsx
'use client';

import { DataGrid, type GridColDef, type GridRowParams } from '@mui/x-data-grid';
import { Box, Chip, Typography } from '@mui/material';
import { EmptyState } from '@/components/common';
import type { JournalRow } from '@/lib/types/journal';
import { fmtDateTime } from '@/lib/format';

const VISIBLE_ROWS = 12;
const ROW_HEIGHT = 44;
const HEADER_HEIGHT = 48;
const GRID_HEIGHT = HEADER_HEIGHT + VISIBLE_ROWS * ROW_HEIGHT;

const COLUMNS: GridColDef<JournalRow>[] = [
  { field: 'time', headerName: 'Date', width: 150, valueFormatter: (v: string | null) => (v ? fmtDateTime(v) : '—') },
  { field: 'symbol', headerName: 'Symbole', width: 90 },
  { field: 'score', headerName: 'Score', width: 70, valueFormatter: (v: number | null) => v ?? '—' },
  { field: 'direction', headerName: 'Dir.', width: 70, valueFormatter: (v: string | null) => v ?? '—' },
  {
    field: 'passed', headerName: 'Verdict', width: 110,
    renderCell: (p) => (
      <Chip size="small" variant="outlined" color={p.row.passed ? 'success' : 'default'} label={p.row.passed ? (p.row.risk_verdict ?? 'passé') : 'rejeté'} />
    ),
  },
  {
    field: 'pnl_pct', headerName: 'PnL simulé', width: 110,
    renderCell: (p) => (
      <Typography variant="body2" className="mono" color={p.row.pnl_pct === null ? 'text.disabled' : p.row.pnl_pct > 0 ? 'success.main' : 'error.main'}>
        {p.row.pnl_pct === null ? '—' : `${p.row.pnl_pct > 0 ? '+' : ''}${p.row.pnl_pct}%`}
      </Typography>
    ),
  },
  { field: 'outcome', headerName: 'Résultat', width: 110, valueFormatter: (v: string | null) => v ?? '—' },
];

export function JournalTable({ rows, loading, onSelect }: {
  rows: JournalRow[];
  loading: boolean;
  onSelect: (eventId: string) => void;
}) {
  if (!loading && rows.length === 0) return <EmptyState message="Aucune décision jugée sur la fenêtre." />;
  return (
    <Box sx={{ height: GRID_HEIGHT }}>
      <DataGrid
        rows={rows}
        columns={COLUMNS}
        getRowId={(r) => r.event_id}
        loading={loading}
        rowHeight={ROW_HEIGHT}
        columnHeaderHeight={HEADER_HEIGHT}
        density="compact"
        disableColumnMenu
        hideFooter
        onRowClick={(p: GridRowParams<JournalRow>) => onSelect(p.row.event_id)}
        sx={{ border: 0, '& .MuiDataGrid-row:hover': { cursor: 'pointer' } }}
      />
    </Box>
  );
}
```

`CalibrationPanel.tsx` :

```tsx
'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box, Slider, Stack, Typography } from '@mui/material';
import { journalApi } from '@/lib/api/endpoints';
import type { CalibrationBucket, JournalWindow } from '@/lib/types/journal';

function BucketStats({ title, bucket }: { title: string; bucket: CalibrationBucket | null }) {
  if (!bucket) return <Typography variant="body2" sx={{ opacity: 0.6 }}>{title} : seuil inconnu de ce conteneur — « — »</Typography>;
  return (
    <Box>
      <Typography variant="caption" sx={{ opacity: 0.6 }}>{title} (seuil {bucket.threshold})</Typography>
      <Typography variant="body2" className="mono">
        {bucket.selected} sél. · {bucket.judged} jugées ·{' '}
        {bucket.sufficient
          ? `win ${Math.round((bucket.win_rate ?? 0) * 100)}% · PnL ${bucket.total_pnl_pct! > 0 ? '+' : ''}${bucket.total_pnl_pct}%`
          : `— (échantillon insuffisant, n < ${20})`}
      </Typography>
    </Box>
  );
}

export function CalibrationPanel({ window }: { window: JournalWindow }) {
  const [threshold, setThreshold] = useState(70);
  const [applied, setApplied] = useState(70);
  const { data } = useQuery({
    queryKey: ['journal', 'calibration', window, applied],
    queryFn: () => journalApi.calibration(applied, window),
    placeholderData: (prev) => prev,
  });

  return (
    <Stack spacing={1.5}>
      <Typography variant="subtitle2">Calibration de seuil</Typography>
      <Slider
        value={threshold}
        min={0}
        max={100}
        onChange={(_, v) => setThreshold(v as number)}
        onChangeCommitted={(_, v) => setApplied(v as number)}
        valueLabelDisplay="auto"
        size="small"
      />
      <BucketStats title="Seuil simulé" bucket={data?.requested ?? null} />
      <BucketStats title="Seuil actuel" bucket={data?.current ?? null} />
    </Stack>
  );
}
```

(Le `min_n` en dur dans le texte : remplacer par `data.min_n` à l'implémentation.)

`AttributionPanel.tsx` :

```tsx
'use client';

import { useQuery } from '@tanstack/react-query';
import { Box, LinearProgress, Stack, Typography } from '@mui/material';
import { journalApi } from '@/lib/api/endpoints';
import type { JournalWindow } from '@/lib/types/journal';

export function AttributionPanel({ window }: { window: JournalWindow }) {
  const { data } = useQuery({
    queryKey: ['journal', 'attribution', window],
    queryFn: () => journalApi.attribution(window),
  });

  return (
    <Stack spacing={1}>
      <Typography variant="subtitle2">Attribution (facteurs de triage Haiku)</Typography>
      <Typography variant="caption" sx={{ opacity: 0.6 }}>
        Corrélation facteur ↔ PnL simulé @ {data?.horizon ?? '—'} — l’attribution par les 8 axes exige des décisions passées.
      </Typography>
      {(data?.factors ?? []).map((f) => (
        <Stack key={f.key} direction="row" spacing={1} alignItems="center" data-testid={`attribution-${f.key}`}>
          <Typography variant="caption" sx={{ width: 90, opacity: 0.7 }}>{f.key}</Typography>
          {f.correlation === null ? (
            <Typography variant="caption" sx={{ opacity: 0.5 }}>— (n={f.n} &lt; {data?.min_n})</Typography>
          ) : (
            <>
              <Box sx={{ flex: 1 }}>
                <LinearProgress variant="determinate" value={Math.abs(f.correlation) * 100} color={f.correlation >= 0 ? 'success' : 'error'} sx={{ height: 6, borderRadius: 3 }} />
              </Box>
              <Typography variant="caption" className="mono">{f.correlation > 0 ? '+' : ''}{f.correlation.toFixed(2)} (n={f.n})</Typography>
            </>
          )}
        </Stack>
      ))}
    </Stack>
  );
}
```

- [ ] **Step 5 : La page `journal/page.tsx`** :

```tsx
'use client';

import { Suspense, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box, Stack, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import { PageHeader } from '@/components/common';
import { SectionCard } from '@/components/systems/common';
import { JournalTable } from '@/components/journal/JournalTable';
import { CalibrationPanel } from '@/components/journal/CalibrationPanel';
import { AttributionPanel } from '@/components/journal/AttributionPanel';
import { journalApi } from '@/lib/api/endpoints';
import { useDecisionParam } from '@/lib/hooks/useDecisionParam';
import type { JournalWindow } from '@/lib/types/journal';

function JournalContent() {
  const [window, setWindow] = useState<JournalWindow>('30d');
  const { open } = useDecisionParam();
  const decisions = useQuery({
    queryKey: ['journal', 'decisions', window],
    queryFn: () => journalApi.decisions(window),
    refetchInterval: 60_000,
  });

  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <PageHeader
        title="Journal contrefactuel"
        subtitle="L'edge fonctionne-t-il ? Décisions jugées, calibration de seuil, attribution"
        actions={
          <ToggleButtonGroup size="small" exclusive value={window} onChange={(_, v) => v && setWindow(v)}>
            <ToggleButton value="7d">7j</ToggleButton>
            <ToggleButton value="30d">30j</ToggleButton>
            <ToggleButton value="90d">90j</ToggleButton>
          </ToggleButtonGroup>
        }
      />
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: '2fr 1fr' }, alignItems: 'start' }}>
        <SectionCard title="Décisions jugées" subtitle={`${decisions.data?.total ?? '—'} sur la fenêtre · horizon ${decisions.data?.horizon ?? '—'}`}>
          <JournalTable rows={decisions.data?.rows ?? []} loading={decisions.isLoading} onSelect={open} />
        </SectionCard>
        <Stack spacing={2}>
          <SectionCard title="Calibration de seuil"><CalibrationPanel window={window} /></SectionCard>
          <SectionCard title="Attribution"><AttributionPanel window={window} /></SectionCard>
        </Stack>
      </Box>
      {decisions.data?.current_threshold !== null && decisions.data?.current_threshold !== undefined && (
        <Typography variant="caption" sx={{ opacity: 0.6, mt: 1, display: 'block' }}>
          Seuil actuellement appliqué par risk-engine : {decisions.data.current_threshold}
        </Typography>
      )}
    </Box>
  );
}

export default function JournalPage() {
  return (
    <Suspense fallback={null}>
      <JournalContent />
    </Suspense>
  );
}
```

(Vérifier les props exactes de `SectionCard` dans `src/components/systems/common.tsx` — `{title, subtitle?, ...}` attendu. Si `PageHeader` n'a pas de prop `actions`, placer le ToggleButtonGroup dans un `Stack direction="row" justifyContent="space-between"` au-dessus de la grille.)

- [ ] **Step 6 : Vérifier** — `npx vitest run "src/app/(app)/journal"` → PASS (ajuster le sélecteur de ligne du 3ᵉ test au DOM réel du DataGrid) ; `npm run typecheck` ; `npm run test:run` complet ; contrôle visuel `npm run dev` → `/journal` accessible depuis la nav, clic ligne → inspecteur.

- [ ] **Step 7 : Commit**

```bash
git add frontend/src/app/\(app\)/journal frontend/src/components/journal frontend/src/components/layout/navItems.ts
git commit -m "feat(frontend): page /journal — decisions jugees, calibration, attribution"
```

---

### Task 12 : Vérification finale

- [ ] **Step 1 : Backend complet** — depuis la racine :

```bash
make lint    # ruff + black --check + mypy strict
make test    # pytest root — contract, regime, explain, calibration, + suites existantes
```

Attendu : 0 erreur. Le méta-test `test_every_contract_entry_is_actually_asserted` confirme que les 8 nouvelles entrées du manifeste sont toutes assertées.

- [ ] **Step 2 : Frontend complet** :

```bash
cd frontend && npm run typecheck && npm run test:run && npm run build
```

Attendu : 0 erreur — le `build` attrape les violations `useSearchParams`-sans-`Suspense` que le typecheck ne voit pas.

- [ ] **Step 3 : Revue null-vs-zéro dédiée** (exigence de la spec, § Risques) — relire les diffs en cherchant : un `?? 0`, un `|| 0`, un `.toFixed()` sur une valeur nullable, un `float(x or 0)`, un driver `neutral` là où la donnée était absente. Chaque trouvaille se corrige en `null`/`'—'`.

- [ ] **Step 4 : Commit final et récapitulatif** — état attendu : ~12 commits, 4 endpoints au manifeste, 2 composants globaux montés, 1 page. Signaler en fin d'exécution : le harness live (`scripts/verify_read_live.py`) reste à lancer **dans le conteneur** au prochain déploiement pour valider contre la vraie DB/Redis.
