"""Operator toggles for collector sources, held in Redis `collectors:runtime`.

control-api writes this key; each provider's poll loop reads it and skips a cycle
when its category (social/news) or its own platform is disabled. Everything is
enabled by default — a missing key or missing platform reads as ON, so the
collectors run fully out of the box.
"""

from __future__ import annotations

from typing import Any

from ..cache import Cache

RUNTIME_KEY = "collectors:runtime"

# Canonical platform list per category (provider .name values).
KNOWN_PLATFORMS: dict[str, list[str]] = {
    "social": ["bluesky", "reddit", "mastodon", "fourchan", "neynar", "youtube", "lens"],
    "news": ["cryptocompare", "gdelt", "newsdata", "rss"],
}


def default_runtime() -> dict[str, Any]:
    return {
        "social_enabled": True,
        "news_enabled": True,
        "platforms": {
            p: True for ps in KNOWN_PLATFORMS.values() for p in ps
        },
    }


async def get_runtime(cache: Cache) -> dict[str, Any]:
    """Current toggles, merged over defaults (new platforms default ON)."""
    cfg = await cache.get_json(RUNTIME_KEY) or {}
    merged = default_runtime()
    merged["social_enabled"] = bool(cfg.get("social_enabled", True))
    merged["news_enabled"] = bool(cfg.get("news_enabled", True))
    merged["platforms"].update(cfg.get("platforms", {}) or {})
    return merged


async def is_enabled(cache: Cache, kind: str, platform: str) -> bool:
    """True if `platform` (of category `kind`) should poll now. Fail-open: any
    read/parse error leaves the source running rather than silently muting it."""
    try:
        cfg = await cache.get_json(RUNTIME_KEY)
    except Exception:  # noqa: BLE001 - never mute a source on a cache blip
        return True
    if not cfg:
        return True
    if not cfg.get(f"{kind}_enabled", True):
        return False
    return bool(cfg.get("platforms", {}).get(platform, True))


async def set_runtime(cache: Cache, patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update (category flags and/or per-platform flags)."""
    cur = await get_runtime(cache)
    if "social_enabled" in patch:
        cur["social_enabled"] = bool(patch["social_enabled"])
    if "news_enabled" in patch:
        cur["news_enabled"] = bool(patch["news_enabled"])
    for name, on in (patch.get("platforms") or {}).items():
        cur["platforms"][name] = bool(on)
    # Durable key (no expiry) — persist, like trading:runtime.
    await cache.set_json(RUNTIME_KEY, cur, ttl_seconds=0)
    return cur
