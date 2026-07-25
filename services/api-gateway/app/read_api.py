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

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cmi_common.db import Decision, News, Price, Sentiment, Signal, Token
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
