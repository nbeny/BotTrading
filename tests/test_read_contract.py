"""Offline contract-parity: every read endpoint's response satisfies CONTRACT.

No DB — pure mappers/compute functions and a _FakeSession drive each endpoint,
mirroring tests/test_api_gateway_read.py. Guards against backend↔frontend shape
drift: a renamed/removed response field breaks this test in CI, not the browser.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from service_modules import load_service_module

read_api = load_service_module("api-gateway", "read_api")
journal_api = load_service_module("api-gateway", "journal_api")
events_api = load_service_module("api-gateway", "events_api")
systems_pipeline = load_service_module("api-gateway", "systems_pipeline")
regime_api = load_service_module("api-gateway", "regime_api")

compute_exposure = read_api.compute_exposure
compute_portfolio = read_api.compute_portfolio
compute_risk_alerts = read_api.compute_risk_alerts
compute_risk_limits = read_api.compute_risk_limits
map_decision = read_api.map_decision
map_news = read_api.map_news
map_portfolio_trade = read_api.map_portfolio_trade
map_position = read_api.map_position
map_price_point = read_api.map_price_point
map_token = read_api.map_token

CONTRACT = load_service_module("api-gateway", "read_contract").CONTRACT

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


# ── fake rows ─────────────────────────────────────────────────────────────────
def _price(**kw):
    base = dict(
        symbol="BTC",
        price_usd=110.0,
        price_change_pct_24h=6.0,
        volume_24h_usd=1000.0,
        market_cap_usd=1e9,
        time=NOW,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _trade(**kw):
    base = dict(
        event_id="t1",
        symbol="BTC",
        direction="long",
        entry_price=100.0,
        position_size_pct=0.1,
        stop_loss=90.0,
        take_profit=120.0,
        fill_price=101.0,
        status="filled",
        pnl=5.0,
        created_at=NOW,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _news(**kw):
    base = dict(
        id=1,
        title="t",
        url="http://x",
        source="CoinDesk",
        symbols=["BTC"],
        sentiment_score=0.3,
        published_at=NOW,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _decision(**kw):
    base = dict(
        event_id="d1",
        symbol="BTC",
        direction="long",
        opportunity_score=80,
        confidence=0.9,
        rationale="x",
        created_at=NOW,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Result:
    def __init__(self, rows=None, scalar=0):
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar


class _FakeSession:
    """Returns a fresh empty result per execute; each supports scalar & rows."""

    def __init__(self, n):
        self._n = n

    async def execute(self, _stmt, _params=None):
        """``_params`` is optional: most endpoints bind parameters into the
        statement, the journal summary passes them as a second argument."""
        if self._n <= 0:
            return _Result()
        self._n -= 1
        return _Result()

    async def scalar(self, _stmt):
        """Counting endpoints call `session.scalar` directly rather than going
        through `execute`. Returning 0 exercises the all-zeros path, which is
        the current production state of the pipeline."""
        return 0


# ── assertions ────────────────────────────────────────────────────────────────
_ASSERTED: set[str] = set()


def _assert_keys(name: str, obj: dict) -> None:
    _ASSERTED.add(name)
    missing = CONTRACT[name] - set(obj)
    assert not missing, f"{name} missing keys: {sorted(missing)}"


def _assert_exact_keys(name: str, obj: dict) -> None:
    """Like `_assert_keys`, but forbids extra keys too.

    Used for the nested shapes the graph reads per node, where a field the
    frontend has never heard of is drift just as much as a missing one.

    Registration and assertion live in one call on purpose: a bare
    `_ASSERTED.add(...)` next to a hand-written assert would let someone later
    register an entry without enforcing it, and the meta-test below would still
    report the manifest as fully covered.
    """
    _ASSERTED.add(name)
    assert set(obj) == CONTRACT[name], (
        f"{name}: missing {sorted(CONTRACT[name] - set(obj))}, "
        f"unexpected {sorted(set(obj) - CONTRACT[name])}"
    )


# ── item-shape endpoints (pure mappers) ───────────────────────────────────────
def test_market_tokens_contract() -> None:
    item = map_token(
        _price(),
        meta=SimpleNamespace(coin_id="bitcoin", name="BTC"),
        opportunity_score=80,
        sentiment_score=0.4,
    )
    _assert_keys("market/tokens", item)
    _assert_keys("market/token", item)


def test_market_prices_and_history_contract() -> None:
    item = map_price_point(_price())
    _assert_keys("market/prices", item)
    _assert_keys("portfolio/history", item)  # history items are {t, price}


def test_market_news_contract() -> None:
    _assert_keys("market/news", map_news(_news()))


def test_market_decisions_contract() -> None:
    _assert_keys("market/decisions", map_decision(_decision()))


def test_portfolio_positions_contract() -> None:
    _assert_keys("portfolio/positions", map_position(_trade(), _price()))


def test_portfolio_trades_contract() -> None:
    _assert_keys("portfolio/trades", map_portfolio_trade(_trade()))


# ── object / aggregate endpoints (pure compute) ───────────────────────────────
def test_portfolio_contract() -> None:
    _assert_keys("portfolio", compute_portfolio([], 0.0, now=NOW))


def test_risk_exposure_contract() -> None:
    _assert_keys("risk/exposure", compute_exposure([], 0.0, 0.0, now=NOW))


def test_risk_limits_contract() -> None:
    exp = compute_exposure([], 0.0, 0.0, now=NOW)
    _assert_keys("risk/limits", compute_risk_limits(exp, 50.0)[0])


def test_risk_alerts_contract() -> None:
    # An unprotected asset triggers an "unp-" info alert → a representative item.
    exp = compute_exposure(
        [{"symbol": "BTC", "value_usd": 100.0, "protected": False}],
        100.0,
        0.0,
        now=NOW,
    )
    _assert_keys("risk/alerts", compute_risk_alerts(exp, now=NOW)[0])


# ── endpoints needing a session (empty stubs; objects stay fully shaped) ───────
async def test_data_stats_contract() -> None:
    resp = await read_api.data_stats(session=_FakeSession(8))
    _assert_keys("data/stats", resp)


async def test_data_content_contract() -> None:
    resp = await read_api.data_content(
        category="all",
        symbol=None,
        q=None,
        sentiment="all",
        limit=50,
        offset=0,
        session=_FakeSession(8),
    )
    _assert_keys("data/content", resp)


async def test_systems_overview_contract() -> None:
    # window is passed explicitly: calling the handler directly bypasses
    # FastAPI, so the Query(...) default would arrive as the sentinel object.
    resp = await read_api.systems_overview(window="24h", session=_FakeSession(40))
    _assert_keys("systems/overview", resp)


async def test_trace_contract() -> None:
    resp = await read_api.trace(cid="corr-x", session=_FakeSession(8))
    _assert_keys("trace", resp)


async def test_systems_funnel_contract() -> None:
    resp = await read_api.systems_funnel(window="24h", session=_FakeSession(40))
    _assert_keys("systems/funnel", resp)


async def test_systems_journal_summary_contract() -> None:
    resp = await journal_api.journal_summary(window="30d", session=_FakeSession(40))
    _assert_keys("systems/journal/summary", resp)


def test_systems_overview_declares_the_pipeline_window_and_staleness() -> None:
    snap = read_api.assemble_systems_snapshot([])
    assert set(snap) >= CONTRACT["systems/overview"]


def test_pipeline_stage_shape_matches_the_contract() -> None:
    # Exact match, not _assert_keys' >=: an extra field the frontend does not
    # know about is drift too, and every node in the graph must carry it.
    snap = read_api.assemble_systems_snapshot([])
    assert len(snap["pipeline"]) == 7  # the static stage catalog, not DB rows
    for stage in snap["pipeline"]:
        _assert_exact_keys("systems/overview.pipeline[]", stage)


def test_stage_detail_item_shape_matches_the_contract() -> None:
    row = SimpleNamespace(
        symbol="SOL",
        opportunity_score=72,
        confidence=0.8,
        factors_present=3,
        escalated=True,
        block_reason="unknown",
        time=NOW,
        payload={"correlation_id": "cid-1"},
    )
    _assert_exact_keys("systems/stage.items[]", systems_pipeline.signal_item(row))


async def test_systems_stage_contract() -> None:
    # window/limit passed explicitly: calling the handler directly bypasses
    # FastAPI, so Query(...) defaults would arrive as sentinel objects, and
    # stage_counts_cached raises ValueError on an unrecognised window.
    resp = await read_api.systems_stage(
        stage_id="collect", window="24h", limit=20, session=_FakeSession(40)
    )
    _assert_keys("systems/stage", resp)


async def test_events_contract() -> None:
    resp = await events_api.list_events(
        limit=10, types=None, symbol=None, before=None, session=_FakeSession(4)
    )
    _assert_keys("events", resp)


def _scored_decision(**kw):
    base = dict(
        symbol="SOL",
        created_at=NOW,
        opportunity_score=84,
        confidence=0.62,
        payload={"meta": {"breakdown": {"volume_growth": 0.8, "positioning": 0.9}}},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _journal_row(**kw):
    base = dict(
        symbol="SOL",
        time=NOW,
        escalated=True,
        sonnet_called=True,
        sonnet_validated=False,
        skip_reason=None,
        decision_event_id=None,
        risk_verdict=None,
        risk_reason=None,
        execution_event_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_dossier_score_contract() -> None:
    _assert_exact_keys("market/dossier.score", read_api.build_score(_scored_decision()))


def test_dossier_pipeline_contract() -> None:
    _assert_exact_keys(
        "market/dossier.pipeline", read_api.build_pipeline(_journal_row(), None)
    )


async def test_dossier_contract() -> None:
    resp = await read_api.market_token_dossier(symbol="SOL", session=_FakeSession(8))
    _assert_keys("market/dossier", resp)


def test_an_absent_axis_never_reaches_the_wire_as_zero() -> None:
    """Garde-fou de bout en bout : le contrat autorise n'importe quel contenu
    dans `axes`, donc seule cette assertion empêche un axe non mesuré de partir
    à 0.0 vers le navigateur."""
    score = read_api.build_score(_scored_decision())
    assert set(score["axes"]) == {"volume_growth", "positioning"}
    assert "fundamentals" not in score["axes"]


class _FakeCacheClient:
    async def mget(self, keys):
        return [None] * len(keys)


class _FakeCache:
    client = _FakeCacheClient()

    async def get_json(self, _key):
        return None


async def test_market_regime_contract() -> None:
    regime_api.REGIME_CACHE.clear()
    resp = await regime_api.market_regime(session=_FakeSession(8), cache=_FakeCache())
    _assert_exact_keys("market/regime", resp)
    assert len(resp["drivers"]) == 5
    for d in resp["drivers"]:
        _assert_exact_keys("market/regime.drivers[]", d)
    assert resp["regime"] is None  # rien de mesuré → pas de régime deviné


class _SeqSession:
    """File de résultats préparés : un execute = un résultat, puis vide."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt, _params=None):
        return self._results.pop(0) if self._results else _Result()

    async def scalar(self, _stmt):
        return 0


