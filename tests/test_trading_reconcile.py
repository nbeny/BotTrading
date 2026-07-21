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
    positions = {"evt-1": {"symbol": "SOL", "pair": "PF_SOLUSD",
                           "position_size_pct": 0.04, "entry_price": 150.0, "side": "buy"}}
    cache = FakeCache(positions, exposure=0.30)
    producer = FakeProducer()
    kraken = FakeKraken(open_pairs=[])  # SOL no longer open -> closed
    asyncio.run(_reconciler(cache, producer, kraken).sweep())
    assert producer.published[0].kind == ExecutionKind.CLOSED
    assert round(cache._values["risk:exposure"], 4) == 0.26  # 0.30 - 0.04


def test_still_open_position_is_left_alone() -> None:
    positions = {"evt-1": {"symbol": "SOL", "pair": "PF_SOLUSD",
                           "position_size_pct": 0.04, "entry_price": 150.0, "side": "buy"}}
    cache = FakeCache(positions, exposure=0.30)
    producer = FakeProducer()
    kraken = FakeKraken(open_pairs=["PF_SOLUSD"])
    asyncio.run(_reconciler(cache, producer, kraken).sweep())
    assert producer.published == []
    assert cache._values["risk:exposure"] == 0.30
