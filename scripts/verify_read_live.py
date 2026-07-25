"""Live smoke check: call the api-gateway read endpoints directly against the
real DB, all within one event loop (avoids TestClient/asyncpg loop juggling).
Seed rows via psql first. Run inside the api-gateway container. Not a unit test.
"""

from __future__ import annotations

import asyncio

from cmi_common import Settings
from cmi_common.db import Database

from app import read_api

settings = Settings()
db = Database(settings.db)


async def main() -> None:
    async with db._sessionmaker() as s:  # noqa: SLF001
        calls = [
            ("market/tokens", read_api.market_tokens(session=s)),
            ("market/news", read_api.market_news(limit=20, session=s)),
            ("market/signals", read_api.market_signals(limit=30, session=s)),
            ("data/content", read_api.data_content(
                category="all", symbol=None, q=None, sentiment="all", limit=50, offset=0, session=s)),
            ("data/stats", read_api.data_stats(session=s)),
            ("portfolio", read_api.portfolio(session=s)),
            ("portfolio/positions", read_api.portfolio_positions(session=s)),
            ("risk/exposure", read_api.risk_exposure(session=s)),
            ("trace/corr-live-1", read_api.trace(cid="corr-live-1", session=s)),
            ("systems/overview", read_api.systems_overview(session=s)),
        ]
        for name, coro in calls:
            try:
                res = await coro
                print(f"OK   {name}  ->  {str(res)[:170]}")
            except Exception as e:  # noqa: BLE001
                print(f"ERR  {name}  ->  {type(e).__name__}: {e}")
    await db.dispose()


asyncio.run(main())
