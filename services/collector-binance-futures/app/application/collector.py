"""One Binance futures cycle: funding for everything, detail for majors."""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal

from cmi_common.events import DerivativesEvent
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_PRODUCED

from ..domain.mapper import to_derivatives_events, with_majors_detail
from ..infrastructure.binance_client import BinanceFuturesClient

logger = logging.getLogger(__name__)
SERVICE = "collector-binance-futures"

#: Binance geo-blocks some IP ranges. An empty broad tier for this many cycles
#: means the source is gone, not that the market went quiet — and since the
#: scoring model *excludes* an absent axis rather than penalising it,
#: renormalisation would absorb a permanently missing positioning axis without
#: a murmur. The collector has to say so itself or nothing will.
EMPTY_CYCLES_BEFORE_WARNING = 3

#: (priced, majors, ambiguous)
Universe = Callable[[], tuple[set[str], set[str], set[str]]]


class BinanceFuturesCollector:
    def __init__(
        self,
        client: BinanceFuturesClient,
        producer: EventProducer,
        *,
        universe: Universe,
    ) -> None:
        self._client = client
        self._producer = producer
        self._universe = universe
        self.empty_cycles = 0

    async def poll_once(self) -> int:
        priced, majors, ambiguous = self._universe()
        if not priced:
            # A cold start, or a price collector outage, otherwise looks
            # identical to "Binance lists nothing we trade".
            logger.warning("binance futures poll skipped: priced universe is empty")
            return 0

        rows = await self._client.premium_index()
        if not rows:
            self.empty_cycles += 1
            if self.empty_cycles >= EMPTY_CYCLES_BEFORE_WARNING:
                logger.warning(
                    "binance broad tier empty for %d cycles — geo-block or outage?",
                    self.empty_cycles,
                )
            return 0
        self.empty_cycles = 0

        events = to_derivatives_events(rows, priced=priced, ambiguous=ambiguous)
        detailed = 0
        for event in events:
            if event.symbol in majors and not self._client.near_weight_ceiling:
                event = await self._detail(event)
                detailed += 1
            await self._producer.publish(Topic.DERIVATIVES, event)
            EVENTS_PRODUCED.labels(
                SERVICE, Topic.DERIVATIVES.value, event.event_type
            ).inc()
        logger.info(
            "binance futures poll published %d events (%d with majors detail)",
            len(events),
            detailed,
        )
        return len(events)

    async def _detail(self, event: DerivativesEvent) -> DerivativesEvent:
        """Fold in the per-symbol readings, tolerating either one failing.

        The two calls are caught independently: a flaky long/short endpoint
        must not cost us an open interest reading we already have, and neither
        must cost us the funding rate from the broad tier. Whatever was not
        measured stays None — which the scorer excludes rather than scoring, so
        a fabricated zero here would read as a real positioning signal.
        """
        ticker = f"{event.symbol}USDT"
        reading = None
        ratio = None
        try:
            reading = await self._client.open_interest(ticker)
        except Exception:
            logger.warning("open interest failed for %s", ticker, exc_info=True)
        try:
            ratio = await self._client.long_short_ratio(ticker)
        except Exception:
            logger.warning("long/short failed for %s", ticker, exc_info=True)
        return with_majors_detail(
            event,
            open_interest_usd=Decimal(str(reading.usd)) if reading else None,
            oi_change_pct_24h=reading.change_pct_24h if reading else None,
            long_short_ratio=ratio,
        )
