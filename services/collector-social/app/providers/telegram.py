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
from collections.abc import Callable
from typing import Any

from cmi_common.cache import Cache
from cmi_common.sources import (
    RateLimitedError,
    RawItem,
    get_runtime,
    source_status_key,
)

logger = logging.getLogger(__name__)

#: Messages pulled per channel on the first cycle (no cursor yet). Small on
#: purpose: this is a live signal, not an archive import.
BACKFILL = 20
#: Ceiling per channel per cycle once a cursor exists. A channel that outruns it
#: leaves a gap rather than stalling the whole fan-out on one firehose.
MAX_PER_CYCLE = 100
#: Telegram posts run to 4096 chars, mostly disclaimer boilerplate past the call.
MAX_TEXT = 4000
#: Source health, written by the provider and rendered in the terminal:
#: ``{"ok": bool, "reason": str | None, "channels": {handle: reason}}``.
#: Both faults it carries are otherwise invisible outside the container logs —
#: AdaptivePollLoop turns every exception into a warning and a 120s backoff, so
#: a revoked session or a written-off channel can run for weeks with nothing
#: reaching the operator who could fix it.
STATUS_KEY = source_status_key("telegram")
#: Kept short enough to render in a table cell — the raw telethon message is a
#: sentence that repeats the handle already shown in the same row.
MAX_REASON = 80


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
        cache: Cache,
        backfill: int = BACKFILL,
        max_per_cycle: int = MAX_PER_CYCLE,
        client_factory: Callable[[], tuple[Any, Any]] | None = None,
    ) -> None:
        # The channel list is operator-owned and read per cycle from Redis, not
        # frozen at construction: editing it in the terminal must take effect on
        # the next poll, without a redeploy.
        self._cache = cache
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
        # Handle -> why it was written off. The reason travels with the handle
        # rather than being logged and dropped: it is what the terminal shows,
        # and "this channel is contributing nothing" is not actionable without
        # it (renamed, gone private and banned all need different fixes).
        self._dead: dict[str, str] = {}

    async def close(self) -> None:
        if self._client is None:
            return
        await _disconnect(self._client)
        self._client = None

    async def fetch(self) -> list[RawItem]:
        channels = await self._active_channels()
        if not channels:
            # Before any session setup, not after: with nobody to poll, a dead
            # session denies us nothing, and raising on it would report a fault
            # every 120s for a source that is behaving exactly as configured.
            await self._publish_status(ok=True, reason=None)
            return []
        client, errors = await self._ensure_client()
        items: list[RawItem] = []
        for channel in channels:
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
                # ResolveUsername calls forever, so write it off until a restart
                # or until the operator takes it off the list (see
                # ``_active_channels``).
                self._dead[channel] = _reason(exc)
                logger.warning(
                    "telegram: dropping %s — %s: %s", channel, type(exc).__name__, exc
                )
        await self._publish_status(ok=True, reason=None)
        return items

    async def _active_channels(self) -> list[str]:
        """The list as the operator last saved it, re-read on every cycle.

        Entities and cursors are keyed by handle and survive an edit untouched —
        a channel taken off the list and put back resumes where it stopped. The
        write-offs cannot: dropping a handle is the operator saying "this one is
        settled", so re-adding it has to buy a fresh resolve attempt, otherwise a
        typo fixed in the terminal stays dead until the process restarts.

        A Redis error propagates and costs the cycle — fail-*closed*, deliberately
        unlike ``is_enabled``, which returns True on a read error so an outage
        cannot mute a source. A toggle has a safe default ("keep running"); a
        channel list has none. Nothing here holds a last-known list, and the only
        other candidate — the env seed — is exactly what the operator's edits
        replaced, so falling back would resurrect channels they had removed.
        """
        channels = (await get_runtime(self._cache))["telegram_channels"]
        active = set(channels)
        self._dead = {h: why for h, why in self._dead.items() if h in active}
        return list(channels)

    async def _publish_status(self, *, ok: bool, reason: str | None) -> None:
        """Publish source health for the terminal to read.

        Durable (``ttl_seconds=0``), like ``collectors:runtime``: an expiring
        fault would lapse back to "nothing reported" while still being broken,
        and the provider only rewrites this key when a cycle runs — which is
        exactly what a revoked session prevents.
        """
        await self._cache.set_json(
            STATUS_KEY,
            {"ok": ok, "reason": reason, "channels": dict(self._dead)},
            ttl_seconds=0,
        )

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
            # Published before the raise: the loop turns this into a warning and
            # a 120s backoff, so the exception itself never leaves the container.
            await self._publish_status(
                ok=False,
                reason="TELEGRAM_SESSION is missing or no longer authorized — "
                "mint a fresh one with `python scripts/telegram_session.py`",
            )
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


def _reason(exc: Exception) -> str:
    """Short, stable label for a write-off.

    The class name alone would collapse telethon's bare ``ValueError`` — the one
    raised for an unknown username — into something the operator cannot tell
    apart from any other lookup failure, so the message rides along, trimmed.
    """
    name = type(exc).__name__
    detail = str(exc).strip()
    return f"{name}: {detail[:MAX_REASON]}" if detail else name


async def _disconnect(client: Any) -> None:
    # Telethon returns a coroutine when the loop is running and None when it is
    # not; both are legal returns from the same call.
    result = client.disconnect()
    if inspect.isawaitable(result):
        await result
