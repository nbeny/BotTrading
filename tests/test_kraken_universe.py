"""Universe intersection and the majors split — the pure half."""

from __future__ import annotations

from decimal import Decimal

from service_modules import load_service_module

pairs = load_service_module("collector-kraken", "domain.pairs")
universe = load_service_module("collector-kraken", "application.universe")
VenuePairSpec = pairs.VenuePairSpec
intersect = universe.intersect
split_regimes = universe.split_regimes


def _spec(symbol):
    return VenuePairSpec(symbol=symbol, pair=f"{symbol}USD", ordermin=Decimal("1"))


def test_intersect_keeps_only_symbols_priced_in_both_places():
    specs = [_spec("BTC"), _spec("ETH"), _spec("XCP")]
    assert [s.symbol for s in intersect(specs, {"BTC", "ETH", "DEXE"})] == [
        "BTC",
        "ETH",
    ]


def test_intersect_marks_nothing_when_prices_are_empty():
    assert intersect([_spec("BTC")], set()) == []


def test_split_regimes_promotes_symbols_above_the_mention_floor():
    majors, alts = split_regimes(
        [_spec("BTC"), _spec("ETH"), _spec("DEXE")],
        mentions={"BTC": 421, "ETH": 163, "DEXE": 0},
        min_mentions=10,
    )
    assert [s.symbol for s in majors] == ["BTC", "ETH"]
    assert [s.symbol for s in alts] == ["DEXE"]


def test_split_regimes_treats_an_unknown_symbol_as_zero_mentions():
    majors, alts = split_regimes([_spec("XYZ")], mentions={}, min_mentions=10)
    assert majors == []
    assert [s.symbol for s in alts] == ["XYZ"]


def test_split_regimes_is_inclusive_at_the_floor():
    majors, _ = split_regimes([_spec("LINK")], mentions={"LINK": 10}, min_mentions=10)
    assert [s.symbol for s in majors] == ["LINK"]


def test_ambiguous_symbols_flags_tickers_claimed_by_several_coins():
    """CoinGecko tickers are not unique; attaching a real Kraken pair's candles
    to a worthless homonym is a silent correctness bug."""
    rows = [("SOL", "solana", 5), ("SOL", "solana-ai", 4100), ("BTC", "bitcoin", 1)]
    assert universe.ambiguous_symbols(rows) == {"SOL"}


def test_ambiguous_symbols_is_empty_when_every_ticker_is_unique():
    assert universe.ambiguous_symbols([("BTC", "bitcoin", 1)]) == set()


def test_untradable_returns_priced_symbols_kraken_does_not_list():
    assert universe.untradable([_spec("BTC")], {"BTC", "DEXE"}) == {"DEXE"}