async def test_decision_explain_contract() -> None:
    from types import SimpleNamespace

    decision = SimpleNamespace(
        event_id="d-1",
        symbol="BTC",
        direction="long",
        opportunity_score=72,
        confidence=0.6,
        payload={},
        correlation_id=None,
        created_at=None,
        rationale="",
    )
    # Query order: decision, then journal. With no cid and no created_at on
    # this fixture, decision_explain now skips the rejection lookup entirely
    # (an unbound guess is worse than none) and cid is None so trace() is
    # never called either — only two executes happen, not three.
    session = _SeqSession([_Result(rows=[decision]), _Result()])
    resp = await read_api.decision_explain(event_id="d-1", session=session)
    _assert_exact_keys("decisions/explain", resp)


async def test_journal_decisions_contract() -> None:
    resp = await journal_api.journal_decisions(
        window="30d", limit=50, offset=0, session=_FakeSession(3)
    )
    _assert_exact_keys("systems/journal/decisions", resp)
    sample = journal_api._map_row(
        {
            "time": None,
            "event_id": "j-1",
            "symbol": "BTC",
            "score": 60,
            "confidence": 0.5,
            "escalated": True,
            "sonnet_called": False,
            "sonnet_validated": None,
            "sonnet_direction": None,
            "risk_verdict": None,
            "decision_event_id": None,
            "correlation_id": None,
            f"pnl_{journal_api.PRIMARY_HORIZON}": None,
            f"outcome_{journal_api.PRIMARY_HORIZON}": None,
        }
    )
    _assert_exact_keys("systems/journal/decisions.rows[]", sample)


