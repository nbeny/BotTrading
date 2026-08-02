"""Collector source toggles held in Redis `collectors:runtime`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[1] / "libs" / "cmi_common"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from cmi_common.sources import runtime  # noqa: E402


class _FakeCache:
    def __init__(self, initial=None):
        self._store = {"collectors:runtime": initial} if initial is not None else {}

    async def get_json(self, key):
        return self._store.get(key)

    async def set_json(self, key, value, ttl_seconds=60):
        self._store[key] = value


async def test_default_all_enabled() -> None:
    cache = _FakeCache()  # no key -> everything on
    assert await runtime.is_enabled(cache, "social", "bluesky") is True
    assert await runtime.is_enabled(cache, "news", "gdelt") is True


async def test_category_toggle_off() -> None:
    cache = _FakeCache({"social_enabled": False})
    assert await runtime.is_enabled(cache, "social", "bluesky") is False
    assert await runtime.is_enabled(cache, "news", "gdelt") is True  # news still on


async def test_platform_toggle_off() -> None:
    cache = _FakeCache({"platforms": {"reddit": False}})
    assert await runtime.is_enabled(cache, "social", "reddit") is False
    assert await runtime.is_enabled(cache, "social", "bluesky") is True


async def test_set_runtime_merges_and_persists() -> None:
    cache = _FakeCache()
    out = await runtime.set_runtime(cache, {"platforms": {"cryptocompare": False}})
    assert out["platforms"]["cryptocompare"] is False
    assert out["platforms"]["gdelt"] is True  # untouched -> default on
    assert out["social_enabled"] is True
    # persisted so the poll loops see it
    assert await runtime.is_enabled(cache, "news", "cryptocompare") is False


async def test_get_runtime_exposes_known_platforms_shape() -> None:
    cache = _FakeCache()
    rt = await runtime.get_runtime(cache)
    assert "bluesky" in rt["platforms"] and "newsdata" in rt["platforms"]


async def test_telegram_is_a_known_social_platform() -> None:
    """SourcesPanel itère sur known_platforms; sans cette entrée l'interrupteur
    n'apparaît jamais dans le terminal."""
    assert "telegram" in runtime.KNOWN_PLATFORMS["social"]


async def test_telegram_channels_default_to_the_seed() -> None:
    cache = _FakeCache()
    rt = await runtime.get_runtime(cache)
    assert rt["telegram_channels"] == list(runtime.TELEGRAM_SEED_CHANNELS)


async def test_an_explicitly_empty_channel_list_is_not_refilled_by_the_seed() -> None:
    """La graine livrée n'est plus vide (elle porte les 24 canaux du desk), donc
    la distinction `[]` / graine est falsifiable sans monkeypatch : `[] or SEED`
    rendrait ici les 24 canaux au lieu de la liste vidée par l'opérateur."""
    assert runtime.TELEGRAM_SEED_CHANNELS  # sinon l'assertion ci-dessous est vide
    cache = _FakeCache({"telegram_channels": []})
    rt = await runtime.get_runtime(cache)
    assert rt["telegram_channels"] == []


async def test_set_runtime_replaces_the_channel_list_wholesale() -> None:
    """Remplacement, pas merge : sans ça un canal supprimé depuis l'UI
    ressusciterait au patch suivant."""
    cache = _FakeCache({"telegram_channels": ["alpha", "beta"]})
    out = await runtime.set_runtime(cache, {"telegram_channels": ["gamma"]})
    assert out["telegram_channels"] == ["gamma"]


async def test_set_runtime_leaves_channels_alone_when_not_patched() -> None:
    cache = _FakeCache({"telegram_channels": ["alpha"]})
    out = await runtime.set_runtime(cache, {"platforms": {"reddit": False}})
    assert out["telegram_channels"] == ["alpha"]


async def test_set_runtime_does_not_consume_the_never_configured_signal() -> None:
    """An absent `telegram_channels` is the only "never configured" signal, and
    collector-social's boot seed is its only consumer. Materialising the seed into
    the *persisted* dict here would spend it on an unrelated toggle, freezing the
    24 shipped channels in place of the operator's `TELEGRAM_CHANNELS`."""
    assert runtime.TELEGRAM_SEED_CHANNELS  # otherwise the first assert is vacuous
    cache = _FakeCache({"platforms": {"telegram": True}})

    out = await runtime.set_runtime(cache, {"platforms": {"reddit": False}})

    # The response still carries the seed — the terminal has to render a default
    # list — while Redis keeps the entry absent.
    assert out["telegram_channels"] == list(runtime.TELEGRAM_SEED_CHANNELS)
    stored = await cache.get_json("collectors:runtime")
    assert "telegram_channels" not in stored
    assert stored["platforms"]["reddit"] is False  # the patch itself did persist


