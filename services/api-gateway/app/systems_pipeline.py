"""Windowed, per-stage view of the pipeline behind the Command Center graph.

The graph used to report `0/m` on every connector: the health collector scraped
the wrong metric names, so `throughput_per_min` was never written and the read
API fell back to a literal zero. An unknown that renders as zero is worse than
no value at all — it says "the pipeline is dead" when it means "I did not
measure". Nothing here converts an unknown into a number.

Volumes come from Postgres rather than Prometheus: they survive a restart, and
they reuse the funnel's stage definitions so the two panels never disagree.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select

from cmi_common.db.models import (
    Decision,
    DecisionJournal,
    PipelineRejection,
    Price,
    RawContent,
    Signal,
    Trade,
)

from .funnel import collapse_reason


@dataclass(frozen=True)
class StageSpec:
    id: str
    label: str
    sublabel: str
    services: tuple[str, ...]


STAGE_SPECS: tuple[StageSpec, ...] = (
    StageSpec(
        "collect", "Collecte", "Marché · Social · News",
        ("collector-coingecko", "collector-dexscreener",
         "collector-social", "collector-news"),
    ),
    StageSpec("sentiment", "Sentiment", "Scoring L1", ("sentiment-service",)),
    StageSpec("triage", "Triage", "Haiku", ("ai-worker-haiku",)),
    StageSpec("senior", "Analyse", "Sonnet", ("ai-worker-sonnet",)),
    StageSpec("decision", "Décision", "Fusion signaux", ("decision-engine",)),
    StageSpec("risk", "Risque", "Garde-fous", ("risk-engine",)),
    StageSpec("execute", "Exécution", "Kraken Futures", ("trading-engine",)),
)

STAGE_IDS: tuple[str, ...] = tuple(s.id for s in STAGE_SPECS)
STAGE_BY_ID: dict[str, StageSpec] = {s.id: s for s in STAGE_SPECS}

# Conversion is reported only between stages that count the same unit.
# `collect` counts price points *and* content rows, `sentiment` counts content
# rows, `triage` counts per-token signals — a ratio across those is a number
# with no meaning, and a meaningless percentage on a debugging panel is worse
# than a blank.
CONVERSION_FROM: dict[str, str] = {
    "senior": "triage",
    "decision": "senior",
    "risk": "decision",
    "execute": "risk",
}

# "idle" ranks above "healthy": a collector with no data is not fine, it is
# quiet, and the stage roll-up must not hide that behind a healthy sibling.
_STATUS_RANK = {"healthy": 0, "idle": 1, "degraded": 2, "down": 3}


@dataclass(frozen=True)
class StageCounts:
    """What crossed one stage over the window. Every field defaults to unknown."""

    volume: int | None = None
    dropped: int | None = None
    last_at: datetime | None = None
    last_summary: str | None = None


def _roll_up_status(statuses: Sequence[str]) -> str:
    """Worst status wins, and the result is always one of the four known values.

    The frontend types this field as a closed union, and nothing validates the
    JSON at runtime — so passing an unrecognised string through verbatim would
    make that type quietly false. An unknown status already ranked as the worst
    case here; reporting it as "down" just makes that reading explicit rather
    than inventing a fifth state the UI has no label or colour for.
    """
    if not statuses:
        return "idle"
    worst = max(statuses, key=lambda s: _STATUS_RANK.get(s, len(_STATUS_RANK)))
    return worst if worst in _STATUS_RANK else "down"


def _roll_up_throughput(values: Sequence[int | None]) -> int | None:
    """Sum the measured collectors; None when none of them has been measured."""
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _conversion(volume: int | None, previous: int | None) -> float | None:
    if volume is None or not previous:
        return None
    return round(volume / previous * 100, 2)


def build_pipeline_stages(
    counts: Mapping[str, StageCounts],
    services: Mapping[str, Mapping],
) -> list[dict]:
    """Assemble the seven graph nodes. Pure — no database, no clock."""
    out: list[dict] = []
    for spec in STAGE_SPECS:
        c = counts.get(spec.id) or StageCounts()
        nodes = [services[s] for s in spec.services if s in services]
        previous_id = CONVERSION_FROM.get(spec.id)
        previous = counts.get(previous_id) if previous_id else None
        out.append(
            {
                "id": spec.id,
                "label": spec.label,
                "sublabel": spec.sublabel,
                "status": _roll_up_status([n.get("status", "idle") for n in nodes]),
                "throughput_per_min": _roll_up_throughput(
                    [n.get("throughput_per_min") for n in nodes]
                ),
                "volume": c.volume,
                "dropped": c.dropped,
                "conversion_pct": _conversion(
                    c.volume, previous.volume if previous else None
                ),
                "last_at": c.last_at.isoformat() if c.last_at else None,
                "last_summary": c.last_summary,
            }
        )
    return out


logger = logging.getLogger(__name__)

# /systems/overview is polled every 8s by every open terminal and these
# aggregates scan four tables. 30s of staleness on a volume counter is invisible
# to an operator; a fourfold query amplification is not.
CACHE_TTL_S = 30.0


class _StageCache:
    def __init__(self, ttl_s: float = CACHE_TTL_S) -> None:
        self._ttl = ttl_s
        self._entries: dict[str, tuple[float, dict[str, StageCounts]]] = {}

    def fresh(self, window: str, now: float) -> dict[str, StageCounts] | None:
        entry = self._entries.get(window)
        if entry and now - entry[0] < self._ttl:
            return entry[1]
        return None

    def last(self, window: str) -> dict[str, StageCounts] | None:
        entry = self._entries.get(window)
        return entry[1] if entry else None

    def put(self, window: str, now: float, value: dict[str, StageCounts]) -> None:
        self._entries[window] = (now, value)

    def clear(self) -> None:
        self._entries.clear()


STAGE_CACHE = _StageCache()

WINDOW_HOURS = {"1h": 1, "24h": 24, "7d": 168}
DEFAULT_WINDOW = "24h"

# A trade is born "approved" and stamped with its execution kind once the
# engine acts on it — mirrors read_api.systems_funnel's EXECUTED_STATUSES so
# the funnel panel and this graph never disagree on what "executed" means.
EXECUTED_STATUSES = ("submitted", "filled", "closed")
FAILED_STATUSES = ("failed", "rejected")


def _cutoffs(window: str) -> tuple[datetime, datetime]:
    """(naive, aware) UTC cutoffs. `raw_content` is the one table with tz-aware
    columns; mixing the two raises at query time, so both are computed once."""
    hours = WINDOW_HOURS[window]
    aware = datetime.now(tz=UTC) - timedelta(hours=hours)
    return aware.replace(tzinfo=None), aware


async def _count(session, model, *where) -> int:
    stmt = select(func.count()).select_from(model).where(and_(*where))
    return int((await session.execute(stmt)).scalar_one())


async def _latest(session, model, order_col, *where):
    stmt = select(model).order_by(order_col.desc()).limit(1)
    if where:
        stmt = stmt.where(and_(*where))
    return (await session.execute(stmt)).scalars().first()


async def fetch_stage_counts(session, window: str) -> dict[str, StageCounts]:
    """One StageCounts per stage over `window`. Raises on DB failure —
    `stage_counts_cached` decides what an operator should see."""
    since, since_aware = _cutoffs(window)

    prices = await _count(session, Price, Price.time >= since)
    content = await _count(session, RawContent, RawContent.fetched_at >= since_aware)
    last_content = await _latest(
        session, RawContent, RawContent.fetched_at, RawContent.fetched_at >= since_aware
    )

    scored = await _count(session, RawContent, RawContent.scored_at >= since_aware)
    # Unwindowed on purpose: this is the unscored queue depth, not a rate.
    backlog = await _count(session, RawContent, RawContent.scored_at.is_(None))
    last_scored = await _latest(
        session, RawContent, RawContent.scored_at, RawContent.scored_at.is_not(None)
    )

    analyses = await _count(session, Signal, Signal.time >= since)
    not_escalated = await _count(
        session, Signal, Signal.time >= since, Signal.escalated.is_(False)
    )
    last_signal = await _latest(session, Signal, Signal.time, Signal.time >= since)

    escalated = await _count(
        session, Signal, Signal.time >= since, Signal.escalated.is_(True)
    )
    # The measured production bottleneck: escalated but never handed to Sonnet
    # because the hourly budget was spent. Two separate numbers, on purpose.
    budget_skipped = await _count(
        session,
        DecisionJournal,
        DecisionJournal.time >= since,
        DecisionJournal.escalated.is_(True),
        DecisionJournal.sonnet_called.is_(False),
    )
    last_journal = await _latest(
        session,
        DecisionJournal,
        DecisionJournal.time,
        DecisionJournal.time >= since,
        DecisionJournal.sonnet_called.is_(True),
    )

    decisions = await _count(session, Decision, Decision.created_at >= since)
    decision_rejected = await _count(
        session,
        PipelineRejection,
        PipelineRejection.time >= since,
        PipelineRejection.stage == "decision_engine",
    )
    last_decision = await _latest(
        session, Decision, Decision.created_at, Decision.created_at >= since
    )

    approved = await _count(session, Trade, Trade.created_at >= since)
    risk_rejected = await _count(
        session,
        PipelineRejection,
        PipelineRejection.time >= since,
        PipelineRejection.stage == "risk_engine",
    )
    last_approved = await _latest(
        session, Trade, Trade.created_at, Trade.created_at >= since
    )

    executed = await _count(
        session, Trade, Trade.created_at >= since, Trade.status.in_(EXECUTED_STATUSES)
    )
    failed = await _count(
        session, Trade, Trade.created_at >= since, Trade.status.in_(FAILED_STATUSES)
    )
    last_executed = await _latest(
        session,
        Trade,
        Trade.created_at,
        Trade.created_at >= since,
        Trade.status.in_(EXECUTED_STATUSES),
    )

    return {
        "collect": StageCounts(
            volume=prices + content,
            last_at=getattr(last_content, "fetched_at", None),
            last_summary=summarize_content(last_content) if last_content else None,
        ),
        "sentiment": StageCounts(
            volume=scored,
            dropped=backlog,
            last_at=getattr(last_scored, "scored_at", None),
            last_summary=summarize_scored(last_scored) if last_scored else None,
        ),
        "triage": StageCounts(
            volume=analyses,
            dropped=not_escalated,
            last_at=getattr(last_signal, "time", None),
            last_summary=summarize_signal(last_signal) if last_signal else None,
        ),
        "senior": StageCounts(
            volume=escalated,
            dropped=budget_skipped,
            last_at=getattr(last_journal, "time", None),
            last_summary=summarize_journal(last_journal) if last_journal else None,
        ),
        "decision": StageCounts(
            volume=decisions,
            dropped=decision_rejected,
            last_at=getattr(last_decision, "created_at", None),
            last_summary=summarize_decision(last_decision) if last_decision else None,
        ),
        "risk": StageCounts(
            volume=approved,
            dropped=risk_rejected,
            last_at=getattr(last_approved, "created_at", None),
            last_summary=summarize_approved(last_approved) if last_approved else None,
        ),
        "execute": StageCounts(
            volume=executed,
            dropped=failed,
            last_at=getattr(last_executed, "created_at", None),
            last_summary=summarize_trade(last_executed) if last_executed else None,
        ),
    }


async def stage_counts_cached(
    session,
    window: str,
    *,
    now: float | None = None,
    fetch: Callable[..., Awaitable[dict[str, StageCounts]]] | None = None,
) -> tuple[dict[str, StageCounts], bool]:
    """Return (counts, stale).

    A failed aggregate serves the last good value flagged stale, or an empty
    mapping — which shapes into `null` fields, never into zeros. Reporting 0 for
    a count we could not take is the exact defect this module exists to remove.
    """
    # Validated before the try below, because the try exists to absorb a
    # database hiccup — not to absorb a caller passing garbage. Calling a
    # FastAPI handler directly (offline tests, scripts/verify_read_live.py)
    # bypasses `Query(...)` resolution and hands `window` the sentinel object
    # itself; without this guard that programming error is swallowed as
    # "aggregates failed", and the endpoint degrades to stale on every single
    # call while every test still reports green.
    if window not in WINDOW_HOURS:
        raise ValueError(
            f"unknown window {window!r}; expected one of {sorted(WINDOW_HOURS)}"
        )

    now = time.monotonic() if now is None else now
    fetch = fetch or fetch_stage_counts

    # Every return is a shallow copy of the cache's internal dict, never the
    # live object: StageCounts is frozen so a caller can't corrupt the values,
    # but it could still add/drop keys in the outer mapping and silently
    # mutate what every other poller reads next.
    hit = STAGE_CACHE.fresh(window, now)
    if hit is not None:
        return dict(hit), False
    try:
        counts = await fetch(session, window)
    except Exception:
        logger.exception("pipeline aggregates failed (window=%s)", window)
        last = STAGE_CACHE.last(window)
        return (dict(last), True) if last is not None else ({}, True)
    STAGE_CACHE.put(window, now, counts)
    return dict(counts), False


def summarize_content(row) -> str:
    head = (row.title or row.text or "").strip().replace("\n", " ")
    head = head[:60] + "…" if len(head) > 60 else head
    return f"{row.source} · {row.kind}" + (f" · {head}" if head else "")


def summarize_scored(row) -> str:
    # scored_at can be set with a null score when the model abstained; "—" says
    # that, where a formatted 0.00 would claim a neutral verdict we never made.
    score = f"{row.sentiment_score:+.2f}" if row.sentiment_score is not None else "—"
    return f"{row.source} · sentiment {score}"


def summarize_signal(row) -> str:
    outcome = "escaladé" if row.escalated else row.block_reason
    return f"{row.symbol} · score {row.opportunity_score} · {outcome}"


def summarize_journal(row) -> str:
    return (
        f"{row.symbol} · Sonnet {row.sonnet_direction or '—'} "
        f"· score {row.sonnet_score if row.sonnet_score is not None else '—'}"
    )


def summarize_decision(row) -> str:
    return f"{row.symbol} · {row.direction} · confiance {row.confidence:.0%}"


def summarize_approved(row) -> str:
    return f"{row.symbol} · {row.direction} · taille {row.position_size_pct:.1%}"


def summarize_trade(row) -> str:
    out = f"{row.symbol} · {row.direction} · {row.status}"
    if row.fill_price is not None:
        out += f" @ {row.fill_price}"
    if row.pnl is not None:
        out += f" · PnL {row.pnl}"
    return out


# Per-stage drawer: the last N items that crossed one stage, plus a breakdown.
MAX_ITEMS = 50
DEFAULT_ITEMS = 20


def _item(at, symbol, summary, detail, correlation_id=None) -> dict:
    return {
        "at": at.isoformat() if at else None,
        "symbol": symbol,
        "summary": summary,
        "detail": detail,
        "correlation_id": correlation_id,
    }


def content_item(row) -> dict:
    return _item(row.fetched_at, (row.symbols or [None])[0], summarize_content(row),
                 {"source": row.source, "kind": row.kind, "url": row.url})


def scored_item(row) -> dict:
    return _item(row.scored_at, (row.symbols or [None])[0], summarize_scored(row),
                 {"score": row.sentiment_score, "model": row.sentiment_model})


def signal_item(row) -> dict:
    return _item(
        row.time, row.symbol, summarize_signal(row),
        {"score": row.opportunity_score, "confidence": row.confidence,
         "factors_present": row.factors_present, "block_reason": row.block_reason},
        (row.payload or {}).get("correlation_id"),
    )


def journal_item(row) -> dict:
    return _item(
        row.time, row.symbol, summarize_journal(row),
        {"score": row.score, "sonnet_score": row.sonnet_score,
         "sonnet_validated": row.sonnet_validated, "skip_reason": row.skip_reason},
        row.correlation_id,
    )


def decision_item(row) -> dict:
    return _item(
        row.created_at, row.symbol, summarize_decision(row),
        {"direction": row.direction, "score": row.opportunity_score,
         "ai_validated": row.ai_validated},
        row.correlation_id,
    )


def approved_item(row) -> dict:
    return _item(
        row.created_at, row.symbol, summarize_approved(row),
        {"size_pct": row.position_size_pct, "stop_loss": row.stop_loss,
         "take_profit": row.take_profit, "rr": row.risk_reward_ratio},
        row.correlation_id,
    )


def trade_item(row) -> dict:
    return _item(
        row.created_at, row.symbol, summarize_trade(row),
        {"status": row.status, "fill_price": row.fill_price, "pnl": row.pnl},
        row.correlation_id,
    )


def reason_breakdown(rows) -> list[dict]:
    """Group rejection reasons the way the funnel does, biggest first."""
    totals: dict[str, int] = {}
    for reason, count in rows:
        key = collapse_reason(str(reason))
        totals[key] = totals.get(key, 0) + int(count)
    return [
        {"key": k, "count": v}
        for k, v in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]


async def _grouped(session, col, *where) -> list[dict]:
    rows = (
        await session.execute(
            select(col, func.count()).where(and_(*where)).group_by(col)
        )
    ).all()
    # A NULL group is a real bucket — rows that carry no reason — but rendering
    # it as the literal "None" in a French drawer dresses an absence up as a
    # category. `skip_reason` is nullable and this is the one grouped column
    # that can be NULL today.
    return [
        {"key": "non renseigné" if k is None else str(k), "count": int(c)}
        for k, c in sorted(rows, key=lambda r: r[1], reverse=True)
    ]


async def _rejection_breakdown(session, stage_name, since) -> list[dict]:
    rows = (
        await session.execute(
            select(PipelineRejection.reason, func.count())
            .where(
                and_(
                    PipelineRejection.time >= since,
                    PipelineRejection.stage == stage_name,
                )
            )
            .group_by(PipelineRejection.reason)
        )
    ).all()
    return reason_breakdown(rows)


@dataclass(frozen=True)
class _DetailCtx:
    """Everything a per-stage builder needs, so each one stays a two-liner."""

    session: Any  # AsyncSession; typed loosely so this module stays import-light
    since: datetime
    since_aware: datetime
    limit: int
    counts: StageCounts

    async def rows(self, model, order_col, *where):
        stmt = (
            select(model)
            .where(and_(*where))
            .order_by(order_col.desc())
            .limit(self.limit)
        )
        return (await self.session.execute(stmt)).scalars().all()


async def _detail_collect(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(
        RawContent, RawContent.fetched_at, RawContent.fetched_at >= ctx.since_aware
    )
    breakdown = await _grouped(
        ctx.session, RawContent.source, RawContent.fetched_at >= ctx.since_aware
    )
    prices = await _count(ctx.session, Price, Price.time >= ctx.since)
    breakdown.append({"key": "prices", "count": prices})
    return [content_item(r) for r in rows], breakdown


async def _detail_sentiment(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(
        RawContent, RawContent.scored_at, RawContent.scored_at >= ctx.since_aware
    )
    return [scored_item(r) for r in rows], [
        {"key": "scoré", "count": ctx.counts.volume or 0},
        {"key": "en attente", "count": ctx.counts.dropped or 0},
    ]


async def _detail_triage(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(Signal, Signal.time, Signal.time >= ctx.since)
    breakdown = await _grouped(
        ctx.session, Signal.block_reason,
        Signal.time >= ctx.since, Signal.escalated.is_(False),
    )
    return [signal_item(r) for r in rows], breakdown


async def _detail_senior(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(
        DecisionJournal, DecisionJournal.time,
        DecisionJournal.time >= ctx.since, DecisionJournal.sonnet_called.is_(True),
    )
    breakdown = await _grouped(
        ctx.session, DecisionJournal.skip_reason,
        DecisionJournal.time >= ctx.since,
        DecisionJournal.escalated.is_(True),
        DecisionJournal.sonnet_called.is_(False),
    )
    return [journal_item(r) for r in rows], breakdown


async def _detail_decision(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(
        Decision, Decision.created_at, Decision.created_at >= ctx.since
    )
    breakdown = await _rejection_breakdown(ctx.session, "decision_engine", ctx.since)
    return [decision_item(r) for r in rows], breakdown


async def _detail_risk(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(Trade, Trade.created_at, Trade.created_at >= ctx.since)
    breakdown = await _rejection_breakdown(ctx.session, "risk_engine", ctx.since)
    return [approved_item(r) for r in rows], breakdown


async def _detail_execute(ctx) -> tuple[list[dict], list[dict]]:
    rows = await ctx.rows(
        Trade, Trade.created_at,
        Trade.created_at >= ctx.since, Trade.status.in_(EXECUTED_STATUSES),
    )
    breakdown = await _grouped(ctx.session, Trade.status, Trade.created_at >= ctx.since)
    return [trade_item(r) for r in rows], breakdown


# Explicit dispatch rather than an if/elif chain: a stage added to STAGE_SPECS
# without a builder here is caught by a test, not by an operator staring at an
# empty drawer that reads like a stage nothing ever crossed.
DETAIL_BUILDERS = {
    "collect": _detail_collect,
    "sentiment": _detail_sentiment,
    "triage": _detail_triage,
    "senior": _detail_senior,
    "decision": _detail_decision,
    "risk": _detail_risk,
    "execute": _detail_execute,
}


async def fetch_stage_detail(session, stage_id: str, window: str, limit: int) -> dict:
    """Items + breakdown for one stage. `stage_id` is validated by the caller."""
    spec = STAGE_BY_ID[stage_id]
    since, since_aware = _cutoffs(window)
    counts = (await stage_counts_cached(session, window))[0].get(
        stage_id, StageCounts()
    )
    ctx = _DetailCtx(session, since, since_aware, limit, counts)
    items, breakdown = await DETAIL_BUILDERS[stage_id](ctx)

    return {
        "id": spec.id,
        "label": spec.label,
        "window": window,
        "volume": counts.volume,
        "dropped": counts.dropped,
        "breakdown": breakdown,
        "items": items,
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }
