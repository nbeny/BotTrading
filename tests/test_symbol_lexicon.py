"""SymbolLexicon: universe snapshot, ticker/name resolution, computed ambiguity."""

from __future__ import annotations

from cmi_common.sources import SymbolLexicon

COINS = [
    {"ticker": "BTC", "name": "Bitcoin"},
    {"ticker": "ETH", "name": "Ethereum"},
    {"ticker": "HYPE", "name": "Hyperliquid"},
    {"ticker": "ONE", "name": "Harmony"},
    {"ticker": "KEEP", "name": "Keep Network"},
]


def test_resolves_ticker_case_insensitively() -> None:
    lex = SymbolLexicon.from_coins(COINS)
    assert lex.resolve_ticker("btc") == "BTC"
    assert lex.resolve_ticker("BTC") == "BTC"


def test_unknown_ticker_resolves_to_none() -> None:
    lex = SymbolLexicon.from_coins(COINS)
    assert lex.resolve_ticker("NOTACOIN") is None


def test_resolves_full_coin_names_found_in_text() -> None:
    lex = SymbolLexicon.from_coins(COINS)
    found = lex.names_in("hyperliquid price prediction today for hype")
    assert found == {"HYPE"}


def test_name_matching_respects_word_boundaries() -> None:
    # "bitcoiner" must not match the coin name "Bitcoin".
    lex = SymbolLexicon.from_coins(COINS)
    assert lex.names_in("every bitcoiner knows") == set()


def test_ambiguous_set_is_computed_from_common_words() -> None:
    # ONE, KEEP and HYPE are ordinary English words; BTC and ETH are not.
    lex = SymbolLexicon.from_coins(COINS)
    assert lex.is_ambiguous("ONE") is True
    assert lex.is_ambiguous("KEEP") is True
    assert lex.is_ambiguous("HYPE") is True
    assert lex.is_ambiguous("BTC") is False
    assert lex.is_ambiguous("ETH") is False


def test_seed_lexicon_is_non_empty_and_knows_majors() -> None:
    # Guarantees a cold Redis degrades recall instead of dropping everything.
    from cmi_common.sources import SEED_LEXICON

    assert SEED_LEXICON.resolve_ticker("BTC") == "BTC"
    assert SEED_LEXICON.resolve_ticker("ETH") == "ETH"


def test_short_names_are_ignored() -> None:
    # A 2-char coin name would match half the corpus; it must not be indexed.
    lex = SymbolLexicon.from_coins([{"ticker": "OK", "name": "Ok"}, *COINS])
    assert lex.names_in("it is ok to buy") == set()


def test_coins_without_a_ticker_are_skipped() -> None:
    lex = SymbolLexicon.from_coins([{"ticker": "", "name": "Nameless"}, *COINS])
    assert lex.names_in("a nameless coin") == set()
    assert lex.resolve_ticker("") is None


def test_a_coin_with_no_name_still_resolves_by_ticker() -> None:
    lex = SymbolLexicon.from_coins([{"ticker": "XYZ", "name": ""}, *COINS])
    assert lex.resolve_ticker("XYZ") == "XYZ"


def test_longest_coin_name_wins_over_a_shorter_prefix() -> None:
    # Without longest-first alternation, "Bitcoin Cash" would resolve to BTC —
    # sentiment about BCH would land on Bitcoin.
    lex = SymbolLexicon.from_coins([*COINS, {"ticker": "BCH", "name": "Bitcoin Cash"}])
    assert lex.names_in("bitcoin cash hard fork news") == {"BCH"}
