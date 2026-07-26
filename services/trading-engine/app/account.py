"""Periodic account-balance poll.

Deliberately independent of the trading mode: reading a balance is always a real
call, so the operator sees their true portfolio while the bot is still in
dry_run. Only order placement is simulated.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from cmi_common.cache import Cache
from cmi_common.events.account import AccountSnapshotEvent
from cmi_common.kafka import EventProducer, Topic

from .kraken_spot import KrakenSpotClient

logger = logging.getLogger(__name__)

REDIS_KEY = "trading:account:{venue}"
# Twice the poll interval: a key that outlived two missed polls would let
# control-api serve a snapshot the read plane has already called stale.
CACHE_TTL_MULTIPLE = 2


class AccountPoller:
    def __init__(self, client, producer: EventProducer, cache: Cache, *,
                 venue: str, interval_s: int = 60) -> None:
        self._client = client
        self._producer = producer
        self._cache = cache
        self._venue = venue
        self._interval = interval_s
        self._stop = asyncio.Event()

    async def start(self) -> None:
        """Open the client's HTTP session.

        Owned here rather than by the caller so main.py never has to reach into
        `poller._client` — a private attribute a caller pokes at is the kind of
        coupling that breaks silently when the internals move.
        """
        await self._client.start()

    async def close(self) -> None:
        await self._client.close()

    async def poll_once(self) -> None:
        """Total by construction: never raises, so `run()` needs no guard of its
        own. Everything that can fail — the call, and the event construction that
        validates its payload — sits inside the try."""
        try:
            snap = await self._client.snapshot()
            # Projected field by field rather than splatted: the event forbids
            # extras, so a key added to snapshot() later would raise here and
            # turn every poll into a logged failure — i.e. no balance at all,
            # the exact bug this loop exists to fix.
            data = {
                "equity_usd": float(snap["equity_usd"]),
                "cash_usd": float(snap["cash_usd"]),
                "balances": dict(snap["balances"]),
            }
            event = AccountSnapshotEvent(venue=self._venue, **data)
        except Exception:
            # No snapshot beats a wrong one: absence reads as "not connected"
            # downstream, whereas a zero would read as "you own nothing".
            logger.warning("account snapshot failed for %s", self._venue,
                           exc_info=True)
            return
        await self._producer.publish(Topic.ACCOUNT_SNAPSHOT, event)
        await self._cache.set_json(
            REDIS_KEY.format(venue=self._venue),
            # `occurred_at` is timezone-aware, so the isoformat string carries
            # its offset and the read plane can compare it to now() without a
            # silent local-time shift when it computes staleness.
            {"venue": self._venue, "fetched_at": event.occurred_at.isoformat(),
             **data},
            ttl_seconds=self._interval * CACHE_TTL_MULTIPLE,
        )

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.poll_once()
            # Waiting on the stop event rather than sleeping: shutdown returns
            # immediately instead of after a whole interval.
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)

    def stop(self) -> None:
        self._stop.set()


def build_poller(config, producer, cache) -> AccountPoller | None:
    """None when no read-only key is configured.

    The spec's rule: no key means the venue is *absent*, not failing. Building a
    client anyway would produce a loop that errors every 60 seconds to say
    nothing.
    """
    if not (config.read_api_key and config.read_api_secret):
        return None
    client = KrakenSpotClient(config.read_api_key, config.read_api_secret)
    return AccountPoller(client, producer, cache, venue="kraken_spot",
                         interval_s=config.account_poll_s)
