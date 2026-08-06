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
from typing import Any, cast

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
    return cast(Cache, request.app.state.cache)


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
) -> dict[str, Any]:
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
    payload = regime.build_regime(drivers, computed_at=datetime.now(tz=UTC).isoformat())
    REGIME_CACHE.put(now, payload)
    return payload
