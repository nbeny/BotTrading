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
    result2 = _norm().apply([_item(title="$WIF season", text="crypto is back")])
    assert result2.kept[0].symbols == ["WIF"]


def test_an_out_of_universe_cashtag_alone_is_not_enough() -> None:
    # This is how a brand-new token gets in, so the channel has to stay open --
    # but a bare $-prefixed word is not evidence of anything crypto. Without it,
    # "$TSLA and $NVDA are leading the rally" booked two equities as crypto
    # symbols, and StockTwits (a wholly $-tagged equities stream) is coming.
    result = _norm().apply([_item(title="$TSLA and $NVDA are leading the rally")])
    assert result.kept == []


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


PROSE_LEX = SymbolLexicon.from_coins(
    [
        {"ticker": "MKR", "name": "Maker"},
        {"ticker": "FLOW", "name": "Flow"},
        {"ticker": "DASH", "name": "Dash"},
        {"ticker": "CRV", "name": "Curve"},
        {"ticker": "BTC", "name": "Bitcoin"},
    ]
)


def _prose_norm() -> ContentNormalizer:
    return ContentNormalizer(PROSE_LEX)


def test_a_coin_name_that_reads_as_prose_proves_nothing_alone() -> None:
    # The ambiguity guard covered tickers but not names, so "the market maker"
    # booked MKR and "cash flow analysis" booked FLOW. Worse, a name match is
    # what unlocks an ambiguous ticker, so one stray noun opened both channels.
    for title in (
        "The market maker adjusted his quotes",
        "Cash flow analysis for the quarter",
        "The yield curve steepened after the Fed meeting",
    ):
        result = _prose_norm().apply([_item(title=title)])
        assert result.kept == [], title
        assert result.dropped[0][1] == "not_relevant", title


def test_a_prose_name_corroborates_its_own_ticker() -> None:
    # Without this, coins whose ticker AND name are both English words would be
    # unreachable: each channel would be waiting on the other.
    result = _prose_norm().apply([_item(title="Dash (DASH) surges after the upgrade")])
    assert result.kept[0].symbols == ["DASH"]


def test_a_prose_name_does_not_corroborate_a_different_ticker() -> None:
    result = _prose_norm().apply([_item(title="Cash flow into Bitcoin accelerates")])
    assert result.kept[0].symbols == ["BTC"]


def test_a_multiword_name_is_still_trusted_on_its_own() -> None:
    lex = SymbolLexicon.from_coins([{"ticker": "BCH", "name": "Bitcoin Cash"}])
    result = ContentNormalizer(lex).apply([_item(title="Bitcoin Cash hard fork lands")])
    assert result.kept[0].symbols == ["BCH"]


REVIEW_LEX = SymbolLexicon.from_coins(
    [
        {"ticker": "ONE", "name": "Harmony"},
        {"ticker": "GRT", "name": "The Graph"},
        {"ticker": "SAND", "name": "The Sandbox"},
        {"ticker": "ATH", "name": "Aethir"},
        {"ticker": "BTC", "name": "Bitcoin"},
    ]
)


def _review_norm() -> ContentNormalizer:
    return ContentNormalizer(REVIEW_LEX)


def test_a_spelled_out_dollar_amount_is_not_a_cashtag() -> None:
    # "$one million" resurrected ONE -- the single worst false positive in
    # production -- through the cashtag channel, which also satisfies the
    # relevance gate, so the row skipped every other check on its way in.
    for title in (
        "The fund is worth $one million after the raise",
        "Analysts see a $trillion opportunity",
        "Revenue up to $ten billion this year",
    ):
        result = _review_norm().apply([_item(title=title)])
        assert result.kept == [], title


def test_an_uppercase_cashtag_still_works() -> None:
    result = _review_norm().apply([_item(title="$ONE is pumping")])
    assert result.kept[0].symbols == ["ONE"]


def test_a_multiword_name_of_ordinary_words_proves_nothing_alone() -> None:
    # "The Graph" ships in the seed universe, so this fired on day one:
    # an inflation article booked GRT, and a devops one booked SAND.
    for title in (
        "As the graph shows, inflation cooled in June",
        "The sandbox environment was misconfigured, engineers say",
    ):
        result = _review_norm().apply([_item(title=title)])
        assert result.kept == [], title


def test_a_multiword_prose_name_corroborates_its_own_ticker() -> None:
    result = _review_norm().apply([_item(title="The Graph GRT indexer rewards rise")])
    assert result.kept[0].symbols == ["GRT"]


def test_a_ticker_that_is_crypto_jargon_needs_corroboration() -> None:
    # ATH means "all-time high" in most crypto copy and is also Aethir's ticker.
    # Uncorroborated it would have climbed the aggregate table exactly as ONE did.
    result = _review_norm().apply([_item(title="BTC ATH incoming, says analyst")])
    assert result.kept[0].symbols == ["BTC"]

    named = _review_norm().apply([_item(title="Aethir ATH node sale opens")])
    assert named.kept[0].symbols == ["ATH"]


def test_lowercase_tickers_resolve_when_the_ticker_is_not_a_word() -> None:
    # Social copy is routinely all-lowercase. These items used to be dropped
    # outright -- not booked as MARKET, lost -- and the drop counter conflated
    # them with football articles, so the loss was invisible.
    assert _norm().apply([_item(title="btc is ripping right now")]).kept[0].symbols == [
        "BTC"
    ]
    assert _norm().apply([_item(title="eth looking strong today, might buy")]).kept[
        0
    ].symbols == ["ETH"]


def test_a_lowercase_english_word_is_still_not_a_ticker() -> None:
    # The relaxation must not reopen the homograph hole: "one" and "keep"
    # stay disbelieved in lowercase, where they are simply words.
    result = _norm().apply([_item(title="one more day, keep stacking, halving soon")])
    assert result.kept[0].symbols == ["MARKET"]


def test_a_single_generic_term_no_longer_admits_general_news() -> None:
    # Each of these hit exactly one weak keyword and entered as MARKET -- the
    # regional-general-news shape the gate exists to reject.
    for title in (
        "Gas prices drop across the Midwest",
        "Whale watching season opens in Monterey",
        "Bridge collapse closes the interstate",
        "Custody battle ends in family court",
        "New York Stock Exchange halts trading briefly",
    ):
        assert _norm().apply([_item(title=title)]).kept == [], title


def test_two_generic_terms_or_one_specific_term_still_admit() -> None:
    two_weak = _norm().apply(
        [_item(title="Wallet custody rules tighten for exchanges")]
    )
    assert two_weak.kept[0].symbols == ["MARKET"]

    one_strong = _norm().apply([_item(title="A new stablecoin launches this week")])
    assert one_strong.kept[0].symbols == ["MARKET"]
