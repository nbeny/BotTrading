"""ContentNormalizer: symbol resolution, relevance gate, MARKET fallback."""

from __future__ import annotations

from cmi_common.sources import ContentNormalizer, RawItem, SymbolLexicon

COINS = [
    {"ticker": "BTC", "name": "Bitcoin"},
    {"ticker": "ETH", "name": "Ethereum"},
    {"ticker": "HYPE", "name": "Hyperliquid"},
    {"ticker": "ONE", "name": "Harmony"},
    {"ticker": "KEEP", "name": "Keep Network"},
    {"ticker": "USDT", "name": "Tether"},
]
LEX = SymbolLexicon.from_coins(COINS)


def _item(title: str = "", text: str = "", **kw: object) -> RawItem:
    return RawItem(
        source=str(kw.pop("source", "stub")),
        kind="news",
        external_id=str(kw.pop("external_id", "1")),
        title=title,
        text=text,
        symbols=list(kw.pop("symbols", []) or []),
    )


def _norm() -> ContentNormalizer:
    return ContentNormalizer(LEX)


def test_unambiguous_ticker_in_text_is_accepted() -> None:
    result = _norm().apply([_item(title="BTC breaks out")])
    assert result.kept[0].symbols == ["BTC"]


def test_coin_name_in_text_resolves_to_its_ticker() -> None:
    result = _norm().apply([_item(title="Ethereum upgrade ships")])
    assert result.kept[0].symbols == ["ETH"]


def test_ambiguous_ticker_without_corroboration_is_rejected() -> None:
    # "ONE" the English word must not become the Harmony token.
    result = _norm().apply([_item(title="ONE more bitcoin rally")])
    assert result.kept[0].symbols == ["BTC"]


def test_ambiguous_ticker_with_cashtag_is_accepted() -> None:
    result = _norm().apply([_item(title="$ONE is pumping")])
    assert result.kept[0].symbols == ["ONE"]


def test_ambiguous_ticker_with_full_name_is_accepted() -> None:
    result = _norm().apply([_item(title="Harmony ONE network update")])
    assert result.kept[0].symbols == ["ONE"]


def test_out_of_universe_token_needs_a_cashtag() -> None:
    result = _norm().apply([_item(title="WIF season", text="crypto is back")])
    assert result.kept[0].symbols == ["MARKET"]
    result2 = _norm().apply([_item(title="$WIF season")])
    assert result2.kept[0].symbols == ["WIF"]


def test_provider_supplied_symbols_are_discarded() -> None:
    # The exact production failure: the upstream API tagged a Bitcoin article
    # with coins that appear nowhere in it. Only BTC, from the word "Bitcoin".
    item = _item(
        title="Bitcoin Slides Under $64K Amid Rising Treasury Yields",
        symbols=["ETH", "BTC", "ONE", "USDT", "REKT", "BAND", "USDC"],
    )
    assert _norm().apply([item]).kept[0].symbols == ["BTC"]


def test_non_crypto_content_is_dropped() -> None:
    result = _norm().apply([_item(title="Local football club wins final")])
    assert result.kept == []
    assert result.dropped[0][1] == "not_relevant"


def test_crypto_content_without_symbol_becomes_market() -> None:
    item = _item(title="SEC approves a new regulatory framework for exchanges")
    assert _norm().apply([item]).kept[0].symbols == ["MARKET"]


def test_empty_text_is_dropped_with_its_own_reason() -> None:
    result = _norm().apply([_item()])
    assert result.kept == []
    assert result.dropped[0][1] == "empty_text"


def test_symbols_are_sorted_and_deduplicated() -> None:
    item = _item(title="Ethereum and Bitcoin and BTC again")
    assert _norm().apply([item]).kept[0].symbols == ["BTC", "ETH"]


def test_original_item_is_not_mutated() -> None:
    item = _item(title="Bitcoin rallies", symbols=["ONE"])
    _norm().apply([item])
    assert item.symbols == ["ONE"]


def test_multiword_coin_name_resolves_without_a_bare_ticker_mention() -> None:
    # "KEEP" is ambiguous (an English word too), so only the full coin name
    # ("Keep Network"), not the bare word "Keep", may confirm it.
    result = _norm().apply([_item(title="Keep Network announces new staking rewards")])
    assert result.kept[0].symbols == ["KEEP"]


def test_all_caps_headline_only_matches_universe_tickers() -> None:
    # An all-caps headline turns ordinary words into upper-case tokens too.
    # "ALL" and "TIME" are ambiguous and unconfirmed, so they must not ride along
    # just because the headline is shouted. HYPE is confirmed by "Hyperliquid".
    result = _norm().apply(
        [_item(title="BREAKING: HYPERLIQUID HYPE HITS AN ALL TIME HIGH")]
    )
    assert result.kept[0].symbols == ["HYPE"]


def test_a_shouted_non_crypto_headline_yields_no_symbol() -> None:
    # "HYPE" is an ordinary English word before it is a ticker. Uncorroborated,
    # it must not manufacture a symbol -- this exact headline shape produced a
    # bogus HYPE attribution before the word was added to COMMON_WORDS.
    result = _norm().apply([_item(title="ALL THE HYPE IS ABOUT NOTHING")])
    assert result.kept == []
    assert result.dropped[0][1] == "not_relevant"
