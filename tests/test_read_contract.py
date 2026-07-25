"""Offline contract-parity: every read endpoint's response satisfies CONTRACT.

No DB — pure mappers/compute functions and a _FakeSession drive each endpoint,
mirroring tests/test_api_gateway_read.py. Guards against backend↔frontend shape
drift: a renamed/removed response field breaks this test in CI, not the browser.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

_SVC = Path(__file__).resolve().parents[1] / "services" / "api-gateway"
if str(_SVC) not in sys.path:
    sys.path.insert(0, str(_SVC))

from app import read_api  # noqa: E402
from app.read_api import (  # noqa: E402
    compute_exposure,
    compute_portfolio,
    compute_risk_alerts,
    compute_risk_limits,
    map_decision,
    map_news,
    map_portfolio_trade,
    map_position,
    map_price_point,
    map_token,
)
from app.read_contract import CONTRACT  # noqa: E402

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


# ── fake rows ─────────────────────────────────────────────────────────────────
def _price(**kw):
    base = dict(symbol="BTC", price_usd=110.0, price_change_pct_24h=6.0,
                volume_24h_usd=1000.0, market_cap_usd=1e9, time=NOW)
    base.update(kw)
    return SimpleNamespace(**base)


def _trade(**kw):
    base = dict(event_id="t1", symbol="BTC", direction="long", entry_price=100.0,
                position_size_pct=0.1, stop_loss=90.0, take_profit=120.0,
                fill_price=101.0, status="filled", pnl=5.0, created_at=NOW)
    base.update(kw)
    return SimpleNamespace(**base)


def _news(**kw):
    base = dict(id=1, title="t", url="http://x", source_name="CoinDesk",
                symbols=["BTC"], provider_sentiment=0.3,
                published_at=int(NOW.timestamp()))
    base.update(kw)
    return SimpleNamespace(**base)


def _decision(**kw):
    base = dict(event_id="d1", symbol="BTC", direction="long",
                opportunity_score=80, confidence=0.9, rationale="x",
                created_at=NOW)
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

    async def execute(self, _stmt):
        if self._n <= 0:
            return _Result()
        self._n -= 1
        return _Result()


# ── assertions ────────────────────────────────────────────────────────────────
def _assert_keys(name: str, obj: dict) -> None:
    missing = CONTRACT[name] - set(obj)
    assert not missing, f"{name} missing keys: {sorted(missing)}"


# ── item-shape endpoints (pure mappers) ───────────────────────────────────────
def test_market_tokens_contract() -> None:
    item = map_token(_price(), meta=SimpleNamespace(coin_id="bitcoin", name="BTC"),
                     opportunity_score=80, sentiment_score=0.4)
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
        100.0, 0.0, now=NOW,
    )
    _assert_keys("risk/alerts", compute_risk_alerts(exp, now=NOW)[0])


# ── endpoints needing a session (empty stubs; objects stay fully shaped) ───────
async def test_data_stats_contract() -> None:
    resp = await read_api.data_stats(session=_FakeSession(8))
    _assert_keys("data/stats", resp)


async def test_data_content_contract() -> None:
    resp = await read_api.data_content(
        category="all", symbol=None, q=None, sentiment="all",
        limit=50, offset=0, session=_FakeSession(8),
    )
    _assert_keys("data/content", resp)


async def test_systems_overview_contract() -> None:
    resp = await read_api.systems_overview(session=_FakeSession(40))
    _assert_keys("systems/overview", resp)


async def test_trace_contract() -> None:
    resp = await read_api.trace(cid="corr-x", session=_FakeSession(8))
    _assert_keys("trace", resp)
