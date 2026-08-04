"""Paginated read over the archived event stream.

Answers the question the Command Center could not: what happened before I opened
this page. The in-memory feed holds 200 entries and dies on reload.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from .events_cursor import decode, encode
from .routers import get_session_dep

router = APIRouter(tags=["events"])

MAX_LIMIT = 200

_COLUMNS = "time, event_id, event_type, topic, symbol, correlation_id, payload"


def assume_utc(time: datetime) -> datetime:
    """Attach UTC to a naive timestamp before it is handed back as a cursor.

    ``decode`` rejects a naive cursor time on purpose (see events_cursor.py),
    so encoding one would hand the client a token its own next request
    answers with a 400 -- pagination that dies on page 2 looking like a
    client bug. In normal operation ``time`` never arrives naive:
    ``events_market.time`` / ``events_signal.time`` are ``timestamptz`` and
    asyncpg decodes that type as timezone-aware regardless of how the row was
    written. This is defense in depth against a legacy row, a future
    regression, or a different driver -- the encode/decode contract, not a
    write-side convention, is what this guards.
    """
    return time.replace(tzinfo=UTC) if time.tzinfo is None else time


@router.get("/events")
async def list_events(
    limit: int = Query(100, ge=1, le=MAX_LIMIT),
    types: str | None = Query(None, description="comma-separated event_type list"),
    symbol: str | None = Query(None),
    before: str | None = Query(None, description="cursor from a previous page"),
    session: AsyncSession = Depends(get_session_dep),
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit + 1}
    where = ["TRUE"]
    expanding = False

    if before:
        try:
            cursor_time, cursor_id = decode(before)
        except ValueError as exc:
            # A malformed cursor is a client error, not an empty page: silently
            # returning the newest page would look like the end of history.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # CAST, not `:ct::timestamptz`: text()'s bind-parameter regex refuses a
        # name followed by a colon, so the postgres cast shorthand would leave
        # `:ct` uninterpolated and blow up at execution.
        where.append(
            "(time, event_id) < (CAST(:ct AS timestamptz), CAST(:cid AS text))"
        )
        params |= {"ct": cursor_time, "cid": cursor_id}
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol.upper()
    if types:
        wanted = [t.strip() for t in types.split(",") if t.strip()]
        if wanted:
            # `= ANY(:types)` would bind a python list to a scalar parameter,
            # which text() does not expand; an expanding IN renders one bind per
            # value and is driver-agnostic.
            where.append("event_type IN :types")
            params["types"] = wanted
            expanding = True

    clause = " AND ".join(where)
    # UNION ALL across the two retention tiers, ordered by the same composite key
    # the cursor uses, then one extra row to detect whether a next page exists.
    #
    # Each branch carries its own ORDER BY + LIMIT. Without them the outer LIMIT
    # applies only after the union, so Postgres would fetch and sort *every*
    # matching row in both hypertables first -- on events_market that is the
    # whole 7-day retention window of price, volume and dex traffic for an
    # unfiltered request. Bounding each branch is exact, not an approximation:
    # the global top N is necessarily contained in the union of the two per-table
    # top N, because both are ordered on the same key as the outer sort.
    branch = (
        f"(SELECT {_COLUMNS} FROM {{table}} WHERE {clause} "
        f"ORDER BY time DESC, event_id DESC LIMIT :limit)"
    )
    stmt = text(
        f"{branch.format(table='events_market')} "
        f"UNION ALL "
        f"{branch.format(table='events_signal')} "
        f"ORDER BY time DESC, event_id DESC LIMIT :limit"
    )
    if expanding:
        # Both halves of the UNION reuse the one name; SQLAlchemy expands it to
        # the same positional binds on each side, so no distinct names needed.
        stmt = stmt.bindparams(bindparam("types", expanding=True))
    rows = (await session.execute(stmt, params)).all()

    items = [dict(r._mapping) for r in rows[:limit]]
    # The extra row is the existence proof of a next page -- counting instead
    # would cost a second scan of both hypertables on every request.
    has_more = len(rows) > limit
    next_cursor = (
        encode(assume_utc(items[-1]["time"]), items[-1]["event_id"])
        if has_more and items
        else None
    )
    return {"items": items, "next_cursor": next_cursor}
