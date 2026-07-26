"""Read-endpoint → required response-key contract.

Single source of truth shared by the offline parity test
(tests/test_read_contract.py) and the live smoke harness
(scripts/verify_read_live.py). Each value is the set of REQUIRED top-level keys
of the endpoint's response (for list endpoints, the required keys of one item),
derived from the frontend interfaces in frontend/src/lib/types/*.ts. Optional TS
fields (declared ``field?:``) are intentionally excluded.

``market/signals`` is omitted on purpose: it returns a heterogeneous union of raw
event dicts with no stable shared key set, so it is smoke-checked (reachable +
list) but not key-asserted.
"""

from __future__ import annotations

CONTRACT: dict[str, set[str]] = {
    # domain.ts
    "portfolio": {
        "total_value_usd", "cash_usd", "kraken_balance_usd", "invested_usd",
        "unrealized_pnl_usd", "unrealized_pnl_pct", "realized_pnl_24h_usd",
        "pnl_24h_pct", "updated_at",
    },
    "portfolio/positions": {
        "position_id", "symbol", "direction", "quantity", "entry_price",
        "current_price", "value_usd", "unrealized_pnl_usd", "unrealized_pnl_pct",
        "stop_loss", "take_profit", "protected", "opened_at", "mode",
    },
    "portfolio/trades": {
        "trade_id", "symbol", "side", "order_type", "price", "quantity",
        "cost_usd", "fee_usd", "pnl_usd", "status", "mode", "executed_at",
    },
    "portfolio/history": {"t", "price"},
    "market/tokens": {
        "symbol", "coin_id", "name", "price_usd", "price_change_pct_24h",
        "volume_24h_usd", "liquidity_usd", "market_cap_usd", "sentiment_score",
        "opportunity_score", "is_trending", "updated_at",
    },
    "market/token": {
        "symbol", "coin_id", "name", "price_usd", "price_change_pct_24h",
        "volume_24h_usd", "liquidity_usd", "market_cap_usd", "sentiment_score",
        "opportunity_score", "is_trending", "updated_at",
    },
    "market/prices": {"t", "price"},
    "market/news": {"id", "title", "url", "source", "symbols", "sentiment",
                    "published_at"},
    "market/decisions": {
        "id", "symbol", "worker", "decision", "opportunity_score", "confidence",
        "justification", "escalated", "created_at",
    },
    "risk/exposure": {
        "total_exposure_usd", "total_exposure_pct", "max_exposure_pct",
        "by_asset", "protected_positions", "open_positions", "daily_loss_usd",
        "daily_loss_limit_usd", "updated_at",
    },
    "risk/limits": {"key", "label", "value", "max", "unit", "breached"},
    "risk/alerts": {"id", "level", "message", "created_at"},
    # content.ts
    "data/content": {"items", "total", "offset", "limit"},
    "data/stats": {
        "total_24h", "social_24h", "news_24h", "market_24h", "avg_sentiment",
        "volume_series", "sentiment_series", "top_sources", "mentions",
        "updated_at",
    },
    "trace": {"correlation_id", "symbol", "stages"},
    # systems.ts
    "systems/overview": {
        "summary", "services", "pipeline", "kafka", "collectors", "workers",
        "infra",
    },
    "systems/funnel": {
        "window", "stages", "score_histogram", "factors_presence",
        "top_block_reasons", "updated_at",
    },
}
