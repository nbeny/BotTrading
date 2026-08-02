"""control-api: validated editing of the Telegram channel list + source health.

What the operator types has to land on the poll loop unchanged, so the router
normalizes with the *same* shared helper the provider uses — these tests pin the
API-side contract (rejections, cap, empty list) rather than re-testing the
helper, which lives in `tests/test_collector_runtime.py`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tests.control_api_helpers import load_module

from cmi_common.sources import TELEGRAM_SEED_CHANNELS


def _mod():
    return load_module("routers.collectors")


class _FakeCache:
    def __init__(self, store: dict | None = None) -> None:
        self._store = dict(store or {})

    async def get_json(self, key):
        return self._store.get(key)

    async def set_json(self, key, value, ttl_seconds=60):
        self._store[key] = value


def _request(cache: _FakeCache) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cache=cache)))


# --- handle normalization, as the API applies it -------------------------


def test_a_bare_name_passes_through() -> None:
    assert _mod().normalize_channel("cryptonews") == "cryptonews"


def test_the_at_prefix_is_stripped() -> None:
    assert _mod().normalize_channel("@cryptonews") == "cryptonews"


def test_both_link_forms_reduce_to_the_username() -> None:
    assert _mod().normalize_channel("t.me/cryptonews") == "cryptonews"
    assert _mod().normalize_channel("https://t.me/cryptonews") == "cryptonews"


def test_a_trailing_slash_is_tolerated() -> None:
    assert _mod().normalize_channel("https://t.me/cryptonews/") == "cryptonews"


def test_case_is_normalized() -> None:
    assert _mod().normalize_channel("@CryptoNews") == "cryptonews"


def test_invite_links_are_rejected() -> None:
    """They imply a join flow this provider does not perform. Accepted, they
    would become a handle that never resolves — written off every restart, with
    the operator told only that a channel is broken, never that it cannot work."""
    for bad in ("t.me/+AbCdEf", "https://t.me/joinchat/AbCdEf", "@+AbCdEf"):
        with pytest.raises(ValueError):
            _mod().normalize_channel(bad)


def test_an_empty_entry_is_rejected() -> None:
    with pytest.raises(ValueError):
        _mod().normalize_channel("   ")


def test_a_message_permalink_is_rejected() -> None:
    """Pasting a *message* link is the common mistake, not the invite link.
    Stripping `t.me/` leaves `name/1234`, which resolves to nothing: the
    operator would get a 200, see the handle in the chip list, and the provider
    would write it off for the life of the process."""
    for bad in ("https://t.me/binancekillers/1234", "https://t.me/s/durov"):
        with pytest.raises(ValueError):
            _mod().normalize_channel(bad)


def test_the_patch_model_rejects_a_message_permalink() -> None:
    # FastAPI turns this into a 422 the operator can act on, which is the whole
    # difference between a rejected save and a channel that silently never runs.
    with pytest.raises(ValueError):
        _mod().RuntimePatch(telegram_channels=["https://t.me/coinbureau/1234"])


# --- the patch model -----------------------------------------------------


def test_the_patch_model_normalizes_every_entry() -> None:
    patch = _mod().RuntimePatch(
        telegram_channels=["@alpha", "https://t.me/beta", "gamma"]
    )
    assert patch.telegram_channels == ["alpha", "beta", "gamma"]


def test_the_patch_model_drops_duplicate_mirrors() -> None:
    """The same desk is routinely listed under two mirrors; polling it twice
    per cycle buys nothing and spends an MTProto call."""
    patch = _mod().RuntimePatch(
        telegram_channels=["CoinBureau", "@coinbureau", "https://t.me/CoinBureau/"]
    )
    assert patch.telegram_channels == ["coinbureau"]


def test_the_patch_model_rejects_an_invite_link() -> None:
    # FastAPI turns a validator ValueError into a 422 — the request must fail
    # loudly rather than store a handle that can never resolve.
    with pytest.raises(ValueError):
        _mod().RuntimePatch(telegram_channels=["alpha", "t.me/+AbCdEf"])


def test_the_patch_model_rejects_a_blank_entry() -> None:
    with pytest.raises(ValueError):
        _mod().RuntimePatch(telegram_channels=["alpha", "   "])


def test_the_patch_model_caps_the_list() -> None:
    """The MTProto call count per cycle is a function of the channel count."""
    mod = _mod()
    # Every entry has to be a *valid* handle, or this passes on the grammar
    # check having rejected the first one and proves nothing about the cap.
    over = [f"chan{i:03d}" for i in range(mod.MAX_TELEGRAM_CHANNELS + 1)]
    assert mod.RuntimePatch(telegram_channels=over[:-1]).telegram_channels == over[:-1]
    with pytest.raises(ValueError):
        mod.RuntimePatch(telegram_channels=over)


def test_the_shipped_seed_fits_comfortably_under_the_cap() -> None:
    """The cap must leave headroom over what we ship, or the very first save
    from the terminal would 422 on the list the operator was handed."""
    mod = _mod()
    assert len(TELEGRAM_SEED_CHANNELS) < mod.MAX_TELEGRAM_CHANNELS
    patch = mod.RuntimePatch(telegram_channels=list(TELEGRAM_SEED_CHANNELS))
    assert patch.telegram_channels == list(TELEGRAM_SEED_CHANNELS)


def test_an_explicitly_empty_list_is_accepted() -> None:
    """Clearing the list from the terminal is a legitimate operator action."""
    assert _mod().RuntimePatch(telegram_channels=[]).telegram_channels == []


# --- routes --------------------------------------------------------------


async def test_clearing_the_list_survives_the_exclude_none_dump() -> None:
    """`model_dump(exclude_none=True)` drops `None` but keeps `[]` — the whole
    reason an emptied list can reach `set_runtime` at all."""
    mod = _mod()
    cache = _FakeCache()
    out = await mod.set_collectors_runtime(
        mod.RuntimePatch(telegram_channels=[]), _request(cache), principal=None
    )
    assert out["telegram_channels"] == []


async def test_a_patch_that_omits_channels_leaves_them_alone() -> None:
    mod = _mod()
    cache = _FakeCache({"collectors:runtime": {"telegram_channels": ["alpha"]}})
    out = await mod.set_collectors_runtime(
        mod.RuntimePatch(platforms={"reddit": False}), _request(cache), principal=None
    )
    assert out["telegram_channels"] == ["alpha"]


async def test_source_status_omits_a_platform_that_published_nothing() -> None:
    """An unknown state is not a healthy state: inventing `{"ok": true}` here
    would render a green pill for a provider that has never run."""
    out = await _mod().get_collectors_runtime(_request(_FakeCache()), principal=None)
    assert out["source_status"] == {}


#: The full payload a provider actually publishes, all four fields. Fixtures
#: that omitted `updated_at` let control-api filter the blob down and still pass
#: every assertion — while the terminal, which reads that field to tell "healthy
#: a minute ago" from "last healthy in March", rendered every reading as frozen.
_PUBLISHED_HEALTH = {
    "ok": False,
    "reason": "TELEGRAM_SESSION is missing",
    "channels": {"alpha": "UsernameInvalidError"},
    "updated_at": "2026-08-02T09:41:07.123456+00:00",
}


async def test_source_status_carries_the_published_health() -> None:
    cache = _FakeCache({"collectors:status:telegram": dict(_PUBLISHED_HEALTH)})
    out = await _mod().get_collectors_runtime(_request(cache), principal=None)
    # Equality, not field-by-field: control-api is a pass-through here, and the
    # blob's shape is the provider's to define. A field dropped in transit is
    # invisible to any assertion that only names the fields it expects.
    assert out["source_status"]["telegram"] == _PUBLISHED_HEALTH


async def test_the_post_route_reports_health_too() -> None:
    cache = _FakeCache({"collectors:status:telegram": dict(_PUBLISHED_HEALTH)})
    mod = _mod()
    out = await mod.set_collectors_runtime(
        mod.RuntimePatch(social_enabled=False), _request(cache), principal=None
    )
    assert out["source_status"]["telegram"] == _PUBLISHED_HEALTH
    assert out["known_platforms"] == mod.KNOWN_PLATFORMS


async def test_any_known_platform_that_publishes_health_is_reported() -> None:
    """The platform list is derived from `KNOWN_PLATFORMS`, not hardcoded to
    Telegram. A hardcoded tuple drops a new provider's health silently — no
    error, no failing test — which is the three-copy drift CLAUDE.md warns
    about, in miniature."""
    health = {"ok": True, "reason": None, "channels": {}, "updated_at": "2026-08-02"}
    cache = _FakeCache({"collectors:status:reddit": dict(health)})
    out = await _mod().get_collectors_runtime(_request(cache), principal=None)
    assert out["source_status"] == {"reddit": health}


async def test_every_known_platform_is_probed_for_health() -> None:
    # The derivation itself: no category and no platform may be left out.
    mod = _mod()
    known = [p for ps in mod.KNOWN_PLATFORMS.values() for p in ps]
    assert sorted(mod._STATUS_PLATFORMS) == sorted(known)
