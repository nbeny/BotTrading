"""Live smoke check: call every api-gateway read endpoint against the real DB in
one event loop and assert each response satisfies read_contract.CONTRACT. Seed
rows via psql first; run inside the api-gateway container. Exits non-zero if any
endpoint is unreachable or missing required keys. Not a unit test.
"""

from __future__ import annotations

import asyncio
import sys

from cmi_common import Settings
from cmi_common.db import Database

from app import events_api, read_api
from app.read_contract import CONTRACT

settings = Settings()
db = Database(settings.db)


def _check(name: str, resp) -> list[str]:
    required = CONTRACT[name]
    if isinstance(resp, list):
        if not resp:
            # An empty list has no keys to check. That is not a pass — it is an
            # unanswered question, and PLAUSIBILITY below is what answers it.
            return []
        target = resp[0]
    else:
        target = resp
    return sorted(required - set(target))


#: Endpoints that must not merely conform — they must carry data. Each entry is
#: (predicate over the response, failure message). A shape-only check passed
#: happily on two empty tables for weeks; this is the layer that would have
#: caught it.
def _has_rows(resp) -> bool:
    return bool(resp)


def _any_sentiment(resp) -> bool:
    return any(t.get("sentiment_score") for t in resp or [])


def _any_liquidity(resp) -> bool:
    return any(t.get("liquidity_usd") for t in resp or [])


def _real_names(resp) -> bool:
    """A name equal to its own symbol means the tokens table is still empty."""
    return any(t.get("name") != t.get("symbol") for t in resp or [])


PLAUSIBILITY = {
    "market/news": [
        (_has_rows, "no news rows — is collector-news writing raw_content?"),
    ],
    "market/tokens": [
        (_has_rows, "no tokens"),
        (_any_sentiment, "every sentiment_score is 0 — check content_sentiment_agg"),
        (_any_liquidity, "every liquidity_usd is 0 — is collector-kraken running?"),
        (_real_names, "every name equals its symbol — tokens table not populated"),
    ],
}


async def main() -> int:
    failures: list[str] = []
    async with db._sessionmaker() as s:  # noqa: SLF001
        calls = [
            ("portfolio", read_api.portfolio(session=s)),
            ("portfolio/positions", read_api.portfolio_positions(session=s)),
            ("portfolio/trades", read_api.portfolio_trades(limit=50, session=s)),
            ("portfolio/history", read_api.portfolio_history(range="30d", session=s)),
            ("market/tokens", read_api.market_tokens(session=s)),
            ("market/news", read_api.market_news(limit=20, session=s)),
            ("market/decisions", read_api.market_decisions(limit=30, session=s)),
            ("risk/exposure", read_api.risk_exposure(session=s)),
            ("risk/limits", read_api.risk_limits(session=s)),
            ("risk/alerts", read_api.risk_alerts(limit=30, session=s)),
            (
                "data/content",
                read_api.data_content(
                    category="all",
                    symbol=None,
                    q=None,
                    sentiment="all",
                    limit=50,
                    offset=0,
                    session=s,
                ),
            ),
            ("data/stats", read_api.data_stats(session=s)),
            ("systems/overview", read_api.systems_overview(session=s)),
            # Every filter branch at once: the offline tests can only prove this
            # query *compiles*, and its two riskiest parts -- the expanding IN
            # and the CAST that works around text()'s bind-parameter regex --
            # fail at execution, not at compile time.
            (
                "events",
                events_api.list_events(
                    limit=5,
                    types="PriceEvent,DecisionEvent",
                    symbol="BTC",
                    before=None,
                    session=s,
                ),
            ),
        ]
        for name, coro in calls:
            try:
                res = await coro
            except Exception as e:  # noqa: BLE001
                print(f"ERR  {name}  ->  {type(e).__name__}: {e}")
                failures.append(name)
                continue
            missing = _check(name, res)
            if missing:
                print(f"FAIL {name}  ->  missing {missing}")
                failures.append(name)
                continue
            broken = [msg for pred, msg in PLAUSIBILITY.get(name, []) if not pred(res)]
            if broken:
                for msg in broken:
                    print(f"THIN {name}  ->  {msg}")
                failures.append(name)
                continue
            print(f"OK   {name}  ->  {str(res)[:120]}")
    await db.dispose()
    if failures:
        print(f"\n{len(failures)} endpoint(s) failed: {failures}")
        return 1
    print("\nall read endpoints conform to CONTRACT")
    return 0


sys.exit(asyncio.run(main()))
