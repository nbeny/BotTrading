"""CandleSweeper orchestration, with a fake store and no database.

Mirrors tests/test_adaptive_poll_loop.py: `Sleeps` breaks the infinite loop by
raising StopAsyncIteration after a fixed number of sleeps.
"""

from __future__ import annotations

from decimal import Decimal

from service_modules import load_service_module

pairs = load_service_module("collector-kraken", "domain.pairs")
sweeper_mod = load_service_module("collector-kraken", "application.sweeper")
CandleSweeper = sweeper_mod.CandleSweeper
VenuePairSpec = pairs.VenuePairSpec

OHLC_ROW = [1690000000, "1.0", "2.0", "0.5", "1.5", "1.2", "10.0", 3]


def _spec(symbol):
    return VenuePairSpec(symbol=symbol, pair=f"{symbol}USD", ordermin=Decimal("1"))


class FakeCache:
    async def allow(self, *_a) -> bool:
        return True


class Sleeps:
    def __init__(self, stop_after: int) -> None:
        self.calls: list[float] = []
        self._left = stop_after

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._left -= 1
        if self._left <= 0:
            raise StopAsyncIteration


class FakeStore:
    def __init__(self, universe, majors) -> None:
        self._universe = universe
        self._majors = majors
        self.saved: list = []
        self.depths: list = []
        self.health: list = []
        self.epoch_calls: list = []

    async def resolve(self, payload, *, min_mentions):
        return self._universe, self._majors

    async def last_candle_epoch(self, symbol, interval):
        self.epoch_calls.append((symbol, interval))
        return 1689990000

    async def save_candles(self, candles):
        self.saved.extend(candles)
        return len(candles)

    async def save_depth(self, snapshot):
        self.depths.append(snapshot)
        return 1

    async def report_health(self, *, interval, candles):
        self.health.append((interval, candles))


class StubClient:
    name = "kraken"
    rate_limit = (60, 60)

    def __init__(self, *, raises=None) -> None:
        self._raises = raises
        self.ohlc_calls: list = []
        self.depth_calls: list = []

    async def asset_pairs(self):
        return {"error": [], "result": {}}

    async def ohlc(self, pair, *, interval_minutes, since=None):
        self.ohlc_calls.append((pair, interval_minutes, since))
        if self._raises is not None:
            raise self._raises
        return {"error": [], "result": {pair: [OHLC_ROW]}}

    async def depth(self, pair, *, count=20):
        self.depth_calls.append(pair)
        return {
            "error": [],
            "result": {
                pair: {"asks": [["2.0", "1.0", 1]], "bids": [["1.0", "1.0", 1]]}
            },
        }


async def _run(sweeper) -> None:
    try:
        await sweeper.run()
    except StopAsyncIteration:
        pass


def _sweeper(store, client, sleeps, **kw):
    params = dict(
        interval="1h",
        cadence=900.0,
        majors_only=False,
        with_depth=True,
        min_mentions=10,
    )
    params.update(kw)
    return CandleSweeper(client, store, FakeCache(), sleep=sleeps, **params)


async def test_a_cycle_stores_candles_for_every_universe_symbol():
    store = FakeStore([_spec("BTC"), _spec("ETH")], [_spec("BTC")])
    client = StubClient()
    await _run(_sweeper(store, client, Sleeps(1)))
    assert [c.symbol for c in store.saved] == ["BTC", "ETH"]
    assert store.health == [("1h", 2)]


async def test_a_cycle_sleeps_its_cadence():
    store = FakeStore([_spec("BTC")], [])
    sleeps = Sleeps(1)
    await _run(_sweeper(store, StubClient(), sleeps))
    assert sleeps.calls == [900.0]


async def test_majors_only_sweeps_only_the_majors():
    store = FakeStore([_spec("BTC"), _spec("DEXE")], [_spec("BTC")])
    client = StubClient()
    await _run(_sweeper(store, client, Sleeps(1), majors_only=True, interval="15m"))
    assert [c.symbol for c in store.saved] == ["BTC"]
    assert client.ohlc_calls == [("BTCUSD", 15, 1689990000)]


async def test_depth_is_skipped_when_disabled():
    """The majors loop refreshes candles fast; the book is about tradability
    and does not need a 5-minute refresh."""
    store = FakeStore([_spec("BTC")], [_spec("BTC")])
    client = StubClient()
    await _run(_sweeper(store, client, Sleeps(1), with_depth=False))
    assert client.depth_calls == []
    assert store.depths == []


async def test_depth_is_measured_when_enabled():
    store = FakeStore([_spec("BTC")], [])
    client = StubClient()
    await _run(_sweeper(store, client, Sleeps(1), with_depth=True))
    assert client.depth_calls == ["BTCUSD"]
    assert store.depths[0].symbol == "BTC"


async def test_the_since_cursor_comes_from_the_last_stored_candle():
    """Incremental fetch: a normal cycle pulls a handful of candles, and a
    restart after downtime backfills from where storage left off."""
    store = FakeStore([_spec("BTC")], [])
    client = StubClient()
    await _run(_sweeper(store, client, Sleeps(1)))
    assert store.epoch_calls == [("BTC", "1h")]
    assert client.ohlc_calls[0][2] == 1689990000


async def test_a_failing_cycle_backs_off_and_does_not_kill_the_loop():
    store = FakeStore([_spec("BTC")], [])
    client = StubClient(raises=RuntimeError("kraken exploded"))
    sleeps = Sleeps(2)
    await _run(_sweeper(store, client, sleeps))
    assert sleeps.calls[0] == 60.0
    assert client.ohlc_calls, "the loop retried instead of dying"
