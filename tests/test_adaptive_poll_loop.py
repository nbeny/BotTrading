"""AdaptivePollLoop: poll→persist; pause-until-reset on RateLimitedError; no failover."""

from __future__ import annotations

from cmi_common.sources import (
    AdaptivePollLoop,
    FakeContentRepository,
    RateLimitedError,
    RawItem,
)


class FakeCache:
    def __init__(self, allow: bool = True) -> None:
        self._allow = allow
        self.paused: dict[str, float] = {}

    async def allow(self, *_a) -> bool:
        return self._allow


class Sleeps:
    """Records sleeps and stops the loop after a fixed number of iterations."""

    def __init__(self, stop_after: int) -> None:
        self.calls: list[float] = []
        self._left = stop_after

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._left -= 1
        if self._left <= 0:
            raise StopAsyncIteration


class StubProvider:
    name = "stub"
    kind = "social"
    rate_limit = (60, 60)

    def __init__(self, *, items=None, raises=None) -> None:
        self._items = items or []
        self._raises = raises
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._items

    async def close(self) -> None:
        pass


async def _run(loop: AdaptivePollLoop) -> None:
    try:
        await loop.run()
    except StopAsyncIteration:
        pass


async def test_polls_and_persists_then_sleeps_interval() -> None:
    repo = FakeContentRepository()
    provider = StubProvider(
        items=[RawItem(source="stub", kind="social", external_id="1")]
    )
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(
        provider,
        repo,
        FakeCache(),
        poll_interval=300,
        service="collector-social",
        sleep=sleeps,
    )
    await _run(loop)
    assert provider.calls == 1
    assert len(repo.rows) == 1
    assert sleeps.calls == [300]  # normal cadence


async def test_rate_limited_sleeps_retry_after_and_resumes_same_provider() -> None:
    repo = FakeContentRepository()
    provider = StubProvider(raises=RateLimitedError(45.0))
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(
        provider,
        repo,
        FakeCache(),
        poll_interval=300,
        service="collector-social",
        sleep=sleeps,
    )
    await _run(loop)
    assert sleeps.calls == [45.0]  # waited the API-provided reset, not the interval
    assert provider.calls == 1  # same provider — no failover to anything else


async def test_quota_guard_blocks_poll_and_waits_window() -> None:
    repo = FakeContentRepository()
    provider = StubProvider(
        items=[RawItem(source="stub", kind="social", external_id="1")]
    )
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(
        provider,
        repo,
        FakeCache(allow=False),
        poll_interval=300,
        service="collector-social",
        sleep=sleeps,
    )
    await _run(loop)
    assert provider.calls == 0  # never fetched — proactive budget spent
    assert sleeps.calls == [60]  # waited the rate-limit window


class RaisingRepo:
    """A repository whose insert_items always fails (simulates a DB blip)."""

    async def insert_items(self, items) -> int:
        raise RuntimeError("db unavailable")


async def test_persist_error_backs_off_and_does_not_kill_loop() -> None:
    # A DB failure during persist must not silently kill the source's loop —
    # it backs off like any other error and the loop lives to poll again.
    provider = StubProvider(
        items=[RawItem(source="stub", kind="social", external_id="1")]
    )
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(
        provider,
        RaisingRepo(),
        FakeCache(),
        poll_interval=300,
        service="collector-social",
        error_backoff=120,
        sleep=sleeps,
    )
    await _run(loop)
    assert provider.calls == 1  # it did poll
    assert sleeps.calls == [120]  # backed off on the persist failure, loop survived


