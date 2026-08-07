"""Déclenche, verrouille et persiste un scan de calibration du seuil.

Le verrou Redis est aussi l'état du job : `GET /systems/journal/threshold`
répond `running: true` tant qu'il est tenu, ce qui évite une machine à états
à maintenir en parallèle du travail réel.

Le verrou passe par la façade partagée, `Cache.lock` (`libs/cmi_common/
cmi_common/cache/redis.py`) : en `blocking=False`, elle lève
`LockNotAcquiredError` plutôt que de laisser passer un appelant qui n'a pas
obtenu le verrou. Même préfixe de clé (`lock:{name}`) que ce que sonde
`GET /systems/journal/threshold` -- une seule construction de cette clé dans
tout le dépôt, pas une copie par service qui pourrait dériver et rendre
`running` silencieusement faux pour toujours.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from cmi_common.cache import LockNotAcquiredError
from cmi_common.db import ThresholdReport as ThresholdReportRow

from .threshold_scan import analyze as _analyze
from .threshold_scan import scan_window as _scan_window

logger = logging.getLogger(__name__)

LOCK_NAME = "threshold-scan"
#: Large devant la durée observée du scan (minutes), assez court pour qu'un
#: processus tué ne bloque pas la demande suivante indéfiniment -- mais un
#: process tué PENDANT un scan laisse quand même la clé tenue jusqu'à ce
#: délai : `running` reste vrai jusqu'à 30 min après un redéploiement survenu
#: en plein scan. Compromis connu d'un verrou à TTL, pas un bug à corriger.
LOCK_TIMEOUT_S = 1800.0


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
        """Lance un scan sous verrou, ou renonce si un autre tourne déjà.

        Rend `True` si le scan a tourné jusqu'au bout -- qu'il ait réussi ou
        échoué, les deux se persistent (`status="ok"`/`"error"`). Rend
        `False`, sans lever, si le verrou était déjà tenu (refus silencieux,
        pas une erreur -- cf. `test_second_scan_is_ignored_while_one_runs`).
        Peut néanmoins lever si l'écriture de la ligne elle-même échoue (base
        indisponible au moment du second `session.add`/`commit` dans
        `_scan_and_store`) : ce cas-là n'est pas absorbé ici, il l'est par
        l'appelant (boucle périodique ou tâche détachée sur commande), qui
        journalise et ne meurt pas pour autant.
        """
        try:
            async with self._cache.lock(
                LOCK_NAME, timeout=LOCK_TIMEOUT_S, blocking=False
            ):
                await self._scan_and_store()
                return True
        except LockNotAcquiredError:
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
