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
    if not statuses:
        return "idle"
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, len(_STATUS_RANK)))


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
