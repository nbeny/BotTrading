"""One DefiLlama polling cycle."""

from __future__ import annotations

import logging
from collections.abc import Callable

from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_PRODUCED

from ..domain.mapper import emission_key, to_fundamentals_events
from ..domain.unlocks import Unlock
from ..infrastructure.llama_client import LlamaClient

logger = logging.getLogger(__name__)
SERVICE = "collector-defillama"

#: Each unlock document is ~2.25 MB. Three per cycle at a 600s cadence refreshes
#: a ~40-protocol universe in about two hours, well inside the 24h cache TTL,
#: while never pulling more than ~7 MB in one go.
DEFAULT_MAX_UNLOCK_FETCHES = 3


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
        protocols = await self._client.protocols()
        fees = await self._client.fees()
        unlocks = await self._collect_unlocks(protocols, known)

        events = to_fundamentals_events(
            protocols, fees=fees, unlocks=unlocks, known=known
        )
        for event in events:
            await self._producer.publish(Topic.FUNDAMENTALS, event)
            EVENTS_PRODUCED.labels(
                SERVICE, Topic.FUNDAMENTALS.value, event.event_type
            ).inc()
        logger.info(
            "defillama poll published %d events (%d unlocks known)",
            len(events),
            len(unlocks),
        )
        return len(events)

    async def _collect_unlocks(
        self, protocols: list[dict], known: dict[str, str]
    ) -> dict[str, Unlock | None]:
        """Unlock readings for the slice of the universe due this cycle.

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
            return {}

        result: dict[str, Unlock | None] = {}
        for offset in range(min(self._max_unlock_fetches, len(eligible))):
            slug, coin_id = eligible[(self._cursor + offset) % len(eligible)]
            try:
                result[coin_id] = await self._client.unlock(slug, coin_id)
            except Exception:
                logger.warning("unlock lookup failed for %s", slug, exc_info=True)
        self._cursor = (self._cursor + self._max_unlock_fetches) % len(eligible)
        return result
