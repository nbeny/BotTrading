"""Le job de scan : un seul à la fois, un échec s'écrit plutôt que de se taire.

`Cache.lock` (la façade partagée) ne lève pas quand `acquire(blocking=False)`
échoue -- elle `yield`rait quand même le verrou (cf. threshold_job.py). Le job
acquiert donc son verrou via `cache.client.lock(...)`, l'API redis-py brute :
la fausse `_Cache` ci-dessous imite cette surface (un `.client` dont `.lock()`
rend un objet à `acquire()`/`release()` asynchrones), pas la façade `.lock()`.
"""

from __future__ import annotations

from service_modules import load_service_module

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


class _FakeLock:
    """Imite `redis.asyncio.lock.Lock` : `acquire()` rend False si tenu."""

    def __init__(self, cache: _Cache) -> None:
        self._cache = cache

    async def acquire(self) -> bool:
        if self._cache.held:
            return False
        self._cache.held = True
        self._cache.acquired += 1
        return True

    async def release(self) -> None:
        self._cache.held = False


class _FakeRedisClient:
    def __init__(self, cache: _Cache) -> None:
        self._cache = cache

    def lock(self, name, timeout=30.0, blocking=True):
        return _FakeLock(self._cache)


class _Cache:
    """Verrou Redis factice : `held` dit si quelqu'un le tient déjà."""

    def __init__(self, *, held: bool = False) -> None:
        self.held = held
        self.acquired = 0
        self.client = _FakeRedisClient(self)


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
