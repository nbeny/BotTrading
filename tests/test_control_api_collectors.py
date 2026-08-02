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
    over = [f"c{i}" for i in range(mod.MAX_TELEGRAM_CHANNELS + 1)]
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


async def test_source_status_carries_the_published_health() -> None:
    cache = _FakeCache(
        {
            "collectors:status:telegram": {
                "ok": False,
                "reason": "TELEGRAM_SESSION is missing",
                "channels": {"alpha": "UsernameInvalidError"},
            }
        }
    )
    out = await _mod().get_collectors_runtime(_request(cache), principal=None)
    assert out["source_status"]["telegram"]["ok"] is False
    assert out["source_status"]["telegram"]["channels"] == {
        "alpha": "UsernameInvalidError"
    }


async def test_the_post_route_reports_health_too() -> None:
    cache = _FakeCache({"collectors:status:telegram": {"ok": True, "reason": None}})
    mod = _mod()
    out = await mod.set_collectors_runtime(
        mod.RuntimePatch(social_enabled=False), _request(cache), principal=None
    )
    assert out["source_status"]["telegram"]["ok"] is True
    assert out["known_platforms"] == mod.KNOWN_PLATFORMS
