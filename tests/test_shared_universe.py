"""The universe helpers are shared, and collector-kraken still re-exports them."""

from __future__ import annotations

from service_modules import load_service_module

from cmi_common.db import universe as shared


def test_shared_module_exposes_the_two_sql_helpers() -> None:
    assert callable(shared.priced_symbols)
    assert callable(shared.mention_counts)


def test_collector_kraken_reexports_the_same_objects() -> None:
    # Same object, not a copy: a re-export that drifted into a second
    # implementation is exactly the failure this move exists to prevent.
    kraken = load_service_module("collector-kraken", "application.universe")
    assert kraken.priced_symbols is shared.priced_symbols
    assert kraken.mention_counts is shared.mention_counts


def test_majors_applies_the_mention_threshold() -> None:
    mentions = {"BTC": 421, "ETH": 163, "DOT": 11, "FOO": 3}
    assert shared.majors({"BTC", "ETH", "DOT", "FOO"}, mentions, 10) == {
        "BTC",
        "ETH",
        "DOT",
    }


def test_majors_excludes_symbols_absent_from_the_mention_map() -> None:
    assert shared.majors({"BTC", "GHOST"}, {"BTC": 50}, 10) == {"BTC"}
