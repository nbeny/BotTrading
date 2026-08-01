"""One Binance futures cycle: funding for everything, detail for majors."""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal

from cmi_common.events import DerivativesEvent
from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_PRODUCED, UNMEASURED

from ..domain.mapper import to_derivatives_events, with_majors_detail
from ..infrastructure.binance_client import (
    BinanceFuturesClient,
    BinanceRateLimitedError,
)

logger = logging.getLogger(__name__)
SERVICE = "collector-binance-futures"

#: Consecutive cycles with no usable broad tier before the collector says so.
#:
#: It counts *unavailability*, not empty responses, and that distinction is the
#: whole point. An earlier version counted only an HTTP 200 carrying an empty
#: list — which /fapi/v1/premiumIndex essentially never returns — while every
#: real way Binance goes away (451 geo-block, 429, 418 ban, 5xx) raises before
#: reaching the counter. The guard written precisely because renormalisation
#: would absorb a permanently missing positioning axis without a murmur could
#: not, in fact, see any of those.
UNAVAILABLE_CYCLES_BEFORE_WARNING = 3

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
        #: Consecutive cycles with no usable broad tier, however it failed.
        self.unavailable_cycles = 0

    async def poll_once(self) -> int:
        priced, majors, ambiguous = self._universe()
        if not priced:
            # A cold start, or a price collector outage, otherwise looks
            # identical to "Binance lists nothing we trade".
            logger.warning("binance futures poll skipped: priced universe is empty")
            return 0

        try:
            rows = await self._client.premium_index()
        except Exception as exc:
            self._broad_tier_unavailable(type(exc).__name__)
            raise
        if not rows:
            self._broad_tier_unavailable("empty")
            return 0
        self.unavailable_cycles = 0

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

    def _broad_tier_unavailable(self, reason: str) -> None:
        self.unavailable_cycles += 1
        UNMEASURED.labels(SERVICE, "funding_rate", reason).inc()
        if self.unavailable_cycles >= UNAVAILABLE_CYCLES_BEFORE_WARNING:
            logger.warning(
                "binance broad tier unavailable for %d cycles (%s) — "
                "geo-block, ban or outage?",
                self.unavailable_cycles,
                reason,
            )

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
        # BinanceRateLimitedError deliberately escapes both handlers. It exists
        # so a caller can back off, and swallowing it meant the collector kept
        # firing 22 requests per cycle into an endpoint that had already said
        # stop -- which is how Binance escalates a 429 into an 418 ban.
        try:
            reading = await self._client.open_interest(ticker)
        except BinanceRateLimitedError:
            raise
        except Exception as exc:
            UNMEASURED.labels(SERVICE, "open_interest", type(exc).__name__).inc()
            logger.warning("open interest failed for %s", ticker, exc_info=True)
        try:
            ratio = await self._client.long_short_ratio(ticker)
        except BinanceRateLimitedError:
            raise
        except Exception as exc:
            UNMEASURED.labels(SERVICE, "long_short_ratio", type(exc).__name__).inc()
            logger.warning("long/short failed for %s", ticker, exc_info=True)
        return with_majors_detail(
            event,
            open_interest_usd=Decimal(str(reading.usd)) if reading else None,
            oi_change_pct_24h=reading.change_pct_24h if reading else None,
            long_short_ratio=ratio,
        )
