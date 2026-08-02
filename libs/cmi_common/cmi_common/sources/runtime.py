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
    "social": [
        "bluesky",
        "reddit",
        "mastodon",
        "fourchan",
        "neynar",
        "youtube",
        "lens",
        "telegram",
    ],
    "news": ["cryptocompare", "gdelt", "newsdata", "rss"],
}

#: Canaux Telegram livrés par défaut : desks de signaux, groupes alpha et les
#: feeds d'annonces qui déplacent les listings. La liste vit ici et non dans
#: collector-social parce que control-api doit pouvoir l'afficher dans le
#: terminal et n'a pas le droit d'importer un collecteur — une seule source de
#: vérité, dans la couche partagée.
TELEGRAM_SEED_CHANNELS: list[str] = [
    "binancekillers",
    "wallstreetqueenofficialx1",
    "wallstreetqueenofficialtg",
    "fatpigsignals_fps",
    "binancekillers_vips",
    "porter_news",
    "airdrops_io",
    "incomesharkst",
    "cryptocapotg",
    "bitcoin_bulletssignals",
    "cryptoinnercircle",
    "coinmarketcapannouncementsairdop",
    "binance_moonbix_announcements",
    "binance_announcements",
    "learn2tradeoriginal1",
    "crypto_signals_org_official",
    "wallstreet_queenofficials",
    "fat_pigs_signals",
    "fat_pig_signals1",
    "binance_killers_signals",
    "coinbureau",
    "wallstreetqueenofficial",
    "icospeakschannels",
    "wublockchainenglish",
]


def default_runtime() -> dict[str, Any]:
    return {
        "social_enabled": True,
        "news_enabled": True,
        "platforms": {p: True for ps in KNOWN_PLATFORMS.values() for p in ps},
        "telegram_channels": list(TELEGRAM_SEED_CHANNELS),
    }


async def get_runtime(cache: Cache) -> dict[str, Any]:
    """Current toggles, merged over defaults (new platforms default ON)."""
    cfg = await cache.get_json(RUNTIME_KEY) or {}
    merged = default_runtime()
    merged["social_enabled"] = bool(cfg.get("social_enabled", True))
    merged["news_enabled"] = bool(cfg.get("news_enabled", True))
    merged["platforms"].update(cfg.get("platforms", {}) or {})
    channels = cfg.get("telegram_channels")
    # `[]` est un "aucun canal" délibéré, pas un "non renseigné". Un `or` ferait
    # revivre la graine que l'opérateur vient de vider.
    if channels is not None:
        merged["telegram_channels"] = [str(c) for c in channels]
    return merged


async def is_enabled(cache: Cache, kind: str, platform: str) -> bool:
    """True if `platform` (of category `kind`) should poll now. Fail-open: any
    read/parse error leaves the source running rather than silently muting it."""
    try:
        cfg = await cache.get_json(RUNTIME_KEY)
    except Exception:
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
    # Remplacement intégral, contrairement aux `platforms` qui se mergent.
    if patch.get("telegram_channels") is not None:
        cur["telegram_channels"] = [str(c) for c in patch["telegram_channels"]]
    # Durable key (no expiry) — persist, like trading:runtime.
    await cache.set_json(RUNTIME_KEY, cur, ttl_seconds=0)
    return cur
