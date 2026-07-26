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
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cmi_common.db import (
    AccountSnapshot,
    Decision,
    News,
    Price,
    Sentiment,
    ServiceHealth,
    Signal,
    Token,
    Trade,
)
from cmi_common.db.models import PipelineRejection, RawContent
from cmi_common.sources import SqlSentimentAggReader

from .funnel import SCORE_BUCKET_WIDTH, build_funnel
from .routers import get_session_dep

router = APIRouter(tags=["read"])


def get_reader_dep(
    session: AsyncSession = Depends(get_session_dep),
) -> SqlSentimentAggReader:
    return SqlSentimentAggReader(session)


@router.get("/api/v1/sentiment/windows")
async def sentiment_windows(
    symbol: str | None = Query(None),
    kind: str = Query("all"),
    decay: float | None = Query(None, gt=0, description="decay half-life in hours"),
    reader: SqlSentimentAggReader = Depends(get_reader_dep),
) -> list[dict]:
    sym = symbol.upper() if symbol else None
    return await reader.all_windows(symbol=sym, kind=kind, half_life_h=decay)


@router.get("/api/v1/sentiment/series")
async def sentiment_series(
    symbol: str | None = Query(None),
    kind: Annotated[str | None, Query()] = None,
    points: int = Query(12, ge=1, le=168),
    reader: SqlSentimentAggReader = Depends(get_reader_dep),
) -> list[dict]:
    sym = symbol.upper() if symbol else None
    return await reader.series(symbol=sym, kind=kind, points=points)


@router.get("/api/v1/sentiment/authors")
async def sentiment_authors(
    symbol: str = Query(...),
    window: str = Query("7d"),
    reader: SqlSentimentAggReader = Depends(get_reader_dep),
) -> dict:
    sym = symbol.upper()
    count = await reader.distinct_authors(symbol=sym, window=window)
    return {"symbol": sym, "window": window, "unique_authors": count}

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


def _utcnow() -> datetime:
    """Aware UTC — for tz-aware columns (raw_content, service_health)."""
    return datetime.now(tz=UTC)


