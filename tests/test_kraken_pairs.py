"""Pure tests for Kraken AssetPairs parsing and ticker normalization."""

from __future__ import annotations

from decimal import Decimal

from service_modules import load_service_module

pairs = load_service_module("collector-kraken", "domain.pairs")
normalize_base = pairs.normalize_base
parse_asset_pairs = pairs.parse_asset_pairs


def _payload(**entries):
    return {"error": [], "result": entries}


def test_normalize_base_maps_kraken_legacy_tickers():
    assert normalize_base("XBT") == "BTC"
    assert normalize_base("XDG") == "DOGE"


def test_normalize_base_upcases_and_passes_through():
    assert normalize_base("sol") == "SOL"


def test_parse_asset_pairs_keeps_online_usd_pairs():
    payload = _payload(
        XXBTZUSD={
            "wsname": "XBT/USD",
            "status": "online",
            "ordermin": "0.0001",
        },
    )
    specs = parse_asset_pairs(payload)
    assert len(specs) == 1
    assert specs[0].symbol == "BTC"
    assert specs[0].pair == "XXBTZUSD"
    assert specs[0].ordermin == Decimal("0.0001")


def test_parse_asset_pairs_drops_non_usd_quotes():
    payload = _payload(
        XXBTZEUR={"wsname": "XBT/EUR", "status": "online", "ordermin": "0.0001"},
    )
    assert parse_asset_pairs(payload) == []


def test_parse_asset_pairs_drops_offline_pairs():
    payload = _payload(
        SOLUSD={"wsname": "SOL/USD", "status": "cancel_only", "ordermin": "0.1"},
    )
    assert parse_asset_pairs(payload) == []


def test_parse_asset_pairs_skips_entries_without_wsname():
    """Kraken ships a handful of legacy entries with no wsname; skip, never crash."""
    payload = _payload(WEIRD={"status": "online", "ordermin": "1"})
    assert parse_asset_pairs(payload) == []


def test_parse_asset_pairs_defaults_missing_ordermin_to_none():
    payload = _payload(SOLUSD={"wsname": "SOL/USD", "status": "online"})
    assert parse_asset_pairs(payload)[0].ordermin is None
