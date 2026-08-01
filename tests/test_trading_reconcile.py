# tests/test_trading_reconcile.py
import asyncio

from cmi_common.events.execution import ExecutionKind
from tests.trading_helpers import load_module


class FakeCacheClient:
    def __init__(self, members):
        self._members = set(members)

    async def smembers(self, key):
        return set(self._members)

    async def srem(self, key, member):
        self._members.discard(member)


class FakeCache:
    def __init__(self, positions, exposure=0.5):
        self._client = FakeCacheClient(list(positions.keys()))
        self._positions = positions
        self._values = {"risk:exposure": exposure}

    @property
    def client(self):
        return self._client

    async def get_json(self, key):
        if key.startswith("trading:position:"):
            return self._positions.get(key.split(":")[-1])
        return self._values.get(key)

    async def set_json(self, key, value, ttl_seconds=60):
        self._values[key] = value


class FakeProducer:
    def __init__(self):
        self.published = []

    async def publish(self, topic, event):
        self.published.append(event)


class FakeKraken:
    def __init__(self, open_pairs):
        self._open = open_pairs

    async def get_open_positions(self):
        return {"openPositions": [{"symbol": p} for p in self._open]}


def _reconciler(cache, producer, kraken):
    mod = load_module("reconcile")
    return mod.Reconciler(cache, producer, kraken)


def test_closed_position_emits_and_frees_exposure() -> None:
    positions = {
        "evt-1": {
            "symbol": "SOL",
            "pair": "PF_SOLUSD",
            "position_size_pct": 0.04,
            "entry_price": 150.0,
            "side": "buy",
        }
    }
    cache = FakeCache(positions, exposure=0.30)
    producer = FakeProducer()
    kraken = FakeKraken(open_pairs=[])  # SOL no longer open -> closed
    asyncio.run(_reconciler(cache, producer, kraken).sweep())
    assert producer.published[0].kind == ExecutionKind.CLOSED
    assert round(cache._values["risk:exposure"], 4) == 0.26  # 0.30 - 0.04


def test_still_open_position_is_left_alone() -> None:
    positions = {
        "evt-1": {
            "symbol": "SOL",
            "pair": "PF_SOLUSD",
            "position_size_pct": 0.04,
            "entry_price": 150.0,
            "side": "buy",
        }
    }
    cache = FakeCache(positions, exposure=0.30)
    producer = FakeProducer()
    kraken = FakeKraken(open_pairs=["PF_SOLUSD"])
    asyncio.run(_reconciler(cache, producer, kraken).sweep())
    assert producer.published == []
    assert cache._values["risk:exposure"] == 0.30


class ExplodingKraken:
    """A venue that has gone away — Kraken retired demo-futures and the
    endpoint now 301s to a marketing page."""

    def __init__(self):
        self.calls = 0

    async def get_open_positions(self):
        self.calls += 1
        raise RuntimeError("301 Moved Permanently")


async def test_run_sweeps_immediately_rather_than_waiting_out_the_interval():
    # main.py used to await an extra sweep at startup, unguarded, three lines
    # before scheduling run(). That call was deleted; this pins what makes the
    # deletion safe -- run() reconciles on its first iteration, not after the
    # interval elapses.
    reconcile = load_module("reconcile")
    kraken = ExplodingKraken()
    r = reconcile.Reconciler(FakeCache({}), FakeProducer(), kraken)
    task = asyncio.create_task(r.run(3600))
    await asyncio.sleep(0.05)
    r.stop()
    await asyncio.wait_for(task, timeout=2)
    assert kraken.calls >= 1


async def test_an_unreachable_venue_does_not_stop_the_reconciler():
    # The failure that took the trading engine down: the boot sweep raised out
    # of _startup, so the process exited before commands.run() was scheduled --
    # the control consumer had already been assigned control.commands and then
    # never polled it. Every operator command was published and silently
    # dropped, including the kill switch.
    #
    # A venue we cannot reach is exactly when the control plane matters most,
    # so it must never gate startup.
    reconcile = load_module("reconcile")
    kraken = ExplodingKraken()
    r = reconcile.Reconciler(FakeCache({}), FakeProducer(), kraken)
    task = asyncio.create_task(r.run(0.01))
    await asyncio.sleep(0.08)
    r.stop()
    await asyncio.wait_for(task, timeout=2)
    assert kraken.calls >= 2  # kept sweeping despite every attempt raising
