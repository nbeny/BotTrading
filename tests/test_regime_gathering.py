"""Parse-guards of regime_api's Redis gathering — pure fakes, no I/O."""

from datetime import UTC, datetime

from service_modules import load_service_module

regime_api = load_service_module("api-gateway", "regime_api")


class _Client:
    def __init__(self, items):
        self._items = items
        self.calls: list[list[str]] = []

    async def mget(self, keys):
        self.calls.append(list(keys))
        return self._items


class _Cache:
    def __init__(self, items):
        self.client = _Client(items)


async def test_feature_rows_tolerates_garbage() -> None:
    cache = _Cache(
        ['{"funding_rate_8h": 0.0001}', None, "not json", '"a string"', "[1,2]"]
    )
    rows = await regime_api._feature_rows(cache, {"BTC", "ETH", "SOL", "DOGE", "ADA"})
    assert rows == [{"funding_rate_8h": 0.0001}]  # dict JSON only
    assert cache.client.calls == [
        [f"features:{s}" for s in sorted({"BTC", "ETH", "SOL", "DOGE", "ADA"})]
    ]


async def test_feature_rows_empty_universe_skips_redis() -> None:
    cache = _Cache([])
    assert await regime_api._feature_rows(cache, set()) == []
    assert cache.client.calls == []  # no round-trip for nothing


def test_floats_rejects_bools_and_non_numbers() -> None:
    rows = [
        {"x": 0.5},
        {"x": True},
        {"x": "0.7"},
        {"x": None},
        {},
        {"x": 2},
    ]
    assert regime_api._floats(rows, "x") == [0.5, 2.0]


class _Row:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _DerivSession:
    """One canned result for `select(func.max(DerivativesSnapshot.time))`."""

    def __init__(self, value):
        self._value = value

    async def execute(self, _stmt):
        return _Row(self._value)


async def test_derivatives_as_of_present() -> None:
    at = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    session = _DerivSession([at])
    assert await regime_api._derivatives_as_of(session) == at.isoformat()


async def test_derivatives_as_of_empty_table() -> None:
    session = _DerivSession(None)
    assert await regime_api._derivatives_as_of(session) is None
