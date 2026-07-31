"""Token metadata upsert: what the persister writes, and how rarely."""

from __future__ import annotations

from service_modules import load_service_module

persister = load_service_module("api-gateway", "persister")
TokenMetaCache = persister.TokenMetaCache


def test_first_sighting_is_always_written():
    cache = TokenMetaCache(min_interval_s=3600)
    assert cache.should_write("bitcoin", ("Bitcoin", 1, False), now=0.0) is True


def test_unchanged_metadata_is_not_rewritten_within_the_interval():
    cache = TokenMetaCache(min_interval_s=3600)
    cache.should_write("bitcoin", ("Bitcoin", 1, False), now=0.0)
    assert cache.should_write("bitcoin", ("Bitcoin", 1, False), now=60.0) is False


def test_changed_metadata_is_written_immediately():
    """A token entering the trending set must not wait out the interval."""
    cache = TokenMetaCache(min_interval_s=3600)
    cache.should_write("bitcoin", ("Bitcoin", 1, False), now=0.0)
    assert cache.should_write("bitcoin", ("Bitcoin", 1, True), now=60.0) is True


def test_unchanged_metadata_is_refreshed_after_the_interval():
    cache = TokenMetaCache(min_interval_s=3600)
    cache.should_write("bitcoin", ("Bitcoin", 1, False), now=0.0)
    assert cache.should_write("bitcoin", ("Bitcoin", 1, False), now=3601.0) is True


def test_tokens_are_tracked_independently():
    cache = TokenMetaCache(min_interval_s=3600)
    cache.should_write("bitcoin", ("Bitcoin", 1, False), now=0.0)
    assert cache.should_write("ethereum", ("Ethereum", 2, False), now=1.0) is True
