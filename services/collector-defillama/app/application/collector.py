"""One DefiLlama polling cycle."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_PRODUCED

from ..domain.mapper import emission_key, to_fundamentals_events
from ..domain.unlocks import Unlock
from ..infrastructure.llama_client import LlamaClient

logger = logging.getLogger(__name__)
SERVICE = "collector-defillama"

#: Each unlock document is ~2.25 MB, so this bounds how many *downloads* a cycle
#: may make — never how many tokens it reports on. Cached readings are served
#: for the whole eligible set on every cycle; only misses consume the budget.
#:
#: An earlier version capped map membership instead, which meant 37 of 40 tokens
#: declared "no schedule known" about schedules already sitting in Redis. Each
#: of those dropped the dilution axis, and renormalisation scores a dropped axis
#: better than a measured bad one — so the cap silently promoted every token it
#: skipped.
DEFAULT_MAX_UNLOCK_FETCHES = 3


@dataclass(frozen=True, slots=True)
class _Stats:
    """What one cycle's unlock pass cost, logged as separate counts.

    Their sum cannot distinguish "nothing was eligible" from "everything
    failed", and those call for opposite responses from an operator.
    """

    eligible: int = 0
    cached: int = 0
    fetched: int = 0
    failed: int = 0


class DefiLlamaCollector:
    def __init__(
        self,
        client: LlamaClient,
        producer: EventProducer,
        *,
        known_tokens: Callable[[], dict[str, str]],
        max_unlock_fetches: int = DEFAULT_MAX_UNLOCK_FETCHES,
    ) -> None:
        self._client = client
        self._producer = producer
        self._known_tokens = known_tokens
        self._max_unlock_fetches = max_unlock_fetches
        #: Rotating cursor over the eligible slugs, so every protocol comes up
        #: in turn instead of the first few starving the rest.
        self._cursor = 0

    async def poll_once(self) -> int:
        known = self._known_tokens()
        if not known:
            # Otherwise a cold start, or an api-gateway persister outage, is
            # byte-identical in the logs to "no DefiLlama protocol is in our
            # universe" — after paying for the 3.7 MB fees payload.
            logger.warning("defillama poll skipped: token universe is empty")
            return 0

        protocols = await self._client.protocols()
        fees = await self._client.fees()
        unlocks, stats = await self._collect_unlocks(protocols, known)

        events = to_fundamentals_events(
            protocols, fees=fees, unlocks=unlocks, known=known
        )
        for event in events:
            await self._producer.publish(Topic.FUNDAMENTALS, event)
            EVENTS_PRODUCED.labels(
                SERVICE, Topic.FUNDAMENTALS.value, event.event_type
            ).inc()
        # The parts are logged separately because their sum cannot distinguish
        # "nothing eligible" from "everything failed" — and those call for
        # opposite responses from an operator.
        logger.info(
            "defillama poll published %d events "
            "(%d cached, %d fetched, %d failed, %d eligible)",
            len(events),
            stats.cached,
            stats.fetched,
            stats.failed,
            stats.eligible,
        )
        return len(events)

    async def _collect_unlocks(
        self, protocols: list[dict[str, Any]], known: dict[str, str]
    ) -> tuple[dict[str, Unlock | None], _Stats]:
        """Every unlock reading we can serve, plus what it cost.

        A coin id lands in the map only when its schedule was actually read —
        cached or freshly fetched. Any failure leaves the key out, so the event
        reports "no schedule known" rather than "no unlock coming": the mapper
        keys on membership, and the scorer reads a known-empty schedule as its
        best possible fundamentals reading.
        """
        scheduled = await self._client.emission_slugs()
        # emission_key, not row["slug"] — the emissions list is keyed by parent
        # slug. Several deployment rows collapse onto the same key (Aave is
        # seven), hence the dedupe: fetching a 2.25 MB document once per
        # deployment would multiply the cost by the deployment count.
        #
        # sorted() is load-bearing, not cosmetic: the cursor indexes into this
        # list, and set iteration order varies with PYTHONHASHSEED, so without
        # it the round-robin would be meaningless across restarts and
        # inconsistent between replicas.
        eligible = sorted(
            {
                (key, row["gecko_id"])
                for row in protocols
                if (key := emission_key(row)) in scheduled
                and row.get("gecko_id")
                and row["gecko_id"] in known
            }
        )
        if not eligible:
            return {}, _Stats()

        result: dict[str, Unlock | None] = {}
        misses: list[tuple[str, str]] = []
        for slug, coin_id in eligible:
            # Free: a Redis read, not a 2.25 MB download. Every eligible token
            # gets one, so a schedule already cached is reported on every cycle
            # rather than on the few where its slug sits in the cursor window.
            hit, unlock = await self._client.cached_unlock(coin_id)
            if hit:
                result[coin_id] = unlock
            else:
                misses.append((slug, coin_id))

        failed = 0
        for offset in range(min(self._max_unlock_fetches, len(misses))):
            slug, coin_id = misses[(self._cursor + offset) % len(misses)]
            try:
                result[coin_id] = await self._client.unlock(slug, coin_id)
            # Exception, not BaseException, and that matters: CancelledError
            # derives from BaseException, so a shutdown cancel propagates
            # instead of being logged as a failed unlock lookup. Nor can this
            # be narrowed — the client raises across httpx, Redis, JSON decoding
            # and next_unlock's own ValueError, and whichever type were missed
            # would reintroduce the present-with-None inversion.
            except Exception:
                failed += 1
                logger.warning("unlock lookup failed for %s", slug, exc_info=True)
        if misses:
            # The cursor rotates over the misses so a persistently failing
            # protocol cannot starve the rest of the backlog.
            self._cursor = (self._cursor + self._max_unlock_fetches) % len(misses)
        return result, _Stats(
            eligible=len(eligible),
            cached=len(eligible) - len(misses),
            fetched=min(self._max_unlock_fetches, len(misses)) - failed,
            failed=failed,
        )
