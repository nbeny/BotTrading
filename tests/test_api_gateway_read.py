"""Unit tests for the api-gateway live-mode read API.

The DB queries themselves need Postgres, but the row→response mappers and the
stats aggregation are pure functions, and the routing/response shapes are
verified with a fake session via FastAPI's dependency override.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from service_modules import load_service_module

# Every service ships a package named `app`; load this one under its own alias.
read_api = load_service_module("api-gateway", "read_api")
_health_collector = load_service_module("api-gateway", "health_collector")
_routers = load_service_module("api-gateway", "routers")
systems_pipeline = load_service_module("api-gateway", "systems_pipeline")

SERVICE_CATALOG = read_api.SERVICE_CATALOG
assemble_systems_snapshot = read_api.assemble_systems_snapshot
assemble_trace = read_api.assemble_trace
build_collectors = read_api.build_collectors
build_infra = read_api.build_infra
build_kafka = read_api.build_kafka
build_workers = read_api.build_workers
compute_content_stats = read_api.compute_content_stats
compute_exposure = read_api.compute_exposure
compute_portfolio = read_api.compute_portfolio
compute_risk_alerts = read_api.compute_risk_alerts
compute_risk_limits = read_api.compute_risk_limits
map_content = read_api.map_content
map_decision = read_api.map_decision
map_news = read_api.map_news
map_position = read_api.map_position
map_portfolio_trade = read_api.map_portfolio_trade
map_price_point = read_api.map_price_point
map_signal_event = read_api.map_signal_event
map_token = read_api.map_token

compute_detail = _health_collector.compute_detail
metric_sum = _health_collector.metric_sum
parse_prometheus = _health_collector.parse_prometheus

get_session_dep = _routers.get_session_dep

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


# ── pure mappers ──────────────────────────────────────────────────────────────
def test_map_token_enriched() -> None:
    price = SimpleNamespace(
        symbol="BTC",
        price_usd=66800,
        price_change_pct_24h=6.2,
        volume_24h_usd=1_000_000,
        market_cap_usd=1_300_000_000_000,
        time=NOW,
    )
    meta = SimpleNamespace(coin_id="bitcoin", name="Bitcoin")
    d = map_token(price, meta=meta, opportunity_score=88, sentiment_score=0.42)
    assert d["symbol"] == "BTC"
    assert d["coin_id"] == "bitcoin"
    assert d["price_usd"] == 66800.0
    assert d["opportunity_score"] == 0.88  # scaled 0..1
    assert d["sentiment_score"] == 0.42
    assert d["is_trending"] is True  # change >= 5
    assert d["updated_at"] == NOW.isoformat()


def test_map_token_defaults_without_meta() -> None:
    price = SimpleNamespace(
        symbol="SOL",
        price_usd=142,
        price_change_pct_24h=1.1,
        volume_24h_usd=None,
        market_cap_usd=None,
        time=NOW,
    )
    d = map_token(price)
    assert d["coin_id"] == "sol"
    assert d["name"] == "SOL"
    assert d["volume_24h_usd"] == 0.0
    assert d["is_trending"] is False


def test_map_price_point() -> None:
    row = SimpleNamespace(time=NOW, price_usd=100.5, volume_24h_usd=2000)
    assert map_price_point(row) == {
        "t": NOW.isoformat(),
        "price": 100.5,
        "volume": 2000.0,
    }


def test_map_news_timestamptz_conversion() -> None:
    row = SimpleNamespace(
        id=7,
        title="ETF approved",
        url="http://x",
        source="CoinDesk",
        symbols=["BTC"],
        sentiment_score=0.3,
        published_at=NOW,
    )
    d = map_news(row)
    assert d["id"] == "7"
    assert d["source"] == "CoinDesk"
    assert d["symbols"] == ["BTC"]
    assert d["published_at"].startswith("2026-07-25")


def test_map_news_reads_raw_content_columns():
    """raw_content.published_at is timestamptz, unlike the old epoch BigInteger."""
    row = SimpleNamespace(
        id=42,
        title="ETF approved",
        url="https://example.com/a",
        source="rss",
        symbols=["BTC"],
        sentiment_score=0.42,
        published_at=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
    )
    out = map_news(row)
    assert out["id"] == "42"
    assert out["title"] == "ETF approved"
    assert out["source"] == "rss"
    assert out["symbols"] == ["BTC"]
    assert out["sentiment"] == 0.42
    assert out["published_at"] == "2026-07-29T10:00:00+00:00"


def test_map_news_tolerates_unscored_and_untagged_rows():
    row = SimpleNamespace(
        id=7,
        title="t",
        url="u",
        source="gdelt",
        symbols=None,
        sentiment_score=None,
        published_at=None,
    )
    out = map_news(row)
    assert out["symbols"] == []
    assert out["sentiment"] == 0.0
    assert out["published_at"] is None


def test_map_signal_event_shape() -> None:
    row = SimpleNamespace(
        symbol="ETH",
        opportunity_score=82,
        confidence=0.7,
        reason="momentum",
        escalated=True,
        time=NOW,
    )
    d = map_signal_event(row)
    assert d["event_type"] == "AnalysisEvent"
    assert d["opportunity_score"] == 0.82
    assert d["escalate"] is True


def test_map_signal_event_normalises_the_score_to_the_frontend_scale() -> None:
    """`Signal` stores 0-100, the frontend speaks 0-1 everywhere else — and the
    mock store already emitted 0-1 for this same event type, so live and mock
    disagreed until this was fixed."""
    row = SimpleNamespace(
        symbol="BTC",
        opportunity_score=79,
        confidence=0.8,
        reason="x",
        escalated=False,
        time=NOW,
    )
    assert map_signal_event(row)["opportunity_score"] == 0.79


def test_map_decision_shape() -> None:
    row = SimpleNamespace(
        event_id="evt1",
        symbol="BTC",
        direction="long",
        opportunity_score=87,
        confidence=0.9,
        rationale="strong",
        created_at=NOW,
    )
    d = map_decision(row)
    assert d["id"] == "evt1"
    assert d["worker"] == "sonnet"
    assert d["decision"] == "LONG_SIGNAL"
    assert d["opportunity_score"] == 0.87


def _content(**kw):
    base = dict(
        id=1,
        source="Reddit",
        kind="social",
        url="http://x",
        title="t",
        text="body $BTC",
        symbols=["BTC"],
        published_at=NOW,
        fetched_at=NOW,
        sentiment_score=0.7,
        sentiment_confidence=0.8,
        sentiment_model="cryptobert",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_map_content_with_decision() -> None:
    d = map_content(_content(sentiment_score=0.7))
    assert d["source_category"] == "social"
    assert d["derived_decision"]["direction"] == "long"
    d2 = map_content(_content(sentiment_score=0.1))
    assert d2["derived_decision"] is None  # below threshold


def test_map_content_unscored() -> None:
    d = map_content(_content(sentiment_score=None, sentiment_model=None))
    assert d["model_name"] == "unscored"
    assert d["derived_decision"] is None


# ── stats aggregation ─────────────────────────────────────────────────────────
def test_compute_content_stats_counts_and_buckets() -> None:
    rows = [
        _content(
            kind="social",
            source="Reddit",
            symbols=["BTC"],
            sentiment_score=0.5,
            published_at=NOW - timedelta(hours=1),
        ),
        _content(
            kind="news",
            source="CoinDesk",
            symbols=["ETH", "BTC"],
            sentiment_score=-0.2,
            published_at=NOW - timedelta(hours=1),
        ),
        _content(
            kind="social",
            source="Reddit",
            symbols=["BTC"],
            sentiment_score=0.1,
            published_at=NOW - timedelta(hours=3),
        ),
    ]
    s = compute_content_stats(rows, now=NOW)
    assert s["total_24h"] == 3
    assert s["social_24h"] == 2
    assert s["news_24h"] == 1
    assert s["avg_sentiment"] == 0.0
    assert len(s["volume_series"]) == 12
    assert s["sentiment_series"] == []
    # BTC appears in all 3 rows → top mention
    assert s["mentions"][0] == {"symbol": "BTC", "count": 3}
    assert {"source": "Reddit", "count": 2} in s["top_sources"]
    # the 1h-ago bucket (index 10) holds 2 items (1 social + 1 news)
    assert s["volume_series"][10]["social"] == 1
    assert s["volume_series"][10]["news"] == 1


def test_compute_content_stats_empty() -> None:
    s = compute_content_stats([], now=NOW)
    assert s["total_24h"] == 0
    assert s["avg_sentiment"] == 0.0
    assert s["sentiment_series"] == []
    assert s["mentions"] == []


# ── routing / response-shape wiring (fake session) ────────────────────────────
class _Result:
    def __init__(self, rows=None, scalar=None):
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
    """Replays canned results positionally, and records which tables were read.

    The positional replay is table-agnostic, so a query pointed at the wrong
    model still gets handed the right-looking result — mutation testing during
    the T6 and T8 reviews confirmed such a swap goes undetected on values alone.
    `tables` is what lets a test assert *where* the data came from, not only
    what came back.
    """

    def __init__(self, results):
        self._results = list(results)
        self.tables: list[str] = []

    async def execute(self, stmt):
        try:
            self.tables.extend(sorted(t.name for t in stmt.get_final_froms()))
        except Exception:  # raw text() statements have no resolvable froms
            pass
        return self._results.pop(0)


def _client(results, capture: list | None = None) -> TestClient:
    api = FastAPI()
    api.include_router(read_api.router)
    session = _FakeSession(results)
    if capture is not None:
        capture.append(session)
    api.dependency_overrides[get_session_dep] = lambda: session
    return TestClient(api)


def test_endpoint_data_stats_wiring() -> None:
    rows = [_content(sentiment_score=0.5), _content(kind="news", sentiment_score=-0.3)]
    # 4 DB calls: raw_content scan, reader.series (hourly), then window_stats
    # unions hourly + daily (two fetches).
    client = _client(
        [_Result(rows=rows), _Result(rows=[]), _Result(rows=[]), _Result(rows=[])]
    )
    r = client.get("/data/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total_24h"] == 2
    assert "volume_series" in body and len(body["volume_series"]) == 12


def test_endpoint_data_content_wiring() -> None:
    rows = [_content(id=1), _content(id=2)]
    # first execute → count (scalar_one), second → rows
    client = _client([_Result(scalar=2), _Result(rows=rows)])
    r = client.get("/data/content?category=social&limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert body["items"][0]["source_category"] == "social"


def test_assemble_trace_full_chain() -> None:
    sig = SimpleNamespace(
        symbol="BTC",
        time=NOW,
        opportunity_score=82,
        confidence=0.7,
        escalated=True,
        payload={
            "price_change_pct_24h": 6.0,
            "sentiment_score": 0.4,
            "social_growth": 0.8,
            "volume_spike_ratio": 2.1,
        },
    )
    dec = SimpleNamespace(
        symbol="BTC",
        created_at=NOW,
        direction="long",
        confidence=0.9,
        ai_validated=True,
    )
    trd = SimpleNamespace(
        symbol="BTC",
        created_at=NOW,
        updated_at=NOW,
        status="filled",
        position_size_pct=0.04,
        stop_loss=63000,
        take_profit=74000,
        risk_reward_ratio=2.4,
        fill_price=66800,
        pnl=120,
    )
    t = assemble_trace("corr-1", sig, dec, trd)
    assert t["correlation_id"] == "corr-1"
    assert t["symbol"] == "BTC"
    kinds = {s["kind"]: s for s in t["stages"]}
    assert len(t["stages"]) == 6
    assert kinds["analysis"]["reached"] is True
    assert kinds["decision"]["detail"]["direction"] == "long"
    assert kinds["order"]["reached"] is True  # status filled
    assert kinds["order"]["detail"]["fill_price"] == 66800.0


def test_assemble_trace_partial_chain() -> None:
    sig = SimpleNamespace(
        symbol="ETH",
        time=NOW,
        opportunity_score=60,
        confidence=0.6,
        escalated=False,
        payload={},
    )
    t = assemble_trace("corr-2", sig, None, None)
    kinds = {s["kind"]: s for s in t["stages"]}
    assert kinds["analysis"]["reached"] is True
    assert kinds["decision"]["reached"] is False
    assert kinds["risk"]["reached"] is False
    assert kinds["order"]["reached"] is False


def test_endpoint_trace_wiring() -> None:
    sig = SimpleNamespace(
        symbol="BTC",
        time=NOW,
        opportunity_score=80,
        confidence=0.7,
        escalated=True,
        payload={},
    )
    dec = SimpleNamespace(
        symbol="BTC",
        created_at=NOW,
        direction="long",
        confidence=0.9,
        ai_validated=True,
    )
    trd = SimpleNamespace(
        symbol="BTC",
        created_at=NOW,
        updated_at=NOW,
        status="filled",
        position_size_pct=0.04,
        stop_loss=1,
        take_profit=2,
        risk_reward_ratio=2.0,
        fill_price=100,
        pnl=None,
    )
    client = _client([_Result(rows=[sig]), _Result(rows=[dec]), _Result(rows=[trd])])
    r = client.get("/trace/corr-1")
    assert r.status_code == 200
    body = r.json()
    assert body["correlation_id"] == "corr-1"
    assert len(body["stages"]) == 6


def test_endpoint_trace_picks_the_newest_signal_without_ordering_in_sql() -> None:
    """`ORDER BY time DESC LIMIT 1` is what made the planner ignore
    ix_signals_correlation and walk the whole hypertable (142 s vs 0.35 ms in
    production), so the newest row is chosen in Python. It still has to be the
    newest."""
    older = SimpleNamespace(
        symbol="BTC",
        time=NOW - timedelta(minutes=5),
        opportunity_score=10,
        confidence=0.1,
        escalated=False,
        payload={},
    )
    newer = SimpleNamespace(
        symbol="BTC",
        time=NOW,
        opportunity_score=90,
        confidence=0.9,
        escalated=True,
        payload={},
    )
    client = _client([_Result(rows=[older, newer]), _Result(rows=[]), _Result(rows=[])])
    kinds = {s["kind"]: s for s in client.get("/trace/corr-1").json()["stages"]}
    assert kinds["analysis"]["detail"]["opportunity_score"] == 90


def _archived(event_type, payload, symbol="BTC"):
    return SimpleNamespace(
        event_type=event_type, symbol=symbol, time=NOW, payload=payload
    )


def test_assemble_trace_unresolved_has_no_null_details() -> None:
    """An id behind which nothing exists must not publish six stages of `None`:
    the drawer renders one chip per entry, so those reached the operator as the
    literal text `score: null`."""
    t = assemble_trace("ghost", None, None, None)
    assert t["symbol"] == "?"
    for s in t["stages"]:
        assert s["reached"] is False
        assert s["detail"] == {}


def test_assemble_trace_price_stage_needs_a_measurement() -> None:
    """`"price_change_pct_24h" in payload` was true of every analysis ever
    written -- model_dump() emits the field whether or not it was measured."""
    sig = SimpleNamespace(
        symbol="ETH",
        time=NOW,
        opportunity_score=60,
        confidence=0.6,
        escalated=False,
        payload={"price_change_pct_24h": None, "volume_spike_ratio": None},
    )
    kinds = {s["kind"]: s for s in assemble_trace("c", sig, None, None)["stages"]}
    assert kinds["price"]["reached"] is False
    assert kinds["price"]["detail"] == {}


def test_assemble_trace_from_sentiment_origin() -> None:
    """The sentiment row the operator clicked fills the sentiment stage from its
    own payload, and the analysis it fed is flagged as an inferred link."""
    origin = _archived(
        "SentimentEvent",
        {
            "sentiment_score": 0.42,
            "confidence": 0.8,
            "model_name": "ElKulako/cryptobert",
            "input_kind": "social",
            "social_growth": None,
        },
    )
    sig = SimpleNamespace(
        symbol="BTC",
        time=NOW,
        opportunity_score=71,
        confidence=0.6,
        escalated=False,
        payload={"correlation_id": "corr-analysis"},
    )
    t = assemble_trace("sent-1", sig, None, None, origin=origin)
    kinds = {s["kind"]: s for s in t["stages"]}
    assert t["symbol"] == "BTC"
    assert kinds["sentiment"]["reached"] is True
    assert kinds["sentiment"]["detail"]["score"] == 0.42
    assert kinds["sentiment"]["detail"]["model"] == "ElKulako/cryptobert"
    # social_growth was not measured, so it is absent rather than 0.
    assert "social_growth" not in kinds["sentiment"]["detail"]
    assert kinds["analysis"]["reached"] is True
    assert "proximité" in kinds["analysis"]["summary"]


def test_assemble_trace_from_price_origin() -> None:
    origin = _archived(
        "PriceEvent",
        {"price_usd": "66800.0", "price_change_pct_24h": 6.2, "volume_24h_usd": None},
    )
    kinds = {
        s["kind"]: s
        for s in assemble_trace("px-1", None, None, None, origin=origin)["stages"]
    }
    assert kinds["price"]["reached"] is True
    assert kinds["price"]["detail"] == {"price_usd": 66800.0, "change_24h_pct": 6.2}
    assert kinds["price"]["summary"] == "Tick prix BTC"
    # Nothing downstream was found: the rest stays honestly empty.
    assert kinds["analysis"]["reached"] is False


def test_assemble_trace_keeps_unset_protection_absent() -> None:
    """`_num` answers 0.0 for None, which would publish a stop-loss the risk
    engine never set as `stop_loss: 0` -- protection at zero, not absent."""
    trd = SimpleNamespace(
        symbol="BTC",
        created_at=NOW,
        status="approved",
        position_size_pct=0.04,
        stop_loss=None,
        take_profit=None,
        risk_reward_ratio=None,
        fill_price=None,
        pnl=None,
    )
    kinds = {s["kind"]: s for s in assemble_trace("c", None, None, trd)["stages"]}
    assert kinds["risk"]["detail"] == {"size_pct": 0.04}
    assert kinds["order"]["detail"] == {"status": "approved"}


def test_endpoint_trace_resolves_a_raw_event_through_the_archive() -> None:
    """A sentiment id matches no signal/decision/trade. Measured in production:
    95% of the live feed is in that case, and each one used to render an empty
    drawer."""
    origin = _archived(
        "SentimentEvent", {"sentiment_score": -0.3, "model_name": "finbert"}, "SOL"
    )
    sig = SimpleNamespace(
        symbol="SOL",
        time=NOW,
        opportunity_score=64,
        confidence=0.55,
        escalated=False,
        payload={"correlation_id": "corr-analysis"},
    )
    client = _client(
        [
            _Result(rows=[]),  # signals by cid
            _Result(rows=[]),  # decisions by cid
            _Result(rows=[]),  # trades by cid
            _Result(rows=[origin]),  # events_signal by cid
            _Result(rows=[sig]),  # consuming analysis (symbol, time window)
            _Result(rows=[]),  # decisions by the analysis cid
            _Result(rows=[]),  # trades by the analysis cid
        ]
    )
    body = client.get("/trace/sent-1").json()
    assert body["symbol"] == "SOL"
    kinds = {s["kind"]: s for s in body["stages"]}
    assert kinds["sentiment"]["reached"] is True
    assert kinds["sentiment"]["detail"]["score"] == -0.3
    assert kinds["analysis"]["reached"] is True


def _trade(**kw):
    base = dict(
        event_id="t1",
        symbol="BTC",
        direction="long",
        entry_price=66800.0,
        stop_loss=63000.0,
        take_profit=74000.0,
        confidence=0.9,
        position_size_pct=0.04,
        risk_reward_ratio=2.4,
        status="filled",
        fill_price=66800.0,
        pnl=None,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_map_position_reprices_and_pnl() -> None:
    trade = _trade(entry_price=100.0, position_size_pct=0.10, direction="long")
    price = SimpleNamespace(price_usd=110.0)
    p = map_position(trade, price, base_capital=100000.0)
    assert p["quantity"] == 100.0  # 100000*0.10/100
    assert p["current_price"] == 110.0
    assert p["value_usd"] == 11000.0
    assert p["unrealized_pnl_usd"] == 1000.0  # (110-100)*100
    assert p["protected"] is True


def test_map_position_short_and_unprotected() -> None:
    trade = _trade(
        entry_price=100.0,
        position_size_pct=0.10,
        direction="short",
        stop_loss=0,
        take_profit=0,
    )
    p = map_position(trade, SimpleNamespace(price_usd=90.0), base_capital=100000.0)
    assert p["unrealized_pnl_usd"] == 1000.0  # short gains when price drops
    assert p["protected"] is False
    assert p["stop_loss"] is None


def test_map_portfolio_trade_shape() -> None:
    d = map_portfolio_trade(
        _trade(direction="long", fill_price=66800.0), base_capital=100000.0
    )
    assert d["side"] == "buy"
    assert d["status"] == "filled"
    assert d["order_type"] == "market"


def test_compute_portfolio_aggregates() -> None:
    positions = [
        {
            "value_usd": 11000.0,
            "quantity": 100.0,
            "entry_price": 100.0,
            "unrealized_pnl_usd": 1000.0,
        },
        {
            "value_usd": 5000.0,
            "quantity": 50.0,
            "entry_price": 100.0,
            "unrealized_pnl_usd": 0.0,
        },
    ]
    pf = compute_portfolio(
        positions, realized_24h=250.0, base_capital=100000.0, now=NOW
    )
    assert pf["invested_usd"] == 16000.0
    # cash = base - cost_basis(100*100 + 50*100 = 15000) = 85000
    assert pf["cash_usd"] == 85000.0
    assert pf["total_value_usd"] == 101000.0
    assert pf["unrealized_pnl_usd"] == 1000.0
    assert pf["realized_pnl_24h_usd"] == 250.0


def test_compute_exposure_and_limits_and_alerts() -> None:
    positions = [
        {"symbol": "BTC", "value_usd": 40000.0, "protected": False},
        {"symbol": "ETH", "value_usd": 5000.0, "protected": True},
    ]
    exp = compute_exposure(positions, total=100000.0, daily_loss=0.0, now=NOW)
    assert exp["open_positions"] == 2
    assert exp["protected_positions"] == 1
    btc = next(a for a in exp["by_asset"] if a["symbol"] == "BTC")
    assert btc["exposure_pct"] == 40.0
    limits = compute_risk_limits(exp, cash_pct=55.0)
    single = next(x for x in limits if x["key"] == "max_single_asset")
    assert single["breached"] is True  # 40% > 30%
    alerts = compute_risk_alerts(exp, now=NOW)
    # BTC over-exposed (warning) + BTC unprotected (info) = 2 alerts min
    assert any(a["level"] == "warning" and a["symbol"] == "BTC" for a in alerts)


def test_endpoint_portfolio_wiring() -> None:
    trade = _trade(entry_price=100.0, position_size_pct=0.10)
    price = SimpleNamespace(symbol="BTC", price_usd=110.0)
    # /portfolio: account snapshot, open trades, latest prices, closed(24h).
    # The snapshot query comes first because it decides the reference capital
    # the positions are then sized against; an empty result is the production
    # state today, with no exchange key configured.
    client = _client(
        [
            _Result(rows=[]),
            _Result(rows=[trade]),
            _Result(rows=[price]),
            _Result(rows=[]),
        ]
    )
    r = client.get("/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["invested_usd"] == 11000.0
    assert (
        body["total_value_usd"] == 100000.0 + 1000.0
    )  # base + unrealized (cash+invested)
    # No snapshot: a declared absence, never a number.
    assert body["kraken_balance_usd"] is None
    assert body["balance_source"] == "unavailable"
    assert body["balance_stale"] is False


def test_assemble_systems_snapshot() -> None:
    rows = [
        SimpleNamespace(
            service="api-gateway",
            status="healthy",
            healthy=True,
            latency_ms=12.0,
            detail={},
        ),
        SimpleNamespace(
            service="trading-engine",
            status="degraded",
            healthy=False,
            latency_ms=310.0,
            detail={"cpu_pct": 70, "mem_mb": 200},
        ),
    ]
    snap = assemble_systems_snapshot(rows, now=NOW)
    assert len(snap["services"]) == len(SERVICE_CATALOG)
    svc = {s["id"]: s for s in snap["services"]}
    assert svc["api-gateway"]["status"] == "healthy"
    assert svc["trading-engine"]["status"] == "degraded"
    assert svc["trading-engine"]["cpu_pct"] == 70
    # a service with no health row is idle
    assert svc["risk-engine"]["status"] == "idle"
    assert len(snap["pipeline"]) == 7
    assert snap["kafka"] == [] and snap["workers"] == []
    assert snap["summary"]["services_total"] == len(SERVICE_CATALOG)
    assert snap["summary"]["services_degraded"] == 1


def _health(service, status="healthy", throughput=None):
    detail = {} if throughput is None else {"throughput_per_min": throughput}
    return SimpleNamespace(
        service=service,
        status=status,
        healthy=status == "healthy",
        latency_ms=3.0,
        detail=detail,
    )


def test_unmeasured_service_throughput_is_null_not_zero() -> None:
    """A service whose /metrics has only been scraped once has no rate yet.
    Reporting 0 there is what made the whole graph look dead."""
    snap = read_api.assemble_systems_snapshot([_health("ai-worker-haiku")])
    haiku = next(s for s in snap["services"] if s["id"] == "ai-worker-haiku")
    assert haiku["throughput_per_min"] is None


def test_overview_pipeline_carries_the_stage_counts_it_is_given() -> None:
    counts = {"triage": systems_pipeline.StageCounts(volume=42, dropped=40)}
    snap = read_api.assemble_systems_snapshot(
        [_health("ai-worker-haiku", throughput=9)], counts=counts
    )
    triage = next(s for s in snap["pipeline"] if s["id"] == "triage")
    assert triage["volume"] == 42
    assert triage["dropped"] == 40
    assert triage["throughput_per_min"] == 9


_METRICS = """# HELP process_cpu_seconds_total Total user and system CPU time
# TYPE process_cpu_seconds_total counter
process_cpu_seconds_total 10.0
# TYPE process_resident_memory_bytes gauge
process_resident_memory_bytes 2.097152e+08
# TYPE cmi_events_consumed_total counter
cmi_events_consumed_total{service="api-gateway",topic="analysis.events"} 100.0
cmi_events_consumed_total{service="api-gateway",topic="decision.events"} 50.0
cmi_events_produced_total{service="api-gateway"} 20.0
"""


def test_parse_prometheus_and_sum() -> None:
    p = parse_prometheus(_METRICS)
    assert metric_sum(p, "process_resident_memory_bytes") == 2.097152e08
    assert metric_sum(p, "cmi_events_consumed_total") == 150.0  # 100 + 50
    assert p["cmi_events_consumed_total"][0][0]["topic"] == "analysis.events"


def test_compute_detail_rates() -> None:
    p = parse_prometheus(_METRICS)
    # first sample: only mem (no prev → no rates)
    detail1, sample1 = compute_detail(p, None, now_ts=1000.0)
    assert detail1["mem_mb"] == 210  # 2.097152e8 / 1e6 ≈ 209.7 → 210
    assert "cpu_pct" not in detail1
    # second sample 10s later, cpu +5s, events +170 → cpu 50%, throughput 1020/min
    text2 = _METRICS.replace(
        "process_cpu_seconds_total 10.0", "process_cpu_seconds_total 15.0"
    ).replace(
        'cmi_events_produced_total{service="api-gateway"} 20.0',
        'cmi_events_produced_total{service="api-gateway"} 190.0',
    )
    detail2, _ = compute_detail(parse_prometheus(text2), sample1, now_ts=1010.0)
    assert detail2["cpu_pct"] == 50.0  # 5s / 10s * 100
    assert detail2["throughput_per_min"] == 1020  # (320-150... ) rate check


def test_build_collectors_from_rawcontent() -> None:
    rows = [("Reddit", "social", 120), ("CoinDesk", "news", 30), ("Dead", "social", 0)]
    cols = build_collectors(rows)
    assert cols[0]["platform"] == "Reddit"  # sorted by items desc
    assert cols[0]["items_last_hour"] == 120
    assert cols[0]["category"] == "social"
    assert cols[-1]["status"] == "idle"  # zero items


def test_build_workers_scales_tokens_and_cost() -> None:
    w = build_workers(haiku_reqs=1000, sonnet_reqs=100)
    haiku = next(x for x in w if x["tier"] == "triage")
    assert haiku["requests_last_hour"] == 1000
    assert haiku["tokens_in"] == 820000
    assert haiku["status"] == "healthy"
    sonnet = next(x for x in w if x["tier"] == "senior")
    assert sonnet["cost_usd_today"] == round(100 * 0.012, 2)


def test_build_kafka_rates_and_orphans() -> None:
    k = build_kafka({"price.events": 600, "analysis.events": 0})
    price = next(t for t in k if t["name"] == "price.events")
    assert price["msg_per_min"] == 10.0  # 600/60
    analysis = next(t for t in k if t["name"] == "analysis.events")
    assert analysis["orphaned"] is True  # zero count


def test_build_infra_postgres() -> None:
    infra = build_infra(pg_connections=12, pg_size_bytes=42_000_000_000)
    assert infra[0]["id"] == "postgres"
    vals = {m["label"]: m["value"] for m in infra[0]["metrics"]}
    assert vals["Connexions"] == "12"
    assert vals["Taille"] == "42.00 GB"


def test_endpoint_systems_overview_wiring() -> None:
    # Global across the whole test session (module-level in systems_pipeline);
    # a cached "24h" entry left over from another test would short-circuit
    # stage_counts_cached and desync every query below it.
    health = [
        SimpleNamespace(
            service="api-gateway",
            status="healthy",
            healthy=True,
            latency_ms=10.0,
            detail={},
        )
    ]
    # fetch_stage_counts issues 2 counts + 1 "latest row" query per of the 7
    # stages (collect, sentiment, triage, senior, decision, risk, execute).
    # collect's two counts are distinct and non-zero so the assertions below can
    # tell "counts were threaded through" from "counts were silently dropped":
    # build_pipeline_stages always returns 7 stages either way, so a length
    # check alone proves nothing about the wiring.
    stage_queries = [_Result(scalar=3), _Result(scalar=5), _Result(rows=[])]
    stage_queries += [_Result(scalar=0), _Result(scalar=0), _Result(rows=[])] * 6
    # execute order: health, [stage counts], coll_rows, workers(Signal,Decision),
    # kafka(Price,Sentiment,Signal,Decision,Trade), pg_stat_activity, pg_database_size
    results = [
        _Result(rows=health),
        *stage_queries,
        _Result(rows=[("Reddit", "social", 50)]),
        _Result(scalar=100),
        _Result(scalar=20),
        _Result(scalar=600),
        _Result(scalar=60),
        _Result(scalar=100),
        _Result(scalar=20),
        _Result(scalar=5),
        _Result(scalar=12),
        _Result(scalar=42_000_000_000),
    ]
    client = _client(results)
    r = client.get("/systems/overview")
    assert r.status_code == 200
    body = r.json()
    assert len(body["pipeline"]) == 7
    assert body["pipeline_window"] == "24h"
    assert body["pipeline_stale"] is False
    collect = next(s for s in body["pipeline"] if s["id"] == "collect")
    assert collect["volume"] == 8  # 3 prices + 5 content rows, threaded through
    senior = next(s for s in body["pipeline"] if s["id"] == "senior")
    assert senior["volume"] == 0  # a measured zero, not a dropped value (None)
    assert body["collectors"][0]["platform"] == "Reddit"
    assert len(body["workers"]) == 2
    assert body["infra"][0]["id"] == "postgres"
    assert body["summary"]["ai_cost_today_usd"] > 0


def test_endpoint_systems_stage_404_for_unknown_stage() -> None:
    """An unknown stage id is a 404, not an empty drawer — an empty item list
    would read as "this stage processed nothing", a different statement."""
    client = _client([])
    r = client.get("/systems/stage/bogus")
    assert r.status_code == 404


def test_endpoint_systems_stage_decision_wiring() -> None:
    # fetch_stage_detail first runs the same 21-query fan-out as
    # /systems/overview (2 counts + 1 latest-row per of the 7 stages, in
    # collect/sentiment/triage/senior/decision/risk/execute order), then the
    # stage's own builder: decision's is one row query + one rejection-reason
    # group-by. decision's own counts (29, 31) are distinct from every other
    # placeholder below so a builder reading the wrong stage's aggregate would
    # surface as a wrong `volume`/`dropped` in the assertions.
    stage_count_queries = (
        [_Result(scalar=1), _Result(scalar=2), _Result(rows=[])]  # collect
        + [_Result(scalar=3), _Result(scalar=4), _Result(rows=[])]  # sentiment
        + [_Result(scalar=5), _Result(scalar=6), _Result(rows=[])]  # triage
        + [_Result(scalar=7), _Result(scalar=8), _Result(rows=[])]  # senior
        + [_Result(scalar=29), _Result(scalar=31), _Result(rows=[])]  # decision
        + [_Result(scalar=9), _Result(scalar=10), _Result(rows=[])]  # risk
        + [_Result(scalar=11), _Result(scalar=12), _Result(rows=[])]  # execute
    )
    decision_row = SimpleNamespace(
        created_at=NOW,
        symbol="ETH",
        direction="short",
        opportunity_score=55,
        confidence=0.66,
        ai_validated=True,
        correlation_id="corr-77",
    )
    rejection_rows = [("score 12 too low", 3), ("score 45 too low", 2)]
    sessions: list = []
    client = _client(
        stage_count_queries
        + [_Result(rows=[decision_row]), _Result(rows=rejection_rows)],
        capture=sessions,
    )
    r = client.get("/systems/stage/decision")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "decision"
    assert body["volume"] == 29
    assert body["dropped"] == 31
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["summary"] == "ETH · short · confiance 66%"
    assert item["correlation_id"] == "corr-77"
    assert item["detail"] == {"direction": "short", "score": 55, "ai_validated": True}
    # "score 12 too low" and "score 45 too low" collapse into one bucket.
    assert body["breakdown"] == [{"key": "score N too low", "count": 5}]
    # Where the drawer read from, not just what it returned. Values alone cannot
    # catch a builder pointed at the wrong model: the fake replays results
    # positionally, so a swap still receives a plausible row.
    assert sessions[0].tables[-2:] == ["decisions", "pipeline_rejections"]


def test_endpoint_market_news_wiring() -> None:
    row = SimpleNamespace(
        id=1,
        title="x",
        url="u",
        source="RSS",
        symbols=[],
        sentiment_score=0.0,
        published_at=NOW,
    )
    client = _client([_Result(rows=[row])])
    r = client.get("/market/news?limit=5")
    assert r.status_code == 200
    assert r.json()[0]["source"] == "RSS"


def _price_row(symbol="BTC", change=1.0):
    return SimpleNamespace(
        symbol=symbol,
        price_usd=100.0,
        market_cap_usd=1000.0,
        volume_24h_usd=500.0,
        price_change_pct_24h=change,
        time=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
    )


def test_map_token_uses_measured_liquidity_when_present():
    out = map_token(_price_row(), liquidity_usd=250_000.0)
    assert out["liquidity_usd"] == 250_000.0


def test_map_token_reports_zero_liquidity_when_unmeasured():
    """The TS contract requires the key; None must not leak to the wire."""
    out = map_token(_price_row(), liquidity_usd=None)
    assert out["liquidity_usd"] == 0.0


def test_map_token_carries_sentiment_through():
    out = map_token(_price_row(), sentiment_score=0.383)
    assert out["sentiment_score"] == 0.38


def test_map_token_prefers_the_real_trending_flag_over_the_heuristic():
    meta = SimpleNamespace(
        coin_id="bitcoin", name="Bitcoin", metadata_={"is_trending": True}
    )
    out = map_token(_price_row(change=0.1), meta=meta)
    assert out["name"] == "Bitcoin"
    assert out["coin_id"] == "bitcoin"
    assert out["is_trending"] is True


def test_map_token_falls_back_to_the_change_heuristic_without_metadata():
    assert map_token(_price_row(change=7.0))["is_trending"] is True
    assert map_token(_price_row(change=1.0))["is_trending"] is False
