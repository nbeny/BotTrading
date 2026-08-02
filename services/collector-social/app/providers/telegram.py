"""Telegram (MTProto) social provider -> RawItem per channel message (key-gated).

Reads public channels through a **user** session, not a bot: a bot only ever
sees a channel it administers, and every channel here is third-party. The
session is minted once, interactively, by ``scripts/telegram_session.py`` and
handed over as ``TELEGRAM_SESSION`` — nothing in this module can prompt for a
login code, so an unauthorized session raises rather than blocking the poll loop
forever on a stdin read that will never be answered inside a container.

Telethon is imported lazily by the default client factory: the module must stay
importable without it (tests inject a fake client), and only the live provider
needs the real library.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from cmi_common.sources import RateLimitedError, RawItem

logger = logging.getLogger(__name__)

#: Channels polled when ``TELEGRAM_CHANNELS`` is unset — signal desks, alpha
#: groups and the announcement feeds that move listings.
DEFAULT_CHANNELS: tuple[str, ...] = (
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
)

#: Messages pulled per channel on the first cycle (no cursor yet). Small on
#: purpose: this is a live signal, not an archive import.
BACKFILL = 20
#: Ceiling per channel per cycle once a cursor exists. A channel that outruns it
#: leaves a gap rather than stalling the whole fan-out on one firehose.
MAX_PER_CYCLE = 100
#: Telegram posts run to 4096 chars, mostly disclaimer boilerplate past the call.
MAX_TEXT = 4000


def _default_client_factory(
    api_id: int, api_hash: str, session: str
) -> tuple[Any, Any]:
    """Build a live Telethon client and hand back its ``errors`` module.

    The errors travel with the client so the provider never has to import
    telethon itself — that is what lets tests swap in a fake with fake error
    classes and keep the library out of the test environment entirely.
    """
    from telethon import TelegramClient, errors
    from telethon.sessions import StringSession

    return TelegramClient(StringSession(session), api_id, api_hash), errors


class TelegramProvider:
    name = "telegram"
    kind = "social"
    # One cycle costs one MTProto call per channel, so the budget that matters
    # is the poll interval (300s by default), not this cap; it exists to stop a
    # restart loop from re-resolving two dozen usernames every few seconds.
    rate_limit = (1, 60)

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session: str,
        channels: Sequence[str] = DEFAULT_CHANNELS,
        backfill: int = BACKFILL,
        max_per_cycle: int = MAX_PER_CYCLE,
        client_factory: Callable[[], tuple[Any, Any]] | None = None,
    ) -> None:
        self._channels = list(channels)
        self._backfill = backfill
        self._max_per_cycle = max_per_cycle
        self._factory = client_factory or (
            lambda: _default_client_factory(api_id, api_hash, session)
        )
        self._client: Any | None = None
        self._errors: Any | None = None
        # Resolved entities, cursors and write-offs are per-process: a restart
        # re-resolves and re-reads the tail, and the (source, external_id)
        # unique index absorbs the overlap.
        self._entities: dict[str, Any] = {}
        self._cursor: dict[str, int] = {}
        self._dead: set[str] = set()

    async def close(self) -> None:
        if self._client is None:
            return
        await _disconnect(self._client)
        self._client = None

    async def fetch(self) -> list[RawItem]:
        client, errors = await self._ensure_client()
        items: list[RawItem] = []
        for channel in self._channels:
            if channel in self._dead:
                continue
            try:
                items.extend(await self._fetch_channel(client, channel))
            except errors.FloodWaitError as exc:
                # Telegram states the pause it wants, in seconds. Hand it to the
                # loop instead of walking the remaining channels into the wall.
                raise RateLimitedError(float(getattr(exc, "seconds", 60))) from exc
            except (
                errors.ChannelPrivateError,
                errors.UsernameInvalidError,
                errors.UsernameNotOccupiedError,
                ValueError,  # telethon's "no user has X as username"
            ) as exc:
                # Unresolvable for *this* account: renamed, banned, or invite-only
                # and we are not a member. Retrying every cycle would spend
                # ResolveUsername calls forever, so write it off until restart.
                self._dead.add(channel)
                logger.warning(
                    "telegram: dropping %s — %s: %s", channel, type(exc).__name__, exc
                )
        return items

    async def _ensure_client(self) -> tuple[Any, Any]:
        if self._client is not None and self._errors is not None:
            return self._client, self._errors
        client, errors = self._factory()
        await client.connect()
        if not await client.is_user_authorized():
            # Drop the socket here, not via close(): the failed client was never
            # published to self._client, so close() would have nothing to hang up
            # and every backed-off retry would leak a connection.
            await _disconnect(client)
            # Loud on every cycle rather than an empty fetch: a silent [] here
            # reads downstream as "Telegram had nothing to say", which is a
            # different claim from "Telegram was never asked".
            raise RuntimeError(
                "telegram: TELEGRAM_SESSION is missing or no longer authorized — "
                "mint a fresh one with `python scripts/telegram_session.py`"
            )
        self._client, self._errors = client, errors
        return client, errors

    async def _fetch_channel(self, client: Any, channel: str) -> list[RawItem]:
        entity = self._entities.get(channel)
        if entity is None:
            entity = await client.get_entity(channel)
            self._entities[channel] = entity
        handle = getattr(entity, "username", None) or channel
        peer_id = getattr(entity, "id", channel)

        last = self._cursor.get(channel)
        kwargs: dict[str, Any] = {
            "limit": self._backfill if last is None else self._max_per_cycle
        }
        if last:
            kwargs["min_id"] = last

        items: list[RawItem] = []
        highest = last or 0
        async for msg in client.iter_messages(entity, **kwargs):
            msg_id = int(getattr(msg, "id", 0) or 0)
            highest = max(highest, msg_id)
            text = (getattr(msg, "message", None) or "").strip()
            if not text:
                continue  # media-only post: nothing to score
            views = getattr(msg, "views", None)
            items.append(
                RawItem(
                    source=self.name,
                    kind=self.kind,
                    # Peer id, not the handle: a channel that renames keeps its
                    # id, so dedup survives the rename.
                    external_id=f"{peer_id}:{msg_id}",
                    text=text[:MAX_TEXT],
                    url=f"https://t.me/{handle}/{msg_id}",
                    author=handle,
                    # Views are absent on non-broadcast peers. None means "not
                    # measured"; 0.0 would mean "measured, nobody read it".
                    engagement=float(views) if views is not None else None,
                    published_at=getattr(msg, "date", None),
                    # Symbols are left to the collector's normalizer, which sees
                    # every provider and overwrites whatever is set here.
                )
            )
        self._cursor[channel] = highest
        return items


async def _disconnect(client: Any) -> None:
    # Telethon returns a coroutine when the loop is running and None when it is
    # not; both are legal returns from the same call.
    result = client.disconnect()
    if inspect.isawaitable(result):
        await result


def parse_channels(raw: str | None) -> list[str]:
    """Split a ``TELEGRAM_CHANNELS`` value into normalized handles.

    Accepts ``@handle``, ``t.me/handle`` links and bare names, in any case, and
    drops duplicates — the same desk is often listed under two mirrors.
    """
    if raw is None or not raw.strip():
        return list(DEFAULT_CHANNELS)
    return _dedupe(_normalize(part) for part in raw.split(",") if part.strip())


def _normalize(target: str) -> str:
    handle = target.strip().rstrip("/")
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if handle.startswith(prefix):
            handle = handle[len(prefix) :]
            break
    return handle.lower()


def _dedupe(handles: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for handle in handles:
        if handle and handle not in seen:
            seen.add(handle)
            out.append(handle)
    return out
