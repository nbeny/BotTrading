"""Operator toggles for collector sources, held in Redis `collectors:runtime`.

control-api writes this key; each provider's poll loop reads it and skips a cycle
when its category (social/news) or its own platform is disabled. Everything is
enabled by default — a missing key or missing platform reads as ON, so the
collectors run fully out of the box.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

from ..cache import Cache

logger = logging.getLogger(__name__)

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


#: A handle containing either marker came from an invite link (`t.me/+AbCdEf`,
#: `t.me/joinchat/AbCdEf`). Telegram only honours those through a join flow the
#: provider deliberately does not perform, so the handle can never resolve: kept,
#: it would be written off on every restart and shown to the operator as a broken
#: channel, with nothing saying it could never have worked. Rejected at the door.
_INVITE_MARKERS = ("+", "joinchat")

_LINK_PREFIXES = ("https://t.me/", "http://t.me/", "t.me/", "@")

#: Telegram's own username grammar: 4 to 32 of letters, digits and underscore.
#: Stripping the `t.me/` prefix is not validation — `https://t.me/name/1234`, a
#: *message* permalink, survives it as `name/1234`, and copying one of those out
#: of the app is far more common than copying an invite link. Anything that is
#: not a username resolves to nothing, so it is written off for the life of the
#: process after the operator has already been told the save succeeded: the same
#: permanent, silent write-off the invite-link guard above exists to prevent.
#: Matched case-insensitively — the handle is lowercased before it gets here,
#: and the rule must not quietly depend on that.
_USERNAME_RE = re.compile(r"[A-Za-z0-9_]{4,32}")


def source_status_key(platform: str) -> str:
    """Redis key a provider publishes its own health under.

    Built here so the writer (a collector) and the reader (control-api, which
    may not import a collector) cannot drift onto two different keys — a drift
    that fails silently, as "no key" is a legal state meaning "never reported".
    """
    return f"collectors:status:{platform}"


def normalize_channel(target: str) -> str:
    """``@Name``, ``t.me/Name``, ``https://t.me/Name/``, ``Name`` -> ``name``.

    Shared rather than owned by collector-social because control-api validates
    operator input with these exact rules: what the terminal accepts has to be
    byte-identical to what the poll loop resolves, or a saved channel silently
    polls nothing.

    Raises ``ValueError`` on a blank entry, an invite link, or anything that is
    not a Telegram username. What that means is the caller's call, and the two
    callers want opposite things — see ``parse_channels``.
    """
    handle = target.strip().rstrip("/")
    for prefix in _LINK_PREFIXES:
        if handle.startswith(prefix):
            handle = handle[len(prefix) :]
            break
    handle = handle.lower()
    if not handle:
        raise ValueError("empty channel name")
    # Before the grammar check, which `+AbCdEf` and `joinchat/AbCdEf` would also
    # fail: the invite-link message names the actual problem, and telling an
    # operator who pasted an invite link that it is not a valid username sends
    # them looking for a typo that is not there.
    if any(marker in handle for marker in _INVITE_MARKERS):
        raise ValueError(f"unsupported invite link: {target!r}")
    if not _USERNAME_RE.fullmatch(handle):
        raise ValueError(
            f"not a Telegram channel username: {handle!r} (from {target!r}) — "
            "expected 4 to 32 letters, digits or underscores; a message link "
            "like t.me/name/1234 is not a channel, use t.me/name"
        )
    return handle


def dedupe_channels(handles: Iterable[str]) -> list[str]:
    """First occurrence wins, order preserved — the same desk is routinely
    listed under two mirrors, and polling it twice per cycle buys nothing."""
    seen: set[str] = set()
    out: list[str] = []
    for handle in handles:
        if handle and handle not in seen:
            seen.add(handle)
            out.append(handle)
    return out


def parse_channels(raw: str | None) -> list[str]:
    """Split a ``TELEGRAM_CHANNELS`` value into normalized handles.

    This is a **bootstrap**, not a request: it runs once at startup to seed
    ``collectors:runtime``, past which the operator owns the list and the env var
    is ignored. So a bad entry is dropped with a warning rather than raised —
    unlike the control-api validator, which must reject the whole request loudly
    because there a human is watching and can retype it. Here nobody is, and
    refusing to boot the collector over one malformed handle would cost us every
    other channel on the list too.
    """
    if raw is None or not raw.strip():
        return list(TELEGRAM_SEED_CHANNELS)
    handles: list[str] = []
    for part in raw.split(","):
        if not part.strip():
            continue
        try:
            handles.append(normalize_channel(part))
        except ValueError as exc:
            logger.warning("TELEGRAM_CHANNELS: skipping %r — %s", part, exc)
    return dedupe_channels(handles)


def default_runtime() -> dict[str, Any]:
    return {
        "social_enabled": True,
        "news_enabled": True,
        "platforms": {p: True for ps in KNOWN_PLATFORMS.values() for p in ps},
        "telegram_channels": list(TELEGRAM_SEED_CHANNELS),
    }


def _merge_over_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    """Stored toggles read over the defaults (new platforms default ON)."""
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


async def get_runtime(cache: Cache) -> dict[str, Any]:
    """Current toggles, merged over defaults (new platforms default ON)."""
    return _merge_over_defaults(await cache.get_json(RUNTIME_KEY) or {})


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
    """Apply a partial update (category flags and/or per-platform flags).

    What is returned and what is persisted deliberately differ on
    ``telegram_channels``: the caller gets the merged view, seed included, so the
    terminal can render the default list — while Redis keeps the entry *absent*
    unless it was already stored or this patch sets it.

    That asymmetry is the whole point. An absent entry is the only "never
    configured" signal, and collector-social's boot seed is its only reader:
    materialising the seed here would spend it on an unrelated toggle, replacing
    the operator's ``TELEGRAM_CHANNELS`` with the shipped list and skipping the
    seed forever after. It is reachable in practice — the boot seed is best
    effort, so a Redis blip at collector-social startup plus any later toggle from
    the terminal is enough.
    """
    raw = await cache.get_json(RUNTIME_KEY) or {}
    cur = _merge_over_defaults(raw)
    if "social_enabled" in patch:
        cur["social_enabled"] = bool(patch["social_enabled"])
    if "news_enabled" in patch:
        cur["news_enabled"] = bool(patch["news_enabled"])
    for name, on in (patch.get("platforms") or {}).items():
        cur["platforms"][name] = bool(on)
    # Remplacement intégral, contrairement aux `platforms` qui se mergent.
    if patch.get("telegram_channels") is not None:
        cur["telegram_channels"] = [str(c) for c in patch["telegram_channels"]]
    stored = dict(cur)
    if patch.get("telegram_channels") is None and raw.get("telegram_channels") is None:
        # Neither stored nor patched: leave the entry out (see the docstring).
        # `is None` rather than a key check, to match every other reader of this
        # field — a stored null has never meant "configured" anywhere else.
        stored.pop("telegram_channels", None)
    # Durable key (no expiry) — persist, like trading:runtime.
    await cache.set_json(RUNTIME_KEY, stored, ttl_seconds=0)
    return cur
