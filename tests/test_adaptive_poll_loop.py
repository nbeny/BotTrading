"""AdaptivePollLoop: poll→persist; pause-until-reset on RateLimitedError; no failover."""

from __future__ import annotations

from cmi_common.sources import (
    AdaptivePollLoop, FakeContentRepository, RateLimitedError, RawItem,
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
    provider = StubProvider(items=[RawItem(source="stub", kind="social", external_id="1")])
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(provider, repo, FakeCache(), poll_interval=300,
                            service="collector-social", sleep=sleeps)
    await _run(loop)
    assert provider.calls == 1
    assert len(repo.rows) == 1
    assert sleeps.calls == [300]  # normal cadence


async def test_rate_limited_sleeps_retry_after_and_resumes_same_provider() -> None:
    repo = FakeContentRepository()
    provider = StubProvider(raises=RateLimitedError(45.0))
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(provider, repo, FakeCache(), poll_interval=300,
                            service="collector-social", sleep=sleeps)
    await _run(loop)
    assert sleeps.calls == [45.0]     # waited the API-provided reset, not the interval
    assert provider.calls == 1        # same provider — no failover to anything else


async def test_quota_guard_blocks_poll_and_waits_window() -> None:
    repo = FakeContentRepository()
    provider = StubProvider(items=[RawItem(source="stub", kind="social", external_id="1")])
    sleeps = Sleeps(stop_after=1)
    loop = AdaptivePollLoop(provider, repo, FakeCache(allow=False), poll_interval=300,
                            service="collector-social", sleep=sleeps)
    await _run(loop)
    assert provider.calls == 0        # never fetched — proactive budget spent
    assert sleeps.calls == [60]       # waited the rate-limit window