class RaisingCache:
    """A cache whose quota check fails (simulates a Redis blip).

    `Cache.allow` issues a bare `redis.incr` and has no error handling of its
    own, so a Redis hiccup surfaces here as a raw exception.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def allow(self, *_a) -> bool:
        self.calls += 1
        raise RuntimeError("redis unavailable")


async def test_quota_check_error_backs_off_and_does_not_kill_loop() -> None:
    # The defect that stopped ingestion for 12h on 2026-08-07: the quota check
    # sat *outside* the try, so one failed `redis.incr` during a memory squeeze
    # ended `run()`, the task died with nobody holding its exception, and the
    # container went on answering /health with 200 for half a day.
    repo = FakeContentRepository()
    provider = StubProvider(
        items=[RawItem(source="stub", kind="social", external_id="1")]
    )
    cache = RaisingCache()
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(
        provider,
        repo,
        cache,
        poll_interval=300,
        service="collector-social",
        error_backoff=120,
        sleep=sleeps,
    )
    await _run(loop)
    assert cache.calls == 1  # the quota check ran and blew up
    assert provider.calls == 0  # fail *closed*: never poll on an unknown budget
    assert sleeps.calls == [120]  # backed off like any other error, loop survived


class DyingLoop(AdaptivePollLoop):
    """A loop whose `run()` dies `deaths` times before returning normally."""

    def __init__(self, *a, deaths: int, **kw) -> None:
        super().__init__(*a, **kw)
        self._deaths = deaths
        self.runs = 0

    async def run(self) -> None:
        self.runs += 1
        if self.runs <= self._deaths:
            raise RuntimeError("boom")


async def test_run_forever_restarts_a_loop_that_died_unexpectedly() -> None:
    # Defence in depth behind the fix above: whatever escapes `run()` in the
    # future must be loud and must come back, not end ingestion in silence.
    sleeps = Sleeps(stop_after=99)
    loop = DyingLoop(
        StubProvider(),
        FakeContentRepository(),
        FakeCache(),
        poll_interval=300,
        service="collector-social",
        restart_delay=5,
        sleep=sleeps,
        deaths=2,
    )
    await loop.run_forever()
    assert loop.runs == 3  # died twice, restarted twice, then ran clean
    assert sleeps.calls == [5, 5]  # waited the restart delay between attempts


class DroppingNormalizer:
    """Normalizer stand-in: keeps nothing, records what it was handed."""

    def __init__(self) -> None:
        self.seen: list[RawItem] = []

    async def normalize(self, items: list[RawItem]) -> list[RawItem]:
        self.seen.extend(items)
        return []


async def test_normalizer_runs_between_fetch_and_persist() -> None:
    repo = FakeContentRepository()
    item = RawItem(source="stub", kind="social", external_id="1")
    provider = StubProvider(items=[item])
    normalizer = DroppingNormalizer()
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(
        provider,
        repo,
        FakeCache(),
        poll_interval=300,
        service="collector-social",
        sleep=sleeps,
        normalizer=normalizer,
    )
    await _run(loop)
    assert normalizer.seen == [item]  # it saw the fetched item
    assert repo.rows == []  # and its rejection reached the repository


async def test_loop_without_a_normalizer_persists_unchanged() -> None:
    # The hook is optional so existing wiring keeps working untouched.
    repo = FakeContentRepository()
    provider = StubProvider(
        items=[RawItem(source="stub", kind="social", external_id="1")]
    )
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(
        provider,
        repo,
        FakeCache(),
        poll_interval=300,
        service="collector-social",
        sleep=sleeps,
    )
    await _run(loop)
    assert len(repo.rows) == 1


class RaisingNormalizer:
    """Normalizer stand-in that fails, like a bad lexicon fetch would."""

    async def normalize(self, items: list[RawItem]) -> list[RawItem]:
        raise RuntimeError("lexicon unavailable")


async def test_normalizer_error_backs_off_and_does_not_persist() -> None:
    # A normalizer failure must be treated exactly like a DB failure: back off,
    # do not persist, and do not kill the loop.
    repo = FakeContentRepository()
    provider = StubProvider(
        items=[RawItem(source="stub", kind="social", external_id="1")]
    )
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(
        provider,
        repo,
        FakeCache(),
        poll_interval=300,
        service="collector-social",
        error_backoff=120,
        sleep=sleeps,
        normalizer=RaisingNormalizer(),
    )
    await _run(loop)
    assert provider.calls == 1  # it did poll
    assert repo.rows == []  # nothing reached the repository
    assert sleeps.calls == [120]  # backed off, loop survived


class CommittingProvider(StubProvider):
    """A provider holding a consumption cursor, like Telegram's.

    It cannot advance that cursor from `fetch()`: the items it just returned are
    not durable until `insert_items` has taken them, and only the loop knows
    whether that happened. `commit()` is how the loop says so.
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


async def test_commit_is_called_once_the_items_are_persisted() -> None:
    repo = FakeContentRepository()
    provider = CommittingProvider(
        items=[RawItem(source="stub", kind="social", external_id="1")]
    )
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(
        provider,
        repo,
        FakeCache(),
        poll_interval=300,
        service="collector-social",
        sleep=sleeps,
    )
    await _run(loop)
    assert len(repo.rows) == 1
    assert provider.commits == 1


async def test_commit_is_not_called_when_the_persist_fails() -> None:
    # The defect the seam exists for: a cursor advanced on a cycle whose items
    # the loop threw away is a permanent, silent hole in the ingestion.
    provider = CommittingProvider(
        items=[RawItem(source="stub", kind="social", external_id="1")]
    )
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(
        provider,
        RaisingRepo(),
        FakeCache(),
        poll_interval=300,
        service="collector-social",
        error_backoff=120,
        sleep=sleeps,
    )
    await _run(loop)
    assert provider.calls == 1  # it did poll
    assert provider.commits == 0  # ...and must not claim the items are durable
    assert sleeps.calls == [120]  # backed off like any other error


async def test_commit_is_not_called_when_the_normalizer_fails() -> None:
    # Same reason: nothing reached the repository, so nothing is durable.
    provider = CommittingProvider(
        items=[RawItem(source="stub", kind="social", external_id="1")]
    )
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(
        provider,
        FakeContentRepository(),
        FakeCache(),
        poll_interval=300,
        service="collector-social",
        error_backoff=120,
        sleep=sleeps,
        normalizer=RaisingNormalizer(),
    )
    await _run(loop)
    assert provider.commits == 0


async def test_a_provider_without_commit_is_driven_exactly_as_before() -> None:
    # Seven of the eight providers re-read a window each cycle and let
    # UNIQUE(source, external_id) absorb the overlap: they have no claim to
    # confirm, and `commit` must stay an optional capability rather than a
    # method the `Provider` protocol forces on all of them.
    repo = FakeContentRepository()
    provider = StubProvider(
        items=[RawItem(source="stub", kind="social", external_id="1")]
    )
    assert not hasattr(provider, "commit")
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(
        provider,
        repo,
        FakeCache(),
        poll_interval=300,
        service="collector-social",
        sleep=sleeps,
    )
    await _run(loop)
    assert len(repo.rows) == 1
    assert sleeps.calls == [300]