async def test_set_runtime_persists_a_channel_list_it_was_given() -> None:
    """The other side of the same rule: an explicit patch — including the empty
    "poll nobody" — must land in Redis, or the seed would undo it on the next
    restart."""
    cache = _FakeCache()
    await runtime.set_runtime(cache, {"telegram_channels": []})
    assert (await cache.get_json("collectors:runtime"))["telegram_channels"] == []


# --- handle normalization, shared by the provider and control-api ---------


def test_normalize_channel_accepts_every_form_operators_paste() -> None:
    for raw in (
        "coinbureau",
        "@CoinBureau",
        "t.me/coinbureau",
        "https://t.me/CoinBureau/",
    ):
        assert runtime.normalize_channel(raw) == "coinbureau"


def test_normalize_channel_rejects_invite_links() -> None:
    """An invite link resolves to nothing without a join flow; kept, it would be
    written off on every restart with no hint of why it can never work."""
    for bad in ("t.me/+AbCdEf", "https://t.me/joinchat/AbCdEf", "@+AbCdEf"):
        with pytest.raises(ValueError):
            runtime.normalize_channel(bad)


def test_normalize_channel_rejects_a_blank_entry() -> None:
    with pytest.raises(ValueError):
        runtime.normalize_channel("  ")


def test_normalize_channel_rejects_a_message_permalink() -> None:
    """Copying a *message* link out of Telegram is far more common than copying
    an invite link, and stripping the `t.me/` prefix leaves `name/1234` — which
    resolves to nothing. Accepted, the operator gets a 200, sees the handle in
    the chip list, and the provider writes it off for the life of the process:
    exactly the outcome the invite-link guard exists to prevent."""
    for bad in (
        "https://t.me/binancekillers/1234",
        "t.me/coinbureau/9",
        "https://t.me/s/durov",  # the web-preview form, same trap
    ):
        with pytest.raises(ValueError):
            runtime.normalize_channel(bad)


def test_normalize_channel_rejects_anything_outside_telegrams_grammar() -> None:
    """A username is 4-32 of `[A-Za-z0-9_]`. Nothing downstream validates it, so
    whatever passes here is what the provider spends a ResolveUsername call on
    every restart before writing it off."""
    for bad in ("@coin bureau", "a", "abc", "coin-bureau", "coin.bureau", "a" * 33):
        with pytest.raises(ValueError):
            runtime.normalize_channel(bad)


def test_normalize_channel_accepts_the_edges_of_the_grammar() -> None:
    # The bounds are inclusive on both ends, and `_` is a legal username char —
    # a rule tightened past Telegram's own would reject handles that do resolve.
    assert runtime.normalize_channel("abcd") == "abcd"
    assert runtime.normalize_channel("a" * 32) == "a" * 32
    assert runtime.normalize_channel("@Fat_Pig_Signals1") == "fat_pig_signals1"


def test_every_seeded_channel_satisfies_the_rule_the_terminal_enforces() -> None:
    """The seed is what the operator is handed, and the first save from the
    terminal sends it straight back through `normalize_channel`. A seed entry
    the validator rejects would 422 that save."""
    assert runtime.TELEGRAM_SEED_CHANNELS
    for handle in runtime.TELEGRAM_SEED_CHANNELS:
        assert runtime.normalize_channel(handle) == handle


def test_dedupe_channels_keeps_the_first_occurrence() -> None:
    assert runtime.dedupe_channels(["b", "a", "b"]) == ["b", "a"]


def test_parse_channels_normalizes_handles_and_drops_duplicates() -> None:
    parsed = runtime.parse_channels(
        " @CoinBureau , https://t.me/CoinBureau, t.me/wublockchainenglish/ ,coinbureau"
    )
    assert parsed == ["coinbureau", "wublockchainenglish"]


def test_parse_channels_falls_back_to_the_shared_seed_list() -> None:
    # An empty seed would make the two assertions below pass vacuously.
    assert runtime.TELEGRAM_SEED_CHANNELS
    assert runtime.parse_channels(None) == list(runtime.TELEGRAM_SEED_CHANNELS)
    assert runtime.parse_channels("   ") == list(runtime.TELEGRAM_SEED_CHANNELS)


def test_parse_channels_skips_a_bad_entry_instead_of_killing_the_boot() -> None:
    """The env path is a bootstrap, not a request: one unusable handle must not
    take the whole collector down at startup, where nobody can fix it."""
    assert runtime.parse_channels("alpha, t.me/+AbCdEf ,beta") == ["alpha", "beta"]
