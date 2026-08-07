"""Déclenche, verrouille et persiste un scan de calibration du seuil.

Le verrou Redis est aussi l'état du job : `GET /systems/journal/threshold`
répond `running: true` tant qu'il est tenu, ce qui évite une machine à états
à maintenir en parallèle du travail réel.

`Cache.lock` (`libs/cmi_common/cmi_common/cache/redis.py`) ne convient pas
telle quelle pour ça : en `blocking=False`, `lock.acquire()` peut rendre
`False` (verrou déjà tenu) sans que la façade ne lève -- elle fait
`await lock.acquire()` puis `yield lock` inconditionnellement, verrou obtenu
ou non. L'utiliser ici laisserait deux scans tourner en même temps. Ce module
acquiert donc son verrou via `cache.client.lock(...)`, l'API redis-py brute,
et lève `ScanBusyError` lui-même quand `acquire()` rend `False` -- sans toucher à
la façade partagée, dont d'autres appelants (collecteurs) dépendent du
comportement actuel. Même préfixe de clé (`lock:{name}`) que la façade, pour
que la sonde de `GET /systems/journal/threshold` (`lock:threshold-scan`) le
retrouve.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from cmi_common.db import ThresholdReport as ThresholdReportRow

from .threshold_scan import analyze as _analyze
from .threshold_scan import scan_window as _scan_window

logger = logging.getLogger(__name__)

LOCK_NAME = "threshold-scan"
#: Large devant la durée observée du scan (minutes), assez court pour qu'un
#: processus tué ne bloque pas la demande suivante indéfiniment.
LOCK_TIMEOUT_S = 1800.0


class ScanBusyError(RuntimeError):
    """Levée quand le verrou est déjà tenu (`acquire()` a rendu False)."""


@asynccontextmanager
async def _try_lock(cache: Any, name: str, lock_timeout: float) -> AsyncIterator[None]:
    """Acquiert `lock:{name}` en non-bloquant ou lève `ScanBusyError`.

    Passe par `cache.client` (redis-py) plutôt que par la façade `Cache.lock`
    -- voir le docstring du module.
    """
    lock = cache.client.lock(f"lock:{name}", timeout=lock_timeout, blocking=False)
    acquired = await lock.acquire()
    if not acquired:
        raise ScanBusyError(name)
    try:
        yield None
    finally:
        with contextlib.suppress(Exception):
            await lock.release()


class ThresholdScanJob:
    def __init__(
        self,
        db: Any,
        cache: Any,
        *,
        days: int,
        target_per_day: int,
        scan_window: Callable[..., Any] = _scan_window,
        analyze: Callable[..., Any] = _analyze,
    ) -> None:
        self._db = db
        self._cache = cache
        self._days = days
        self._target = target_per_day
        self._scan_window = scan_window
        self._analyze = analyze

    async def run_once(self) -> bool:
        """True si le scan a tourné, False s'il a été refusé (déjà en cours)."""
        try:
            async with _try_lock(self._cache, LOCK_NAME, LOCK_TIMEOUT_S):
                await self._scan_and_store()
                return True
        except ScanBusyError:
            logger.info("threshold scan already running; request ignored")
            return False

    async def _scan_and_store(self) -> None:
        started = time.monotonic()
        status, error, payload = "ok", None, {}
        try:
            async with self._db.sessionmaker() as session:
                scan = await self._scan_window(session, self._days)
                report = self._analyze(
                    scan, days=self._days, target_per_day=self._target
                )
                payload = report.to_payload()
        except Exception as exc:  # l'échec doit s'écrire, pas se taire
            logger.exception("threshold scan failed")
            status, error = "error", f"{type(exc).__name__}: {exc}"
        async with self._db.sessionmaker() as session:
            session.add(
                ThresholdReportRow(
                    time=datetime.now(tz=UTC),
                    window_days=self._days,
                    target_per_day=self._target,
                    status=status,
                    error=error,
                    duration_s=round(time.monotonic() - started, 2),
                    payload=payload,
                )
            )
            await session.commit()