def _utcnow_naive() -> datetime:
    """Naive UTC — for the time-series columns (prices, signals, sentiments,
    pipeline_rejections, decisions.created_at, trades.created_at).

    Those columns are ``timestamptz``, not TIMESTAMP WITHOUT TIME ZONE as an
    earlier version of this docstring claimed (the wrong description cost a
    reviewer a false alarm). The naive comparison is still correct: PostgreSQL
    reads a naive literal in the session timezone, which is UTC here, so it
    resolves to the same instant an aware UTC value would.
    """
    return datetime.now(tz=UTC).replace(tzinfo=None)


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
        "published_at": datetime.fromtimestamp(row.published_at, tz=UTC).isoformat()
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
    now = now or datetime.now(tz=UTC)
    rows = list(rows)
    by_cat: Counter[str] = Counter()
    src: Counter[str] = Counter()
    mentions: Counter[str] = Counter()
    vol: dict[int, dict[str, int]] = defaultdict(lambda: {"social": 0, "news": 0, "market": 0})

    for r in rows:
        cat = _KIND_TO_CATEGORY.get(r.kind, "market")
        by_cat[cat] += 1
        src[r.source] += 1
        for sym in (r.symbols or []):
            mentions[sym] += 1
        ts = r.published_at or r.fetched_at
        if isinstance(ts, datetime):
            age_h = int((now - ts).total_seconds() // 3600)
            if 0 <= age_h < 12:
                bucket = 11 - age_h
                vol[bucket][cat] += 1

    def label(bucket: int) -> str:
        hour = (now - timedelta(hours=11 - bucket)).hour
        return f"{hour:02d}h"

    volume_series = [
        {"hour": label(b), **{k: vol[b][k] for k in ("social", "news", "market")}}
        for b in range(12)
    ]
    return {
        "total_24h": len(rows),
        "social_24h": by_cat["social"],
        "news_24h": by_cat["news"],
        "market_24h": by_cat["market"],
        "avg_sentiment": 0.0,
        "volume_series": volume_series,
        "sentiment_series": [],
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
    since = _utcnow_naive() - _RANGE_TO_DELTA.get(range, timedelta(days=1))
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
    since = datetime.now(tz=UTC) - timedelta(hours=24)
    stmt = (
        select(RawContent)
        .where(RawContent.fetched_at >= since)
        .order_by(RawContent.fetched_at.desc())
        .limit(5000)
    )
    rows = (await session.execute(stmt)).scalars().all()
    stats = compute_content_stats(rows)
    reader = SqlSentimentAggReader(session)
    # Reader returns ISO hours; DataStats/mock use an "HHh" label on the X axis.
    series = await reader.series(symbol=None, kind=None, points=12)
    stats["sentiment_series"] = [
        {
            "hour": datetime.fromisoformat(p["hour"]).strftime("%Hh"),
            "sentiment": p["sentiment"],
            "mentions": p["mentions"],
        }
        for p in series
    ]
    day = await reader.window_stats(symbol=None, kind=None, window="24h")
    stats["avg_sentiment"] = round(day["avg"], 2)
    return stats


# ── portfolio / risk (derived from the persisted trades ledger + prices) ──────
# The trades table is the durable source; positions are re-priced against the
# latest persisted price. Absolute sizing uses a configured base capital (the
# risk-approved events only carry a size *fraction*). Documented assumption.
BASE_CAPITAL = float(os.getenv("CMI_BASE_CAPITAL_USD", "100000"))
OPEN_STATUSES = ("submitted", "filled")
# ExecutionKind values meaning an order actually reached the exchange. The other
# three — `pending` (queued), `failed`, `rejected` — are not executions, so the
# funnel's "executed" stage must enumerate rather than exclude "approved".
EXECUTED_STATUSES = ("submitted", "filled", "closed")
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


# Past this age a snapshot is served greyed rather than as a current balance.
STALE_AFTER_S = 300


def reference_capital(snapshot: dict | None) -> float:
    """The exchange's real equity when we have one, the configured constant
    otherwise.

    Without this, a real Kraken balance would be displayed beside positions
    sized against an imaginary 100k. A *stale* snapshot still governs: a balance
    from ten minutes ago approximates the real capital far better than a
    configuration constant does. An equity of exactly zero is an answer, not an
    absence, so it must not fall back either.
    """
    if snapshot is None or snapshot.get("equity_usd") is None:
        return BASE_CAPITAL
    return float(snapshot["equity_usd"])


def _balance_fields(snapshot: dict | None, now: datetime) -> dict:
    """What the balance is, and what it is worth trusting.

    `cash * 0.8` used to stand here. It was not a balance, and the operator had
    no way to tell it from one. The rule now: a real number or `null`, plus
    enough context that the UI never has to guess which it got.
    """
    if snapshot is None or snapshot.get("equity_usd") is None:
        return {
            "kraken_balance_usd": None,
            "balance_source": "unavailable",
            "balance_fetched_at": None,
            "balance_stale": False,
        }
    fetched = snapshot.get("fetched_at")
    # The persister writes naive UTC; comparing that to an aware `now` raises,
    # and swallowing the error would present a stale balance as a current one.
    if isinstance(fetched, datetime) and fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    age = (now - fetched).total_seconds() if isinstance(fetched, datetime) else None
    return {
        "kraken_balance_usd": round(float(snapshot["equity_usd"]), 2),
        "balance_source": snapshot.get("venue") or "unavailable",
        "balance_fetched_at": _iso(fetched),
        "balance_stale": age is not None and age > STALE_AFTER_S,
    }


def compute_portfolio(
    positions: list[dict],
    realized_24h: float,
    base_capital: float | None = None,
    *,
    snapshot: dict | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(tz=UTC)
    if base_capital is None:
        base_capital = reference_capital(snapshot)
    invested = round(sum(p["value_usd"] for p in positions), 2)
    cost_basis = sum(p["quantity"] * p["entry_price"] for p in positions)
    unrealized = round(sum(p["unrealized_pnl_usd"] for p in positions), 2)
    cash = round(base_capital - cost_basis, 2)
    total = round(cash + invested, 2)
    return {
        "total_value_usd": total,
        "cash_usd": cash,
        **_balance_fields(snapshot, now),
        "invested_usd": invested,
        "unrealized_pnl_usd": unrealized,
        "unrealized_pnl_pct": round(unrealized / total * 100, 2) if total else 0.0,
        "realized_pnl_24h_usd": round(realized_24h, 2),
        "pnl_24h_pct": round(realized_24h / base_capital * 100, 2) if base_capital else 0.0,
        "updated_at": now.isoformat(),
    }


def compute_exposure(positions: list[dict], total: float, daily_loss: float = 0.0, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(tz=UTC)
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
    now = now or datetime.now(tz=UTC)
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


async def _account_snapshot(session: AsyncSession) -> dict | None:
    """The most recent balance reading, or None when no venue has reported one.

    None is the honest answer, not a failure: with no exchange key configured
    the poller does not run at all, which is the production state today.
    """
    row = (
        await session.execute(
            select(AccountSnapshot).order_by(AccountSnapshot.fetched_at.desc()).limit(1)
        )
    ).scalars().first()
    if row is None:
        return None
    return {
        "venue": row.venue,
        "equity_usd": _num(row.equity_usd),
        "cash_usd": _num(row.cash_usd),
        "fetched_at": row.fetched_at,
    }


async def _portfolio_basis(
    session: AsyncSession,
) -> tuple[list[dict], dict | None, float]:
    """Positions, snapshot and reference capital — always resolved together.

    They have to be: `map_position` turns a size *fraction* into a quantity
    using the reference capital, so sizing positions against the configured
    constant while heading the page with a real exchange balance would make the
    total disagree with its own header.
    """
    snapshot = await _account_snapshot(session)
    capital = reference_capital(snapshot)
    return await _open_positions(session, capital), snapshot, capital


async def _open_positions(
    session: AsyncSession, base_capital: float | None = None
) -> list[dict]:
    trades = (await session.execute(select(Trade).where(Trade.status.in_(OPEN_STATUSES)))).scalars().all()
    prices = (await session.execute(_latest_per_symbol(Price, Price.price_usd, Price.time))).scalars().all()
    pmap = {p.symbol: p for p in prices}
    capital = BASE_CAPITAL if base_capital is None else base_capital
    return [map_position(t, pmap.get(t.symbol), capital) for t in trades]


async def _realized_24h(session: AsyncSession) -> tuple[float, float]:
    since = _utcnow_naive() - timedelta(hours=24)
    closed = (
        await session.execute(select(Trade).where(and_(Trade.status == "closed", Trade.created_at >= since)))
    ).scalars().all()
    realized = sum(_num(t.pnl) for t in closed)
    daily_loss = -sum(_num(t.pnl) for t in closed if (t.pnl or 0) < 0)
    return realized, daily_loss


@router.get("/portfolio")
async def portfolio(session: AsyncSession = Depends(get_session_dep)) -> dict:
    positions, snapshot, capital = await _portfolio_basis(session)
    realized, _ = await _realized_24h(session)
    return compute_portfolio(positions, realized, capital, snapshot=snapshot)


@router.get("/portfolio/positions")
async def portfolio_positions(
    session: AsyncSession = Depends(get_session_dep),
) -> list[dict]:
    # Same reference capital as /portfolio: sizing the rows against the config
    # constant while the header shows a real exchange balance would make the
    # page disagree with itself.
    positions, _snapshot, _capital = await _portfolio_basis(session)
    return positions


@router.get("/portfolio/trades")
async def portfolio_trades(
    limit: int = Query(50, ge=1, le=500), session: AsyncSession = Depends(get_session_dep)
) -> list[dict]:
    capital = reference_capital(await _account_snapshot(session))
    rows = (
        await session.execute(
            select(Trade).order_by(Trade.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    return [map_portfolio_trade(t, capital) for t in rows]


@router.get("/portfolio/history")
async def portfolio_history(
    range: str = Query("30d"), session: AsyncSession = Depends(get_session_dep)
) -> list[dict]:
    since = _utcnow_naive() - _RANGE_TO_DELTA.get(range, timedelta(days=30))
    closed = (
        await session.execute(
            select(Trade)
            .where(and_(Trade.status == "closed", Trade.created_at >= since))
            .order_by(Trade.created_at.asc())
        )
    ).scalars().all()
    # Reconstruct equity curve: reference capital + cumulative realized PnL at
    # each close. Same basis as every other portfolio figure -- a curve starting
    # at the config constant while the header shows a real exchange balance
    # would put the two on different scales in the same page.
    equity = reference_capital(await _account_snapshot(session))
    points = [{"t": since.isoformat(), "price": round(equity, 2)}]
    for t in closed:
        equity += _num(t.pnl)
        points.append({"t": _iso(t.created_at), "price": round(equity, 2)})
    return points


@router.get("/risk/exposure")
async def risk_exposure(session: AsyncSession = Depends(get_session_dep)) -> dict:
    positions, snapshot, capital = await _portfolio_basis(session)
    realized, daily_loss = await _realized_24h(session)
    total = compute_portfolio(positions, realized, capital, snapshot=snapshot)["total_value_usd"]
    return compute_exposure(positions, total, daily_loss)


@router.get("/risk/limits")
async def risk_limits(session: AsyncSession = Depends(get_session_dep)) -> list[dict]:
    positions, snapshot, capital = await _portfolio_basis(session)
    realized, daily_loss = await _realized_24h(session)
    pf = compute_portfolio(positions, realized, capital, snapshot=snapshot)
    exposure = compute_exposure(positions, pf["total_value_usd"], daily_loss)
    cash_pct = (pf["cash_usd"] / pf["total_value_usd"] * 100) if pf["total_value_usd"] else 0.0
    return compute_risk_limits(exposure, cash_pct)


@router.get("/risk/alerts")
async def risk_alerts(
    limit: int = Query(30, ge=1, le=100), session: AsyncSession = Depends(get_session_dep)
) -> list[dict]:
    positions, snapshot, capital = await _portfolio_basis(session)
    realized, daily_loss = await _realized_24h(session)
    total = compute_portfolio(positions, realized, capital, snapshot=snapshot)["total_value_usd"]
    exposure = compute_exposure(positions, total, daily_loss)
    return compute_risk_alerts(exposure)[:limit]


# ── systems / infrastructure health (from the persisted service_health table) ──
# (service, name, group, role) — the map the terminal draws. Health rows written
# by the collector supply live status/latency; kafka/collector/worker/infra
# detail arrays need a Prometheus scrape (documented follow-up) and are empty.
SERVICE_CATALOG: list[tuple[str, str, str, str]] = [
    ("traefik", "Traefik", "edge", "Reverse proxy / TLS"),
    ("api-gateway", "api-gateway", "edge", "REST lecture seule"),
    ("websocket-gateway", "websocket-gateway", "edge", "Diffusion temps réel"),
    ("control-api", "control-api", "execute", "Plan de contrôle"),
    ("collector-coingecko", "collector-coingecko", "collect", "Prix & volumes"),
    ("collector-dexscreener", "collector-dexscreener", "collect", "Liquidité DEX"),
    ("collector-social", "collector-social", "collect", "Social multi-plateformes"),
    ("collector-news", "collector-news", "collect", "News multi-sources"),
    ("sentiment-service", "sentiment-service", "analyze", "Scoring L1"),
    ("ai-worker-haiku", "ai-worker-haiku", "analyze", "Triage — Haiku"),
    ("ai-worker-sonnet", "ai-worker-sonnet", "analyze", "Analyste — Sonnet"),
    ("decision-engine", "decision-engine", "decide", "Fusion signaux"),
    ("risk-engine", "risk-engine", "decide", "Garde-fous"),
    ("trading-engine", "trading-engine", "execute", "Exécution — Kraken"),
]
_PIPELINE = [
    ("collect", "Collecte", "Marché · Social · News", "collector-coingecko"),
    ("sentiment", "Sentiment", "Scoring L1", "sentiment-service"),
    ("triage", "Triage", "Haiku", "ai-worker-haiku"),
    ("senior", "Analyse", "Sonnet", "ai-worker-sonnet"),
    ("decision", "Décision", "Fusion signaux", "decision-engine"),
    ("risk", "Risque", "Garde-fous", "risk-engine"),
    ("execute", "Exécution", "Kraken Futures", "trading-engine"),
]


def assemble_systems_snapshot(rows: Iterable[Any], *, now: datetime | None = None) -> dict:
    """Build the SystemsSnapshot from persisted health rows. Pure."""
    now = now or datetime.now(tz=UTC)
    by_svc = {r.service: r for r in rows}

    services = []
    for sid, name, group, role in SERVICE_CATALOG:
        r = by_svc.get(sid)
        status = (r.status if r else None) or ("healthy" if (r and r.healthy) else "idle")
        detail = getattr(r, "detail", None) or {}
        services.append(
            {
                "id": sid, "name": name, "group": group, "role": role,
                "status": status,
                "version": str(detail.get("version", "—")),
                "replicas": int(detail.get("replicas", 1)),
                "uptime_pct": float(detail.get("uptime_pct", 99.9 if status == "healthy" else 0.0)),
                "latency_ms": round(_num(getattr(r, "latency_ms", 0.0)), 0) if r else 0,
                "cpu_pct": int(detail.get("cpu_pct", 0)),
                "mem_mb": int(detail.get("mem_mb", 0)),
                "throughput_per_min": int(detail.get("throughput_per_min", 0)),
                "kafka_in": list(detail.get("kafka_in", [])),
                "kafka_out": list(detail.get("kafka_out", [])),
                "host": detail.get("host"),
                "spark": list(detail.get("spark", [])),
            }
        )

    smap = {s["id"]: s for s in services}
    pipeline = [
        {
            "id": pid, "label": label, "sublabel": sub,
            "status": smap.get(svc, {}).get("status", "idle"),
            "throughput_per_min": smap.get(svc, {}).get("throughput_per_min", 0),
        }
        for pid, label, sub, svc in _PIPELINE
    ]

    healthy = sum(1 for s in services if s["status"] == "healthy")
    degraded = sum(1 for s in services if s["status"] == "degraded")
    down = sum(1 for s in services if s["status"] in {"down", "idle"})
    summary = {
        "services_total": len(services),
        "services_healthy": healthy,
        "services_degraded": degraded,
        "services_down": down,
        "events_per_min": int(sum(s["throughput_per_min"] for s in services)),
        "kafka_lag_total": 0,
        "ai_cost_today_usd": 0.0,
        "global_uptime_pct": round(sum(s["uptime_pct"] for s in services) / len(services), 2) if services else 0.0,
        "data_points_today": 0,
        "updated_at": now.isoformat(),
    }
    return {
        "summary": summary,
        "services": services,
        "pipeline": pipeline,
        "kafka": [],       # needs a Prometheus scrape of the broker (follow-up)
        "collectors": [],  # needs per-collector metrics (follow-up)
        "workers": [],     # needs AI worker metrics (follow-up)
        "infra": [],       # needs Postgres/Redis/Kafka/Traefik metrics (follow-up)
    }


def build_collectors(rows: Iterable[tuple[str, str, int]]) -> list[dict]:
    """rows: (source, kind, count_last_hour) from raw_content."""
    out = []
    for source, kind, count in rows:
        out.append(
            {
                "platform": source,
                "category": _KIND_TO_CATEGORY.get(kind, "market"),
                "status": "healthy" if count else "idle",
                "poll_interval_s": 0,
                "items_last_hour": int(count),
                "rate_limit_pct": 0,
                "key_gated": False,
                "enabled": True,
                "last_item_ago_s": 0,
            }
        )
    return sorted(out, key=lambda c: c["items_last_hour"], reverse=True)


def build_workers(haiku_reqs: int, sonnet_reqs: int) -> list[dict]:
    def worker(name, model, tier, reqs, tin, tout, cost_per):
        return {
            "name": name, "model": model, "tier": tier,
            "status": "healthy" if reqs else "idle",
            "requests_last_hour": int(reqs), "tokens_in": int(reqs * tin), "tokens_out": int(reqs * tout),
            "cost_usd_today": round(reqs * cost_per, 2), "avg_latency_ms": 0,
            "escalation_rate": 0.0, "queue_depth": 0,
        }
    return [
        worker("ai-worker-haiku", "claude-haiku-4-5", "triage", haiku_reqs, 820, 190, 0.0009),
        worker("ai-worker-sonnet", "claude-sonnet-4-6", "senior", sonnet_reqs, 2400, 640, 0.012),
    ]


# topic name → (persisted table label, partitions)
_TOPIC_COUNTS = [
    ("price.events", 6), ("sentiment.events", 4), ("analysis.events", 6),
    ("decision.events", 4), ("risk.approved.events", 3),
]


def build_kafka(counts: dict[str, int]) -> list[dict]:
    out = []
    for name, partitions in _TOPIC_COUNTS:
        c = int(counts.get(name, 0))
        out.append(
            {
                "name": name, "partitions": partitions, "msg_per_min": round(c / 60, 1),
                "lag": 0, "consumers": 1, "retention_h": 24,
                "bytes_per_min": round(c / 60 * 512), "orphaned": c == 0,
            }
        )
    return out


def build_infra(pg_connections: int, pg_size_bytes: int) -> list[dict]:
    gb = pg_size_bytes / 1e9 if pg_size_bytes else 0.0
    return [
        {
            "id": "postgres", "name": "PostgreSQL + TimescaleDB", "kind": "database", "status": "healthy",
            "metrics": [
                {"label": "Connexions", "value": str(pg_connections)},
                {"label": "Taille", "value": f"{gb:.2f} GB"},
            ],
        }
    ]


@router.get("/systems/overview")
async def systems_overview(session: AsyncSession = Depends(get_session_dep)) -> dict:
    from sqlalchemy import text as _text

    rows = (await session.execute(select(ServiceHealth))).scalars().all()
    snap = assemble_systems_snapshot(rows)

    hour = _utcnow_naive() - timedelta(hours=1)  # naive time-series columns
    hour_aware = _utcnow() - timedelta(hours=1)  # raw_content.fetched_at is tz-aware

    async def count(model, time_col) -> int:
        return int((await session.execute(select(func.count()).select_from(model).where(time_col >= hour))).scalar_one())

    coll_rows = (
        await session.execute(
            select(RawContent.source, RawContent.kind, func.count())
            .where(RawContent.fetched_at >= hour_aware)
            .group_by(RawContent.source, RawContent.kind)
        )
    ).all()
    snap["collectors"] = build_collectors([(r[0], r[1], r[2]) for r in coll_rows])
    snap["workers"] = build_workers(await count(Signal, Signal.time), await count(Decision, Decision.created_at))
    snap["kafka"] = build_kafka(
        {
            "price.events": await count(Price, Price.time),
            "sentiment.events": await count(Sentiment, Sentiment.time),
            "analysis.events": await count(Signal, Signal.time),
            "decision.events": await count(Decision, Decision.created_at),
            "risk.approved.events": await count(Trade, Trade.created_at),
        }
    )
    try:
        conns = int((await session.execute(_text("SELECT count(*) FROM pg_stat_activity"))).scalar_one())
        size = int((await session.execute(_text("SELECT pg_database_size(current_database())"))).scalar_one())
        snap["infra"] = build_infra(conns, size)
    except Exception:
        snap["infra"] = build_infra(0, 0)

    snap["summary"]["kafka_lag_total"] = sum(t["lag"] for t in snap["kafka"])
    snap["summary"]["ai_cost_today_usd"] = round(sum(w["cost_usd_today"] for w in snap["workers"]), 2)
    if not snap["summary"]["events_per_min"]:
        snap["summary"]["events_per_min"] = int(sum(t["msg_per_min"] for t in snap["kafka"]))
    return snap


@router.get("/systems/funnel")
async def systems_funnel(
    window: str = Query("24h", pattern="^(1h|24h|7d)$"),
    session: AsyncSession = Depends(get_session_dep),
) -> dict:
    """Stage-by-stage survival of signals, plus why the rest were dropped."""
    hours = {"1h": 1, "24h": 24, "7d": 168}[window]
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
    # A trade is born "approved" and is stamped with the ExecutionEvent kind once
    # the engine acts on it. Enumerate the kinds that mean an order actually
    # reached the exchange rather than testing "moved past approved": of the six
    # ExecutionKind values, `failed` and `rejected` are terminal non-executions
    # and `pending` is still queued, so a negative test would report three kinds
    # of non-execution as executions — in a funnel whose only job is to say where
    # signals stop, that inverts the answer.
    executed = await session.scalar(
        select(func.count())
        .select_from(Trade)
        .where(
            and_(Trade.created_at >= since, Trade.status.in_(EXECUTED_STATUSES))
        )
    )

    # Floor-divide, not `/`: SQLAlchemy renders `/` on two Integers as true
    # division (it casts the divisor to NUMERIC), which would put every distinct
    # score in its own group instead of a bucket. `//` compiles to PostgreSQL
    # integer division and keeps the keys integral, as build_funnel expects.
    bucket = (Signal.opportunity_score // SCORE_BUCKET_WIDTH) * SCORE_BUCKET_WIDTH
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
