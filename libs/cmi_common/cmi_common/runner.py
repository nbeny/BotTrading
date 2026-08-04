"""Background task helpers for services: periodic pollers and consumer loops."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .config import get_settings
from .observability import PERIODIC_TICKS

logger = logging.getLogger(__name__)

#: Plancher d'echecs consecutifs avant de declarer une tache en panne. Trois
#: reste le minimum quelle que soit la cadence: en dessous, un rate-limit
#: transitoire ferait clignoter /health. threshold_for_interval() derive le
#: seuil reel a partir de la cadence de la tache; ce nombre n'est plus lui que
#: pour les TaskState construits a la main (tests) et pour les cadences trop
#: lentes pour que la fenetre visee y ajoute quoi que ce soit.
UNHEALTHY_AFTER = 3

#: Fenetre visee avant l'alerte, independamment de la cadence de la tache.
FAILURE_WINDOW_SECONDS = 15 * 60


def threshold_for_interval(interval_seconds: float) -> int:
    """Nombre d'echecs consecutifs valant environ FAILURE_WINDOW_SECONDS de panne.

    UNHEALTHY_AFTER fixe en nombre de ticks ne veut rien dire a travers des
    cadences allant de 10 s a 7 jours -- mesure sur les appelants reels de
    run_periodic, a nombre d'echecs egal (3, la valeur actuelle):

        tache                  | intervalle | 3 echecs valent
        sentiment-worker       | 10 s       | 30 s
        binance-futures-poll   | 5 min      | 15 min  (calibrage vise)
        sentiment-compaction   | 6 h        | 18 h
        github-lists           | 168 h      | 21 jours

    Deriver le seuil de la cadence reproduit exactement le calibrage voulu
    pour un poll de 5 min (3 echecs, inchange) et resserre les taches
    rapides: 30 s est trop court pour absorber un rate-limit transitoire sans
    faire clignoter /health, donc une tache toutes les 10 s a besoin de bien
    plus de 3 echecs avant de compter comme en panne. Pour les cadences tres
    lentes (6 h, 168 h), le plancher UNHEALTHY_AFTER reste la seule
    protection possible en nombre de ticks -- en dessous de trois, un seul
    tick raterait et suffirait a declarer la tache en panne. Au-dela, seul
    critical=False (voir run_periodic) empeche une tache de maintenance de
    faire declarer "unhealthy" un service par ailleurs sain.
    """
    if interval_seconds <= 0:
        return UNHEALTHY_AFTER
    return max(UNHEALTHY_AFTER, math.ceil(FAILURE_WINDOW_SECONDS / interval_seconds))


@dataclass(slots=True)
class TaskState:
    #: Nom de la tache, repris ici pour que failing_tasks() reste lisible dans
    #: une reponse JSON sans que l'appelant ait a rezipper les cles.
    name: str = ""
    consecutive_failures: int = 0
    last_success: float | None = None
    last_error: str | None = None
    #: Tache non critique (ex: compaction, rafraichissement de listes de
    #: reference): comptee et exposee en metrique comme les autres, mais
    #: ignoree par failing_tasks(). Une panne y merite une metrique, pas un
    #: conteneur declare mort alors que ses taches critiques tournent bien.
    critical: bool = True
    #: Echecs consecutifs a partir desquels la tache est en panne. Derive de
    #: la cadence par threshold_for_interval() dans run_periodic; vaut
    #: UNHEALTHY_AFTER par defaut pour les TaskState construits a la main.
    threshold: int = UNHEALTHY_AFTER


#: Etat par nom de tache. Global au processus, comme les compteurs Prometheus:
#: un service execute ses taches dans un seul event loop.
TASK_HEALTH: dict[str, TaskState] = {}


def failing_tasks() -> dict[str, TaskState]:
    """Taches critiques ayant depasse leur seuil d'echecs consecutifs.

    Une tache critical=False peut depasser son seuil sans jamais apparaitre
    ici: c'est le point qui l'empeche de faire basculer /health.
    """
    return {
        name: state
        for name, state in TASK_HEALTH.items()
        if state.critical and state.consecutive_failures >= state.threshold
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
    critical: bool = True,
) -> None:
    """Run ``coro_factory`` every ``interval_seconds`` until cancelled.

    Exceptions in a single tick are logged and swallowed so one bad poll never
    kills the loop -- but they are *counted*. Swallowing without counting is
    what made a collector failing every cycle indistinguishable from a healthy
    one: the traceback went to the log, `/health` kept answering 200, and the
    axis it feeds stayed empty for 28 hours without a single alert.

    ``critical=False`` marks a refresh/maintenance task whose failure should
    still be counted and exposed as a metric, but must never turn ``/health``
    503 by itself -- see ``failing_tasks()``.
    """
    threshold = threshold_for_interval(interval_seconds)
    logger.info(
        "starting periodic task '%s' every %ss (critical=%s, threshold=%d)",
        name,
        interval_seconds,
        critical,
        threshold,
    )
    TASK_HEALTH.setdefault(
        name, TaskState(name=name, critical=critical, threshold=threshold)
    )
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
