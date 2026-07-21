"""Background task helpers for services: periodic pollers and consumer loops."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def run_periodic(
    coro_factory: Callable[[], Awaitable[None]],
    interval_seconds: float,
    *,
    name: str = "task",
) -> None:
    """Run ``coro_factory`` every ``interval_seconds`` until cancelled.

    Exceptions in a single tick are logged and swallowed so one bad poll never
    kills the loop.
    """
    logger.info("starting periodic task '%s' every %ss", name, interval_seconds)
    try:
        while True:
            started = asyncio.get_event_loop().time()
            try:
                await coro_factory()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("periodic task '%s' tick failed", name)
            elapsed = asyncio.get_event_loop().time() - started
            await asyncio.sleep(max(0.0, interval_seconds - elapsed))
    except asyncio.CancelledError:
        logger.info("periodic task '%s' cancelled", name)
        raise
