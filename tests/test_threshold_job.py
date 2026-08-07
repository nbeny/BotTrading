"""Le job de scan : un seul à la fois, un échec s'écrit plutôt que de se taire."""

from __future__ import annotations

from contextlib import asynccontextmanager

from service_modules import load_service_module

from cmi_common.cache import LockNotAcquiredError

job_mod = load_service_module("decision-engine", "threshold_job")


class _Session:
    def __init__(self) -> None:
        self.added: list = []
        self.committed = False

    def add(self, row) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class _Db:
    def __init__(self, session) -> None:
        self._session = session

    def sessionmaker(self):
        return self._session


class _Cache:
    """Même contrat que la façade réelle : `lock(blocking=False)` lève
    `LockNotAcquiredError` plutôt que de céder le passage quand `held`."""

    def __init__(self, *, held: bool = False) -> None:
        self.held = held
        self.acquired = 0
        #: (name, timeout, blocking) de chaque appel -- pin la clé exacte que
        # `job.run_once` demande : c'est elle que api-gateway sonde en dur
        # (`lock:threshold-scan`, autre service, ne peut pas importer
        # `LOCK_NAME`) pour `running`. Une clé qui dérive ici rendrait
        # `running` silencieusement et durablement faux.
        self.calls: list[tuple[str, float, bool]] = []

    # Mirrors Cache.lock's real (name, timeout, blocking) signature.
    @asynccontextmanager
    async def lock(self, name, timeout=30.0, blocking=True):  # noqa: ASYNC109
        self.calls.append((name, timeout, blocking))
        if self.held:
            raise LockNotAcquiredError(name)
        self.held = True
        self.acquired += 1
        try:
            yield object()
        finally:
            self.held = False


async def test_successful_scan_persists_an_ok_row() -> None:
    session = _Session()
    cache = _Cache()

    async def fake_scan(_session, days):
        return "SCAN"

    def fake_analyze(scan, *, days, target_per_day):
        assert scan == "SCAN"
        return type("R", (), {"to_payload": lambda self: {"axes": []}})()

    job = job_mod.ThresholdScanJob(
        _Db(session),
        cache,
        days=7,
        target_per_day=200,
        scan_window=fake_scan,
        analyze=fake_analyze,
    )
    assert await job.run_once() is True
    assert session.committed is True
    assert len(session.added) == 1
    assert session.added[0].status == "ok"
    assert session.added[0].payload == {"axes": []}
    # Clé exacte demandée à la façade, cf. commentaire de `_Cache.calls`.
    assert cache.calls == [("threshold-scan", job_mod.LOCK_TIMEOUT_S, False)]


async def test_failed_scan_persists_an_error_row_rather_than_nothing() -> None:
    session = _Session()

    async def boom(_session, days):
        raise RuntimeError("stream died")

    job = job_mod.ThresholdScanJob(
        _Db(session),
        _Cache(),
        days=7,
        target_per_day=200,
        scan_window=boom,
        analyze=lambda *a, **k: None,
    )
    assert await job.run_once() is True
    assert len(session.added) == 1
    assert session.added[0].status == "error"
    assert "stream died" in session.added[0].error
    assert session.added[0].payload == {}


async def test_second_scan_is_ignored_while_one_runs() -> None:
    """Sur 2 vCPU en concurrence avec le pipeline, deux scans simultanés ne
    sont pas une option -- et une demande refusée n'est pas une erreur."""
    session = _Session()
    cache = _Cache(held=True)

    async def never(_session, days):
        raise AssertionError("le scan n'aurait pas du demarrer")

    job = job_mod.ThresholdScanJob(
        _Db(session),
        cache,
        days=7,
        target_per_day=200,
        scan_window=never,
        analyze=lambda *a, **k: None,
    )
    assert await job.run_once() is False  # refus silencieux, pas d'exception
    assert session.added == []
