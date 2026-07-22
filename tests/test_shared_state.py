import asyncio


class FakeRedis:
    def __init__(self, members): self._members = members
    async def smembers(self, key): return set(self._members)


class FakeCache:
    def __init__(self, values, members):
        self._values = values
        self.client = FakeRedis(members)

    async def get_json(self, key): return self._values.get(key)


def test_positions_injects_event_id():
    from cmi_common.state import StateReader

    cache = FakeCache(
        values={
            "trading:position:e1": {
                "symbol": "SOL",
                "side": "buy",
                "size": 1.0,
                "entry_price": 140.0,
            }
        },
        members=["e1"],
    )
    reader = StateReader(cache, db=None)
    out = asyncio.run(reader.positions())
    assert out == [
        {
            "event_id": "e1",
            "symbol": "SOL",
            "side": "buy",
            "size": 1.0,
            "entry_price": 140.0,
        }
    ]


def test_pending_injects_event_id():
    from cmi_common.state import StateReader

    cache = FakeCache(
        values={
            "trading:pending:p1": {
                "symbol": "BTC",
                "side": "sell",
                "size": 0.5,
                "entry_price": 60000.0,
            }
        },
        members=["p1"],
    )
    reader = StateReader(cache, db=None)
    out = asyncio.run(reader.pending())
    assert out == [
        {
            "event_id": "p1",
            "symbol": "BTC",
            "side": "sell",
            "size": 0.5,
            "entry_price": 60000.0,
        }
    ]


def test_settings_defaults_empty():
    from cmi_common.state import StateReader

    reader = StateReader(FakeCache(values={}, members=[]), db=None)
    assert asyncio.run(reader.settings()) == {}
