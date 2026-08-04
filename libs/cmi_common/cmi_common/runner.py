"""Background task helpers for services: periodic pollers and consumer loops."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .config import get_settings
from .observability import PERIODIC_TICKS

logger = logging.getLogger(__name__)

#: Echecs consecutifs au-dela desquels une tache est declaree en panne.
#: Sur un cycle de 5 min, trois echecs valent 15 minutes de panne integrale
#: avant l'alerte: un rate-limit transitoire ne fait pas clignoter, et on ne
#: reproduit pas les 28 heures pendant lesquelles collector-binance-futures a
#: rate 100% de ses cycles en se declarant healthy.
UNHEALTHY_AFTER = 3


@dataclass(slots=True)
class TaskState:
    #: Nom de la tache, repris ici pour que failing_tasks() reste lisible dans
    #: une reponse JSON sans que l'appelant ait a rezipper les cles.
    name: str = ""
    consecutive_failures: int = 0
    last_success: float | None = None
    last_error: str | None = None


#: Etat par nom de tache. Global au processus, comme les compteurs Prometheus:
#: un service execute ses taches dans un seul event loop.
TASK_HEALTH: dict[str, TaskState] = {}


def failing_tasks() -> dict[str, TaskState]:
    """Taches ayant depasse le seuil d'echecs consecutifs."""
    return {
        name: state
        for name, state in TASK_HEALTH.items()
        if state.consecutive_failures >= UNHEALTHY_AFTER
    }


def _record(name: str, *, error: BaseException | None) -> None:
    state = TASK_HEALTH.setdefault(name, TaskState(name=name))
    service = get_settings().service_name
    if error is None:
        state.consecutive_failures = 0
        state.last_success = time.time()
        PERIODIC_TICKS.labels(service, name, "ok").inc()
        return
    state.consecutive_failures += 1
    state.last_error = f"{type(error).__name__}: {error}"
    PERIODIC_TICKS.labels(service, name, "error").inc()


async def run_periodic(
    coro_factory: Callable[[], Awaitable[None]],
    interval_seconds: float,
    *,
    name: str = "task",
) -> None:
    """Run ``coro_factory`` every ``interval_seconds`` until cancelled.

    Exceptions in a single tick are logged and swallowed so one bad poll never
    kills the loop -- but they are *counted*. Swallowing without counting is
    what made a collector failing every cycle indistinguishable from a healthy
    one: the traceback went to the log, `/health` kept answering 200, and the
    axis it feeds stayed empty for 28 hours without a single alert.
    """
    logger.info("starting periodic task '%s' every %ss", name, interval_seconds)
    TASK_HEALTH.setdefault(name, TaskState(name=name))
    try:
        while True:
            started = asyncio.get_event_loop().time()
            try:
                await coro_factory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("periodic task '%s' tick failed", name)
                _record(name, error=exc)
            else:
                _record(name, error=None)
            elapsed = asyncio.get_event_loop().time() - started
            await asyncio.sleep(max(0.0, interval_seconds - elapsed))
    except asyncio.CancelledError:
        logger.info("periodic task '%s' cancelled", name)
        raise
