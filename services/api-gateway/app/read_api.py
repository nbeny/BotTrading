"""Read-only REST endpoints backing the web terminal's *live* mode.

These mirror the frontend contract (see ``frontend/src/lib/api/endpoints.ts``)
and are served at the gateway root (the Next.js rewrite maps
``/api/gateway/*`` → ``api-gateway:8000/*``). Everything here is derived from
tables already persisted by collectors / workers / the persister:

    market  ← tokens, prices, news, signals, decisions
    data    ← raw_content (+ its sentiment scoring)

The query layer is intentionally thin; the row→response mapping and the stats
computation are pure functions (``_map_*`` / ``compute_content_stats``) so they
can be unit-tested without a database.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cmi_common.db import Decision, News, Price, Sentiment, Signal, Token, Trade
from cmi_common.db.models import RawContent

from .routers import get_session_dep

router = APIRouter(tags=["read"])

_RANGE_TO_DELTA = {
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}


def _num(v: Any) -> float:
    """Coerce Decimal/None to a plain float for JSON."""
    return float(v) if v is not None else 0.0


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


# ── pure mappers (unit-tested) ────────────────────────────────────────────────
def map_token(
    price: Any,
    *,
    meta: Any | None = None,
    opportunity_score: float | None = None,
    sentiment_score: float | None = None,
) -> dict:
    """Build a MarketToken from the latest price row (+ optional enrichments)."""
    change = price.price_change_pct_24h
    return {
        "symbol": price.symbol,
        "coin_id": getattr(meta, "coin_id", None) or price.symbol.lower(),
        "name": getattr(meta, "name", None) or price.symbol,
        "price_usd": _num(price.price_usd),
        "price_change_pct_24h": _num(change),
        "volume_24h_usd": _num(price.volume_24h_usd),
        "liquidity_usd": 0.0,  # not persisted (DexEvent liquidity is transient)
        "market_cap_usd": _num(price.market_cap_usd),
        "sentiment_score": round(sentiment_score, 2) if sentiment_score is not None else 0.0,
        "opportunity_score": round(_num(opportunity_score) / 100, 2) if opportunity_score else 0.0,
        "is_trending": bool(change is not None and change >= 5),
        "updated_at": _iso(price.time),
    }


def map_price_point(row: Any) -> dict:
    return {
        "t": _iso(row.time),
        "price": _num(row.price_usd),
        "volume": _num(row.volume_24h_usd),
    }


def map_news(row: Any) -> dict:
    return {
        "id": str(row.id),
        "title": row.title,
        "url": row.url,
        "source": row.source_name,
        "symbols": list(row.symbols or []),
        "sentiment": _num(row.provider_sentiment),
        # News.published_at is a unix-epoch BigInteger
        "published_at": datetime.fromtimestamp(row.published_at, tz=timezone.utc).isoformat()
        if row.published_at
        else None,
    }


def map_signal_event(row: Any) -> dict:
    """A signals row surfaced as an AnalysisEvent (the frontend's signal union)."""
    return {
        "event_type": "AnalysisEvent",
        "source": "haiku_worker",
        "symbol": row.symbol,
        "opportunity_score": row.opportunity_score,
        "confidence": _num(row.confidence),
        "reason": row.reason,
        "escalate": bool(row.escalated),
        "occurred_at": _iso(row.time),
    }


def map_decision(row: Any) -> dict:
    """A decisions row surfaced as the terminal's WorkerDecision."""
    return {
        "id": str(row.event_id),
        "symbol": row.symbol,
        "worker": "sonnet",
        "decision": f"{str(row.direction).upper()}_SIGNAL" if row.direction else "HOLD",
        "opportunity_score": round(_num(row.opportunity_score) / 100, 2),
        "confidence": _num(row.confidence),
        "justification": row.rationale,
        "escalated": False,
        "created_at": _iso(row.created_at),
    }


_KIND_TO_CATEGORY = {"social": "social", "news": "news"}


def map_content(row: Any) -> dict:
    score = row.sentiment_score
    has_decision = score is not None and (score > 0.55 or score < -0.45)
    text = row.text or ""
    return {
        "id": str(row.id),
        "platform": row.source,
        "source_category": _KIND_TO_CATEGORY.get(row.kind, "market"),
        "symbols": list(row.symbols or []),
        "title": row.title or (text[:80] if text else row.source),
        "snippet": text[:200],
        "url": row.url,
        "published_at": _iso(row.published_at),
        "collected_at": _iso(row.fetched_at),
        "sentiment_score": round(_num(score), 2),
        "sentiment_confidence": _num(row.sentiment_confidence),
        "model_name": row.sentiment_model or "unscored",
        "sample_size": 1,
        "derived_decision": (
            {
                "direction": "long" if (score or 0) > 0 else "short",
                "opportunity_score": None,
                "correlation_id": None,
            }
            if has_decision
            else None
        ),
    }


def compute_content_stats(rows: Iterable[Any], *, now: datetime | None = None) -> dict:
    """Aggregate a bounded window of raw_content rows into DataStats. Pure."""
    now = now or datetime.now(tz=timezone.utc)
    rows = list(rows)
    by_cat: Counter[str] = Counter()
    src: Counter[str] = Counter()
    mentions: Counter[str] = Counter()
    vol: dict[int, dict[str, int]] = defaultdict(lambda: {"social": 0, "news": 0, "market": 0})
    sent: dict[int, list[float]] = defaultdict(list)
    score_sum = 0.0
    score_n = 0

    for r in rows:
        cat = _KIND_TO_CATEGORY.get(r.kind, "market")
        by_cat[cat] += 1
        src[r.source] += 1
        for sym in (r.symbols or []):
            mentions[sym] += 1
        if r.sentiment_score is not None:
            score_sum += float(r.sentiment_score)
            score_n += 1
        ts = r.published_at or r.fetched_at
        if isinstance(ts, datetime):
            age_h = int((now - ts).total_seconds() // 3600)
            if 0 <= age_h < 12:
                bucket = 11 - age_h
                vol[bucket][cat] += 1
                if r.sentiment_score is not None:
                    sent[bucket].append(float(r.sentiment_score))

    def label(bucket: int) -> str:
        hour = (now - timedelta(hours=11 - bucket)).hour
        return f"{hour:02d}h"

    volume_series = [
        {"hour": label(b), **{k: vol[b][k] for k in ("social", "news", "market")}}
        for b in range(12)
    ]
    sentiment_series = [
        {"hour": label(b), "sentiment": round(sum(sent[b]) / len(sent[b]), 2) if sent[b] else 0.0}
        for b in range(12)
    ]
    return {
        "total_24h": len(rows),
        "social_24h": by_cat["social"],
        "news_24h": by_cat["news"],
        "market_24h": by_cat["market"],
        "avg_sentiment": round(score_sum / score_n, 2) if score_n else 0.0,
        "volume_series": volume_series,
        "sentiment_series": sentiment_series,
        "top_sources": [{"source": s, "count": c} for s, c in src.most_common(6)],
        "mentions": [{"symbol": s, "count": c} for s, c in mentions.most_common(8)],
        "updated_at": now.isoformat(),
    }


# ── latest-per-symbol helpers ─────────────────────────────────────────────────
def _latest_per_symbol(model: Any, value_col: Any, time_col: Any):
    """Subquery-join selecting the newest row per symbol for `model`."""
    newest = (
        select(model.symbol.label("symbol"), func.max(time_col).label("t"))
        .group_by(model.symbol)
        .subquery()
    )
    return select(model).join(
        newest, and_(model.symbol == newest.c.symbol, time_col == newest.c.t)
    )


# ── market endpoints ──────────────────────────────────────────────────────────
@router.get("/market/tokens")
async def market_tokens(session: AsyncSession = Depends(get_session_dep)) -> list[dict]:
    prices = (await session.execute(_latest_per_symbol(Price, Price.price_usd, Price.time))).scalars().all()
    tokens = (await session.execute(select(Token))).scalars().all()
    meta = {t.symbol: t for t in tokens}
    sigs = (await session.execute(_latest_per_symbol(Signal, Signal.opportunity_score, Signal.time))).scalars().all()
    opp = {s.symbol: s.opportunity_score for s in sigs}
    sents = (await session.execute(_latest_per_symbol(Sentiment, Sentiment.sentiment_score, Sentiment.time))).scalars().all()
    sent = {s.symbol: s.sentiment_score for s in sents}
    return [
        map_token(p, meta=meta.get(p.symbol), opportunity_score=opp.get(p.symbol), sentiment_score=sent.get(p.symbol))
        for p in prices
    ]


@router.get("/market/tokens/{symbol}")
async def market_token(symbol: str, session: AsyncSession = Depends(get_session_dep)) -> dict:
    sym = symbol.upper()
    stmt = select(Price).where(Price.symbol == sym).order_by(Price.time.desc()).limit(1)
    price = (await session.execute(stmt)).scalars().first()
    if price is None:
        return {}
    meta = (await session.execute(select(Token).where(Token.symbol == sym))).scalars().first()
    return map_token(price, meta=meta)


@router.get("/market/tokens/{symbol}/prices")
async def market_token_prices(
    symbol: str,
    range: str = Query("1d"),
    session: AsyncSession = Depends(get_session_dep),
) -> list[dict]:
    since = datetime.now(tz=timezone.utc) - _RANGE_TO_DELTA.get(range, timedelta(days=1))
    stmt = (
        select(Price)
        .where(and_(Price.symbol == symbol.upper(), Price.time >= since))
        .order_by(Price.time.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [map_price_point(r) for r in rows]


@router.get("/market/news")
async def market_news(
    limit: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(get_session_dep),
) -> list[dict]:
    stmt = select(News).order_by(News.published_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [map_news(r) for r in rows]


@router.get("/market/signals")
async def market_signals(
    limit: int = Query(30, ge=1, le=500),
    session: AsyncSession = Depends(get_session_dep),
) -> list[dict]:
    stmt = select(Signal).order_by(Signal.time.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [map_signal_event(r) for r in rows]


@router.get("/market/decisions")
async def market_decisions(
    limit: int = Query(30, ge=1, le=500),
    session: AsyncSession = Depends(get_session_dep),
) -> list[dict]:
    stmt = select(Decision).order_by(Decision.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [map_decision(r) for r in rows]


# ── data explorer endpoints ───────────────────────────────────────────────────
@router.get("/data/content")
async def data_content(
    category: str = Query("all"),
    symbol: str | None = Query(None),
    q: str | None = Query(None),
    sentiment: str = Query("all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session_dep),
) -> dict:
    conds = []
    if category in ("social", "news"):
        conds.append(RawContent.kind == category)
    if symbol:
        conds.append(RawContent.symbols.contains([symbol.upper()]))
    if q:
        like = f"%{q}%"
        conds.append(or_(RawContent.title.ilike(like), RawContent.text.ilike(like)))
    if sentiment == "pos":
        conds.append(RawContent.sentiment_score > 0.15)
    elif sentiment == "neg":
        conds.append(RawContent.sentiment_score < -0.15)
    elif sentiment == "neu":
        conds.append(and_(RawContent.sentiment_score >= -0.15, RawContent.sentiment_score <= 0.15))

    where = and_(*conds) if conds else None
    count_stmt = select(func.count()).select_from(RawContent)
    if where is not None:
        count_stmt = count_stmt.where(where)
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = select(RawContent).order_by(RawContent.fetched_at.desc())
    if where is not None:
        stmt = stmt.where(where)
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return {"items": [map_content(r) for r in rows], "total": int(total), "offset": offset, "limit": limit}


def assemble_trace(cid: str, signal: Any | None, decision: Any | None, trade: Any | None) -> dict:
    """Reconstruct an end-to-end DecisionTrace from persisted rows. Pure.

    Stages map to what the pipeline durably records: analysis (signals) →
    decision (decisions) → risk + order (trades). Price/sentiment context is
    read from the analysis signal's payload when available.
    """
    p = getattr(signal, "payload", None) or {}
    symbol = (
        getattr(signal, "symbol", None)
        or getattr(decision, "symbol", None)
        or getattr(trade, "symbol", None)
        or "?"
    )
    filled = bool(trade and str(getattr(trade, "status", "")).lower() in {"filled", "closed"})
    stages = [
        {
            "kind": "price",
            "at": _iso(getattr(signal, "time", None)),
            "reached": "price_change_pct_24h" in p,
            "summary": f"Contexte prix {symbol}",
            "detail": {"change_24h_pct": p.get("price_change_pct_24h"), "volume_spike": p.get("volume_spike_ratio")},
        },
        {
            "kind": "sentiment",
            "at": _iso(getattr(signal, "time", None)),
            "reached": p.get("sentiment_score") is not None,
            "summary": "Sentiment agrégé",
            "detail": {"score": p.get("sentiment_score"), "social_growth": p.get("social_growth")},
        },
        {
            "kind": "analysis",
            "at": _iso(getattr(signal, "time", None)),
            "reached": signal is not None,
            "summary": "Haiku — triage",
            "detail": {
                "opportunity_score": getattr(signal, "opportunity_score", None),
                "confidence": _num(getattr(signal, "confidence", None)) if signal else None,
                "escalate": bool(getattr(signal, "escalated", False)) if signal else None,
            },
        },
        {
            "kind": "decision",
            "at": _iso(getattr(decision, "created_at", None)),
            "reached": decision is not None,
            "summary": "Sonnet — décision",
            "detail": {
                "direction": getattr(decision, "direction", None),
                "confidence": _num(getattr(decision, "confidence", None)) if decision else None,
                "ai_validated": bool(getattr(decision, "ai_validated", False)) if decision else None,
            },
        },
        {
            "kind": "risk",
            "at": _iso(getattr(trade, "created_at", None)),
            "reached": trade is not None,
            "summary": "Risque — sizing & protection",
            "detail": {
                "size_pct": _num(getattr(trade, "position_size_pct", None)) if trade else None,
                "stop_loss": _num(getattr(trade, "stop_loss", None)) if trade else None,
                "take_profit": _num(getattr(trade, "take_profit", None)) if trade else None,
                "rr": _num(getattr(trade, "risk_reward_ratio", None)) if trade else None,
            },
        },
        {
            "kind": "order",
            "at": _iso(getattr(trade, "created_at", None)),
            "reached": filled,
            "summary": "Ordre exécuté" if filled else "Ordre en attente",
            "detail": {
                "status": getattr(trade, "status", None) if trade else None,
                "fill_price": _num(getattr(trade, "fill_price", None)) if trade and getattr(trade, "fill_price", None) else None,
                "pnl": _num(getattr(trade, "pnl", None)) if trade and getattr(trade, "pnl", None) is not None else None,
            },
        },
    ]
    return {"correlation_id": cid, "symbol": symbol, "stages": stages}


@router.get("/trace/{cid}")
async def trace(cid: str, session: AsyncSession = Depends(get_session_dep)) -> dict:
    sig = (
        await session.execute(
            select(Signal).where(Signal.payload["correlation_id"].astext == cid).order_by(Signal.time.desc()).limit(1)
        )
    ).scalars().first()
    dec = (
        await session.execute(
            select(Decision).where(Decision.correlation_id == cid).order_by(Decision.created_at.desc()).limit(1)
        )
    ).scalars().first()
    trd = (
        await session.execute(
            select(Trade).where(Trade.correlation_id == cid).order_by(Trade.created_at.desc()).limit(1)
        )
    ).scalars().first()
    return assemble_trace(cid, sig, dec, trd)


@router.get("/data/stats")
async def data_stats(session: AsyncSession = Depends(get_session_dep)) -> dict:
    since = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    stmt = (
        select(RawContent)
        .where(RawContent.fetched_at >= since)
        .order_by(RawContent.fetched_at.desc())
        .limit(5000)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return compute_content_stats(rows)


# ── portfolio / risk (derived from the persisted trades ledger + prices) ──────
# The trades table is the durable source; positions are re-priced against the
# latest persisted price. Absolute sizing uses a configured base capital (the
# risk-approved events only carry a size *fraction*). Documented assumption.
BASE_CAPITAL = float(os.getenv("CMI_BASE_CAPITAL_USD", "100000"))
OPEN_STATUSES = ("submitted", "filled")
DAILY_LOSS_LIMIT = 2000.0
MAX_EXPOSURE_PCT = 80.0
MAX_ASSET_PCT = 30.0


def map_position(trade: Any, price: Any | None, base_capital: float = BASE_CAPITAL) -> dict:
    entry = _num(trade.entry_price)
    qty = round((base_capital * _num(trade.position_size_pct)) / entry, 6) if entry else 0.0
    cur = _num(getattr(price, "price_usd", None)) or entry
    value = round(qty * cur, 2)
    cost = qty * entry
    pnl = round((value - cost) if trade.direction == "long" else (cost - value), 2)
    return {
        "position_id": trade.event_id,
        "symbol": trade.symbol,
        "direction": trade.direction,
        "quantity": qty,
        "entry_price": entry,
        "current_price": round(cur, 6),
        "value_usd": value,
        "unrealized_pnl_usd": pnl,
        "unrealized_pnl_pct": round((pnl / cost) * 100, 2) if cost else 0.0,
        "stop_loss": _num(trade.stop_loss) or None,
        "take_profit": _num(trade.take_profit) or None,
        "protected": bool(trade.stop_loss and trade.take_profit),
        "opened_at": _iso(trade.created_at),
        "mode": "live",
    }


def map_portfolio_trade(trade: Any, base_capital: float = BASE_CAPITAL) -> dict:
    entry = _num(trade.entry_price)
    price = _num(trade.fill_price) or entry
    qty = round((base_capital * _num(trade.position_size_pct)) / entry, 6) if entry else 0.0
    status = "filled" if str(trade.status).lower() in {"filled", "closed"} else str(trade.status)
    return {
        "trade_id": trade.event_id,
        "symbol": trade.symbol,
        "side": "buy" if trade.direction == "long" else "sell",
        "order_type": "market",
        "price": round(price, 6),
        "quantity": qty,
        "cost_usd": round(price * qty, 2),
        "fee_usd": round(price * qty * 0.0016, 4),
        "pnl_usd": _num(trade.pnl) if trade.pnl is not None else None,
        "status": status,
        "mode": "live",
        "executed_at": _iso(trade.created_at),
    }


def compute_portfolio(positions: list[dict], realized_24h: float, base_capital: float = BASE_CAPITAL, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(tz=timezone.utc)
    invested = round(sum(p["value_usd"] for p in positions), 2)
    cost_basis = sum(p["quantity"] * p["entry_price"] for p in positions)
    unrealized = round(sum(p["unrealized_pnl_usd"] for p in positions), 2)
    cash = round(base_capital - cost_basis, 2)
    total = round(cash + invested, 2)
    return {
        "total_value_usd": total,
        "cash_usd": cash,
        "kraken_balance_usd": round(cash * 0.8, 2),
        "invested_usd": invested,
        "unrealized_pnl_usd": unrealized,
        "unrealized_pnl_pct": round(unrealized / total * 100, 2) if total else 0.0,
        "realized_pnl_24h_usd": round(realized_24h, 2),
        "pnl_24h_pct": round(realized_24h / base_capital * 100, 2) if base_capital else 0.0,
        "updated_at": now.isoformat(),
    }


def compute_exposure(positions: list[dict], total: float, daily_loss: float = 0.0, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(tz=timezone.utc)
    by_asset = [
        {
            "symbol": p["symbol"],
            "exposure_usd": p["value_usd"],
            "exposure_pct": round(p["value_usd"] / total * 100, 2) if total else 0.0,
            "limit_pct": MAX_ASSET_PCT,
            "protected": p["protected"],
        }
        for p in positions
    ]
    texp = round(sum(a["exposure_usd"] for a in by_asset), 2)
    return {
        "total_exposure_usd": texp,
        "total_exposure_pct": round(texp / total * 100, 2) if total else 0.0,
        "max_exposure_pct": MAX_EXPOSURE_PCT,
        "by_asset": by_asset,
        "protected_positions": sum(1 for p in positions if p["protected"]),
        "open_positions": len(positions),
        "daily_loss_usd": round(daily_loss, 2),
        "daily_loss_limit_usd": DAILY_LOSS_LIMIT,
        "updated_at": now.isoformat(),
    }


def compute_risk_limits(exposure: dict, cash_pct: float) -> list[dict]:
    max_asset = max((a["exposure_pct"] for a in exposure["by_asset"]), default=0.0)
    return [
        {"key": "max_portfolio_exposure", "label": "Exposition maximale portefeuille",
         "value": exposure["total_exposure_pct"], "max": MAX_EXPOSURE_PCT, "unit": "%",
         "breached": exposure["total_exposure_pct"] > MAX_EXPOSURE_PCT},
        {"key": "max_single_asset", "label": "Exposition maximale par actif",
         "value": round(max_asset, 1), "max": MAX_ASSET_PCT, "unit": "%", "breached": max_asset > MAX_ASSET_PCT},
        {"key": "daily_loss_limit", "label": "Perte journalière maximale",
         "value": exposure["daily_loss_usd"], "max": DAILY_LOSS_LIMIT, "unit": "USD",
         "breached": exposure["daily_loss_usd"] > DAILY_LOSS_LIMIT},
        {"key": "max_open_positions", "label": "Positions ouvertes maximum",
         "value": exposure["open_positions"], "max": 10, "unit": "positions",
         "breached": exposure["open_positions"] > 10},
        {"key": "min_cash_reserve", "label": "Réserve de liquidité minimum",
         "value": round(cash_pct, 1), "max": 20, "unit": "%", "breached": cash_pct < 20},
    ]


def compute_risk_alerts(exposure: dict, *, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(tz=timezone.utc)
    alerts: list[dict] = []
    for a in exposure["by_asset"]:
        if a["exposure_pct"] > MAX_ASSET_PCT:
            alerts.append({"id": f"exp-{a['symbol']}", "level": "warning", "symbol": a["symbol"],
                           "message": f"Exposition {a['symbol']} à {a['exposure_pct']}% (> {MAX_ASSET_PCT}%)",
                           "created_at": now.isoformat()})
        if not a["protected"]:
            alerts.append({"id": f"unp-{a['symbol']}", "level": "info", "symbol": a["symbol"],
                           "message": f"Position {a['symbol']} sans SL/TP complet", "created_at": now.isoformat()})
    if exposure["daily_loss_usd"] > DAILY_LOSS_LIMIT:
        alerts.append({"id": "daily-loss", "level": "critical",
                       "message": f"Perte journalière {exposure['daily_loss_usd']}$ dépasse la limite",
                       "created_at": now.isoformat()})
    return alerts


async def _open_positions(session: AsyncSession) -> list[dict]:
    trades = (await session.execute(select(Trade).where(Trade.status.in_(OPEN_STATUSES)))).scalars().all()
    prices = (await session.execute(_latest_per_symbol(Price, Price.price_usd, Price.time))).scalars().all()
    pmap = {p.symbol: p for p in prices}
    return [map_position(t, pmap.get(t.symbol)) for t in trades]


async def _realized_24h(session: AsyncSession) -> tuple[float, float]:
    since = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    closed = (
        await session.execute(select(Trade).where(and_(Trade.status == "closed", Trade.created_at >= since)))
    ).scalars().all()
    realized = sum(_num(t.pnl) for t in closed)
    daily_loss = -sum(_num(t.pnl) for t in closed if (t.pnl or 0) < 0)
    return realized, daily_loss


@router.get("/portfolio")
async def portfolio(session: AsyncSession = Depends(get_session_dep)) -> dict:
    positions = await _open_positions(session)
    realized, _ = await _realized_24h(session)
    return compute_portfolio(positions, realized)


@router.get("/portfolio/positions")
async def portfolio_positions(session: AsyncSession = Depends(get_session_dep)) -> list[dict]:
    return await _open_positions(session)


@router.get("/portfolio/trades")
async def portfolio_trades(
    limit: int = Query(50, ge=1, le=500), session: AsyncSession = Depends(get_session_dep)
) -> list[dict]:
    rows = (await session.execute(select(Trade).order_by(Trade.created_at.desc()).limit(limit))).scalars().all()
    return [map_portfolio_trade(t) for t in rows]


@router.get("/portfolio/history")
async def portfolio_history(
    range: str = Query("30d"), session: AsyncSession = Depends(get_session_dep)
) -> list[dict]:
    since = datetime.now(tz=timezone.utc) - _RANGE_TO_DELTA.get(range, timedelta(days=30))
    closed = (
        await session.execute(
            select(Trade)
            .where(and_(Trade.status == "closed", Trade.created_at >= since))
            .order_by(Trade.created_at.asc())
        )
    ).scalars().all()
    # Reconstruct equity curve: base capital + cumulative realized PnL at each close.
    equity = BASE_CAPITAL
    points = [{"t": since.isoformat(), "price": round(equity, 2)}]
    for t in closed:
        equity += _num(t.pnl)
        points.append({"t": _iso(t.created_at), "price": round(equity, 2)})
    return points


@router.get("/risk/exposure")
async def risk_exposure(session: AsyncSession = Depends(get_session_dep)) -> dict:
    positions = await _open_positions(session)
    realized, daily_loss = await _realized_24h(session)
    total = compute_portfolio(positions, realized)["total_value_usd"]
    return compute_exposure(positions, total, daily_loss)


@router.get("/risk/limits")
async def risk_limits(session: AsyncSession = Depends(get_session_dep)) -> list[dict]:
    positions = await _open_positions(session)
    realized, daily_loss = await _realized_24h(session)
    pf = compute_portfolio(positions, realized)
    exposure = compute_exposure(positions, pf["total_value_usd"], daily_loss)
    cash_pct = (pf["cash_usd"] / pf["total_value_usd"] * 100) if pf["total_value_usd"] else 0.0
    return compute_risk_limits(exposure, cash_pct)


@router.get("/risk/alerts")
async def risk_alerts(
    limit: int = Query(30, ge=1, le=100), session: AsyncSession = Depends(get_session_dep)
) -> list[dict]:
    positions = await _open_positions(session)
    realized, daily_loss = await _realized_24h(session)
    total = compute_portfolio(positions, realized)["total_value_usd"]
    exposure = compute_exposure(positions, total, daily_loss)
    return compute_risk_alerts(exposure)[:limit]