async def test_journal_calibration_contract() -> None:
    resp = await journal_api.journal_calibration(
        window="30d", threshold=70, session=_FakeSession(2)
    )
    _assert_exact_keys("systems/journal/calibration", resp)
    _assert_exact_keys("systems/journal/calibration.requested", resp["requested"])


async def test_journal_attribution_contract() -> None:
    resp = await journal_api.journal_attribution(window="30d", session=_FakeSession(2))
    _assert_exact_keys("systems/journal/attribution", resp)
    for f in resp["factors"]:
        _assert_exact_keys("systems/journal/attribution.factors[]", f)


# ── manifest coverage ─────────────────────────────────────────────────────────
# Defined last on purpose: pytest runs a module's tests in definition order, so
# every _assert_keys call above has already registered by the time this runs.
def test_every_contract_entry_is_actually_asserted() -> None:
    """A CONTRACT entry with no test is inert — it documents a shape nothing
    enforces, which is worse than no entry because it reads as coverage.

    This file enumerates endpoints by hand rather than iterating the manifest,
    so adding a key to CONTRACT alone changes nothing in CI. This test is what
    makes the manifest self-enforcing.

    ``market/signals`` is excluded by design: it returns a heterogeneous union
    of raw event dicts with no stable shared key set (see read_contract.py's
    module docstring), and is smoke-checked live instead.
    """
    expected = set(CONTRACT) - {"market/signals"}
    unasserted = expected - _ASSERTED
    assert not unasserted, (
        f"CONTRACT entries with no assertion: {sorted(unasserted)}. "
        "Add a test calling _assert_keys for each, or the manifest lies."
    )
