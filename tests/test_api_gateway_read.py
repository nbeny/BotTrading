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
    compute_content_stats,
    map_content,
    map_decision,
    map_news,
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
