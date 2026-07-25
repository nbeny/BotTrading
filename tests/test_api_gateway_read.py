"""Unit tests for the api-gateway live-mode read API.

The DB queries themselves need Postgres, but the row→response mappers and the
stats aggregation are pure functions, and the routing/response shapes are
verified with a fake session via FastAPI's dependency override.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

# The service package is named `app`; add its dir to the path.
_SVC = Path(__file__).resolve().parents[1] / "services" / "api-gateway"
if str(_SVC) not in sys.path:
    sys.path.insert(0, str(_SVC))

from app import read_api  # noqa: E402
from app.read_api import (  # noqa: E402
    SERVICE_CATALOG,
    assemble_systems_snapshot,
    assemble_trace,
    compute_content_stats,
    compute_exposure,
    compute_portfolio,
    compute_risk_alerts,
    compute_risk_limits,
    map_content,
    map_decision,
    map_news,
    map_position,
    map_portfolio_trade,
    map_price_point,
    map_signal_event,
    map_token,
)
from app.routers import get_session_dep  # noqa: E402

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


# ── pure mappers ──────────────────────────────────────────────────────────────
def test_map_token_enriched() -> None:
    price = SimpleNamespace(
        symbol="BTC", price_usd=66800, price_change_pct_24h=6.2,
        volume_24h_usd=1_000_000, market_cap_usd=1_300_000_000_000, time=NOW,
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
        symbol="SOL", price_usd=142, price_change_pct_24h=1.1,
        volume_24h_usd=None, market_cap_usd=None, time=NOW,
    )
    d = map_token(price)
    assert d["coin_id"] == "sol"
    assert d["name"] == "SOL"
    assert d["volume_24h_usd"] == 0.0
    assert d["is_trending"] is False


def test_map_price_point() -> None:
    row = SimpleNamespace(time=NOW, price_usd=100.5, volume_24h_usd=2000)
    assert map_price_point(row) == {"t": NOW.isoformat(), "price": 100.5, "volume": 2000.0}


def test_map_news_epoch_conversion() -> None:
    epoch = int(NOW.timestamp())
    row = SimpleNamespace(
        id=7, title="ETF approved", url="http://x", source_name="CoinDesk",
        symbols=["BTC"], provider_sentiment=0.3, published_at=epoch,
    )
    d = map_news(row)
    assert d["id"] == "7"
    assert d["source"] == "CoinDesk"
    assert d["symbols"] == ["BTC"]
    assert d["published_at"].startswith("2026-07-25")


def test_map_signal_event_shape() -> None:
    row = SimpleNamespace(
        symbol="ETH", opportunity_score=82, confidence=0.7,
        reason="momentum", escalated=True, time=NOW,
    )
    d = map_signal_event(row)
    assert d["event_type"] == "AnalysisEvent"
    assert d["opportunity_score"] == 82
    assert d["escalate"] is True


def test_map_decision_shape() -> None:
    row = SimpleNamespace(
        event_id="evt1", symbol="BTC", direction="long", opportunity_score=87,
        confidence=0.9, rationale="strong", created_at=NOW,
    )
    d = map_decision(row)
    assert d["id"] == "evt1"
    assert d["worker"] == "sonnet"
    assert d["decision"] == "LONG_SIGNAL"
    assert d["opportunity_score"] == 0.87


def _content(**kw):
    base = dict(
        id=1, source="Reddit", kind="social", url="http://x", title="t", text="body $BTC",
        symbols=["BTC"], published_at=NOW, fetched_at=NOW, sentiment_score=0.7,
        sentiment_confidence=0.8, sentiment_model="cryptobert",
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
        _content(kind="social", source="Reddit", symbols=["BTC"], sentiment_score=0.5,
                 published_at=NOW - timedelta(hours=1)),
        _content(kind="news", source="CoinDesk", symbols=["ETH", "BTC"], sentiment_score=-0.2,
                 published_at=NOW - timedelta(hours=1)),
        _content(kind="social", source="Reddit", symbols=["BTC"], sentiment_score=0.1,
                 published_at=NOW - timedelta(hours=3)),
    ]
    s = compute_content_stats(rows, now=NOW)
    assert s["total_24h"] == 3
    assert s["social_24h"] == 2
    assert s["news_24h"] == 1
    assert s["avg_sentiment"] == round((0.5 - 0.2 + 0.1) / 3, 2)
    assert len(s["volume_series"]) == 12
    assert len(s["sentiment_series"]) == 12
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
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        return self._results.pop(0)


def _client(results) -> TestClient:
    api = FastAPI()
    api.include_router(read_api.router)
    api.dependency_overrides[get_session_dep] = lambda: _FakeSession(results)
    return TestClient(api)


def test_endpoint_data_stats_wiring() -> None:
    rows = [_content(sentiment_score=0.5), _content(kind="news", sentiment_score=-0.3)]
    client = _client([_Result(rows=rows)])
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
        symbol="BTC", time=NOW, opportunity_score=82, confidence=0.7, escalated=True,
        payload={"price_change_pct_24h": 6.0, "sentiment_score": 0.4, "social_growth": 0.8,
                 "volume_spike_ratio": 2.1},
    )
    dec = SimpleNamespace(symbol="BTC", created_at=NOW, direction="long", confidence=0.9, ai_validated=True)
    trd = SimpleNamespace(
        symbol="BTC", created_at=NOW, updated_at=NOW, status="filled", position_size_pct=0.04,
        stop_loss=63000, take_profit=74000, risk_reward_ratio=2.4, fill_price=66800, pnl=120,
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
    sig = SimpleNamespace(symbol="ETH", time=NOW, opportunity_score=60, confidence=0.6, escalated=False, payload={})
    t = assemble_trace("corr-2", sig, None, None)
    kinds = {s["kind"]: s for s in t["stages"]}
    assert kinds["analysis"]["reached"] is True
    assert kinds["decision"]["reached"] is False
    assert kinds["risk"]["reached"] is False
    assert kinds["order"]["reached"] is False


def test_endpoint_trace_wiring() -> None:
    sig = SimpleNamespace(symbol="BTC", time=NOW, opportunity_score=80, confidence=0.7, escalated=True, payload={})
    dec = SimpleNamespace(symbol="BTC", created_at=NOW, direction="long", confidence=0.9, ai_validated=True)
    trd = SimpleNamespace(symbol="BTC", created_at=NOW, updated_at=NOW, status="filled", position_size_pct=0.04,
                          stop_loss=1, take_profit=2, risk_reward_ratio=2.0, fill_price=100, pnl=None)
    client = _client([_Result(rows=[sig]), _Result(rows=[dec]), _Result(rows=[trd])])
    r = client.get("/trace/corr-1")
    assert r.status_code == 200
    body = r.json()
    assert body["correlation_id"] == "corr-1"
    assert len(body["stages"]) == 6


def _trade(**kw):
    base = dict(
        event_id="t1", symbol="BTC", direction="long", entry_price=66800.0, stop_loss=63000.0,
        take_profit=74000.0, confidence=0.9, position_size_pct=0.04, risk_reward_ratio=2.4,
        status="filled", fill_price=66800.0, pnl=None, created_at=NOW, updated_at=NOW,
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
    trade = _trade(entry_price=100.0, position_size_pct=0.10, direction="short", stop_loss=0, take_profit=0)
    p = map_position(trade, SimpleNamespace(price_usd=90.0), base_capital=100000.0)
    assert p["unrealized_pnl_usd"] == 1000.0  # short gains when price drops
    assert p["protected"] is False
    assert p["stop_loss"] is None


def test_map_portfolio_trade_shape() -> None:
    d = map_portfolio_trade(_trade(direction="long", fill_price=66800.0), base_capital=100000.0)
    assert d["side"] == "buy"
    assert d["status"] == "filled"
    assert d["order_type"] == "market"


def test_compute_portfolio_aggregates() -> None:
    positions = [
        {"value_usd": 11000.0, "quantity": 100.0, "entry_price": 100.0, "unrealized_pnl_usd": 1000.0},
        {"value_usd": 5000.0, "quantity": 50.0, "entry_price": 100.0, "unrealized_pnl_usd": 0.0},
    ]
    pf = compute_portfolio(positions, realized_24h=250.0, base_capital=100000.0, now=NOW)
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
    # /portfolio: open trades, latest prices, closed(24h)
    client = _client([_Result(rows=[trade]), _Result(rows=[price]), _Result(rows=[])])
    r = client.get("/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["invested_usd"] == 11000.0
    assert body["total_value_usd"] == 100000.0 + 1000.0  # base + unrealized (cash+invested)


def test_assemble_systems_snapshot() -> None:
    rows = [
        SimpleNamespace(service="api-gateway", status="healthy", healthy=True, latency_ms=12.0, detail={}),
        SimpleNamespace(service="trading-engine", status="degraded", healthy=False, latency_ms=310.0,
                        detail={"cpu_pct": 70, "mem_mb": 200}),
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


def test_endpoint_systems_overview_wiring() -> None:
    rows = [SimpleNamespace(service="api-gateway", status="healthy", healthy=True, latency_ms=10.0, detail={})]
    client = _client([_Result(rows=rows)])
    r = client.get("/systems/overview")
    assert r.status_code == 200
    body = r.json()
    assert "services" in body and "pipeline" in body and "summary" in body
    assert len(body["pipeline"]) == 7


def test_endpoint_market_news_wiring() -> None:
    epoch = int(NOW.timestamp())
    row = SimpleNamespace(
        id=1, title="x", url="u", source_name="RSS", symbols=[], provider_sentiment=0.0,
        published_at=epoch,
    )
    client = _client([_Result(rows=[row])])
    r = client.get("/market/news?limit=5")
    assert r.status_code == 200
    assert r.json()[0]["source"] == "RSS"
