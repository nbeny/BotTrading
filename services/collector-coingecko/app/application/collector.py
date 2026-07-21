"""Application use-case: poll CoinGecko and publish market events."""

from __future__ import annotations

import logging

from cmi_common.kafka import EventProducer, Topic
from cmi_common.observability import EVENTS_PRODUCED

from ..domain.mapper import to_price_event, to_volume_event
from ..infrastructure.coingecko_client import CoinGeckoClient

logger = logging.getLogger(__name__)
SERVICE = "collector-coingecko"


class CoinGeckoCollector:
    """Orchestrates one polling cycle: fetch markets + trending, publish."""

    def __init__(
        self,
        client: CoinGeckoClient,
        producer: EventProducer,
        *,
        pages: int = 2,
        per_page: int = 100,
    ) -> None:
        self._client = client
        self._producer = producer
        self._pages = pages
        self._per_page = per_page

    async def poll_once(self) -> int:
        trending = set(await self._client.trending())
        published = 0
        for page in range(1, self._pages + 1):
            rows = await self._client.markets(per_page=self._per_page, page=page)
            for row in rows:
                price = to_price_event(row, trending)
                await self._producer.publish(Topic.PRICE, price)
                EVENTS_PRODUCED.labels(
                    SERVICE, Topic.PRICE.value, price.event_type
                ).inc()
                published += 1

                volume = to_volume_event(row)
                if volume is not None:
                    await self._producer.publish(Topic.VOLUME, volume)
                    EVENTS_PRODUCED.labels(
                        SERVICE, Topic.VOLUME.value, volume.event_type
                    ).inc()
                    published += 1
        logger.info("coingecko poll published %d events", published)
        return published
