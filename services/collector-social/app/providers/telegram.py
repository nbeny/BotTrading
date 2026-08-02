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
from datetime import UTC, datetime
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
#: ``{"ok": bool, "reason": str | None, "channels": {handle: reason},
#: "updated_at": iso8601}``.
#: The faults it carries are otherwise invisible outside the container logs —
#: AdaptivePollLoop turns every exception into a warning and a 120s backoff, so
#: a revoked session or a written-off channel can run for weeks with nothing
#: reaching the operator who could fix it.
#: ``updated_at`` is what separates "measured healthy a minute ago" from a key
#: frozen since March: the key is durable, the loop skips ``fetch`` entirely
#: while the operator has the platform switched off, and a stale success is
#: otherwise indistinguishable from a live one.
STATUS_KEY = source_status_key("telegram")
#: Bounds the *detail* appended to the exception class name, not the emitted
#: string: a reason runs to ``len(type(exc).__name__) + 2 + MAX_REASON_DETAIL``.
#: Kept short enough to render in a table cell — the raw telethon message is a
#: sentence that repeats the handle already shown in the same row.
MAX_REASON_DETAIL = 80
#: Operator-facing text for a session that cannot be repaired from in here.
#: Published verbatim rather than through ``_reason``, whose truncation would cut
#: off the only part that says how to fix it.
SESSION_REASON = (
    "TELEGRAM_SESSION is missing or no longer authorized — "
    "mint a fresh one with `python scripts/telegram_session.py`"
)


class TelegramSessionError(RuntimeError):
    """The session is gone: missing, expired, or revoked from another device.

    Held apart from a generic cycle failure because nothing this process can do
    repairs it — a human has to mint a new session — and because its status
    reason is curated rather than derived from the exception.
    """


class UnresolvableChannelError(Exception):
    """A handle this account cannot resolve, carrying its already-built reason.

    Exists only to scope telethon's bare ``ValueError`` — the one it raises for
    an unknown username — to the resolve call. ``_fetch_channel`` runs ``int``
    and ``float`` over message fields and raises that same class for an unrelated
    reason, so catching ``ValueError`` around a whole channel wrote a healthy
    channel off for the life of the process over one malformed message.
    """


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
        # Handle -> why it was written off; see `_write_off`.
        self._dead: dict[str, str] = {}

    async def close(self) -> None:
        await self._drop_client()

    async def fetch(self) -> list[RawItem]:
        """One poll cycle, whose outcome is always published before it returns.

        The publish-on-the-way-out is the point of this wrapper. AdaptivePollLoop
        turns any exception into a log warning and a 120s backoff, so a cycle that
        raised used to leave the previous payload standing: a session revoked from
        the phone hours ago still read ``ok: true``. Every exception counts, not
        just the ones we can name — the failure mode is the one nobody predicted.
        """
        try:
            return await self._poll_cycle()
        except RateLimitedError as exc:
            # The one exception. A flood wait is Telegram working as designed:
            # the loop pauses and resumes this same provider, so red here would
            # be a lie. Re-stamped rather than left alone, because a *silently*
            # stale success is the other half of the same defect.
            await self._try_publish_status(ok=True, reason=_flood_reason(exc))
            raise
        except Exception as exc:
            reason = str(exc) if isinstance(exc, TelegramSessionError) else _reason(exc)
            await self._try_publish_status(ok=False, reason=reason)
            raise

    async def _poll_cycle(self) -> list[RawItem]:
        channels = await self._configured_channels()
        self._expire_write_offs(channels)
        if not channels:
            # Before any session setup, not after: with nobody to poll, a dead
            # session denies us nothing, and raising on it would report a fault
            # every 120s for a source that is behaving exactly as configured.
            await self._publish_status(ok=True, reason="no channels configured")
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
            except errors.UnauthorizedError as exc:
                # The session died *after* we connected — revoked from the phone's
                # active-sessions screen, most often. `_ensure_client` short-
                # circuits on the cached client and would never re-check it, so
                # drop it here: only a rebuild re-runs `is_user_authorized`, and
                # only that can tell the operator what actually happened.
                await self._drop_client()
                raise TelegramSessionError(SESSION_REASON) from exc
            except UnresolvableChannelError as exc:
                self._write_off(channel, str(exc))
            except (
                errors.ChannelPrivateError,
                errors.UsernameInvalidError,
                errors.UsernameNotOccupiedError,
            ) as exc:
                # Reachable while reading history too, not only while resolving:
                # a channel can go private between two cycles.
                self._write_off(channel, _reason(exc))
        ok, reason = self._verdict(channels)
        await self._publish_status(ok=ok, reason=reason)
        return items

    async def _configured_channels(self) -> list[str]:
        """The list as the operator last saved it, re-read on every cycle.

        A Redis error propagates and costs the cycle — fail-*closed*, deliberately
        unlike ``is_enabled``, which returns True on a read error so an outage
        cannot mute a source. A toggle has a safe default ("keep running"); a
        channel list has none. Nothing here holds a last-known list, and the only
        other candidate — the env seed — is exactly what the operator's edits
        replaced, so falling back would resurrect channels they had removed.
        """
        return list((await get_runtime(self._cache))["telegram_channels"])

    def _expire_write_offs(self, channels: list[str]) -> None:
        """Forget the write-offs of handles no longer on the list.

        Entities and cursors are keyed by handle and survive an edit untouched —
        a channel taken off the list and put back resumes where it stopped. The
        write-offs cannot: dropping a handle is the operator saying "this one is
        settled", so re-adding it has to buy a fresh resolve attempt, otherwise a
        typo fixed in the terminal stays dead until the process restarts.
        """
        active = set(channels)
        self._dead = {h: why for h, why in self._dead.items() if h in active}

    def _write_off(self, channel: str, reason: str) -> None:
        """Stop polling a channel this account cannot read, with the why attached.

        Unresolvable for *this* account: renamed, banned, or invite-only and we
        are not a member. Retrying every cycle would spend ResolveUsername calls
        forever, so it is written off until a restart or until the operator takes
        it off the list (see ``_expire_write_offs``). The reason travels with the
        handle rather than being logged and dropped: it is what the terminal
        shows, and "this channel is contributing nothing" is not actionable
        without it — renamed, gone private and banned need different fixes.
        """
        self._dead[channel] = reason
        logger.warning("telegram: dropping %s — %s", channel, reason)

    def _verdict(self, channels: list[str]) -> tuple[bool, str | None]:
        """Source-level health, aggregated over the per-channel write-offs.

        One dead channel among many is a channel fault and stays green — the rest
        of the desk is still ingesting. *Every* channel dead is a source fault:
        nothing is coming in, and the terminal drives its indicator off ``ok``.
        An empty list never reaches here; polling nobody is a valid choice.
        """
        dead = sum(1 for c in channels if c in self._dead)
        if dead == len(channels):
            return False, f"all {dead} configured channels are unreadable"
        return True, None

    async def _publish_status(self, *, ok: bool, reason: str | None) -> None:
        """Publish source health for the terminal to read.

        Durable (``ttl_seconds=0``), like ``collectors:runtime``: an expiring
        fault would lapse back to "nothing reported" while still being broken.
        That durability is exactly why ``updated_at`` has to ride along — the key
        outlives the cycle that wrote it, and the loop stops writing altogether
        while the operator has the platform switched off.
        """
        await self._cache.set_json(
            STATUS_KEY,
            {
                "ok": ok,
                "reason": reason,
                "channels": dict(self._dead),
                "updated_at": datetime.now(UTC).isoformat(),
            },
            ttl_seconds=0,
        )

    async def _try_publish_status(self, *, ok: bool, reason: str | None) -> None:
        """Best-effort publish from a failure path.

        The cycle has already failed and is about to re-raise. A second failure
        out of the same Redis that probably caused the first would replace the
        exception the operator actually needs to see.
        """
        try:
            await self._publish_status(ok=ok, reason=reason)
        except Exception:
            logger.warning("telegram: could not publish source health", exc_info=True)

    async def _ensure_client(self) -> tuple[Any, Any]:
        if self._client is not None and self._errors is not None:
            return self._client, self._errors
        client, errors = self._factory()
        await client.connect()
        if not await client.is_user_authorized():
            # Drop the socket here, not via `_drop_client`: the failed client was
            # never published to self._client, so that would have nothing to hang
            # up and every backed-off retry would leak a connection.
            await _disconnect(client)
            # Loud on every cycle rather than an empty fetch: a silent [] here
            # reads downstream as "Telegram had nothing to say", which is a
            # different claim from "Telegram was never asked". ``fetch`` publishes
            # the fault on the way out.
            raise TelegramSessionError(SESSION_REASON)
        self._client, self._errors = client, errors
        return client, errors

    async def _drop_client(self) -> None:
        """Hang up and forget the client so the next cycle rebuilds it.

        A connection that dies mid-life is otherwise never replaced, and
        ``_ensure_client`` short-circuits on it: the loop would back off forever
        against a client it will never re-authorize.
        """
        client, self._client, self._errors = self._client, None, None
        if client is not None:
            await _disconnect(client)

    @staticmethod
    async def _resolve(client: Any, channel: str) -> Any:
        """Handle -> entity, with telethon's bare ``ValueError`` scoped to here.

        Telethon raises a plain ``ValueError`` ("no user has X as username") for
        an unknown handle, and that has to write the channel off. Nothing else in
        a cycle may: ``_fetch_channel`` runs ``int`` and ``float`` over message
        fields and raises the same class when a message is malformed, which is a
        bug to surface, not a channel to abandon for the life of the process.
        """
        try:
            return await client.get_entity(channel)
        except ValueError as exc:
            raise UnresolvableChannelError(_reason(exc)) from exc

    async def _fetch_channel(self, client: Any, channel: str) -> list[RawItem]:
        entity = self._entities.get(channel)
        if entity is None:
            entity = await self._resolve(client, channel)
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
    return f"{name}: {detail[:MAX_REASON_DETAIL]}" if detail else name


def _flood_reason(exc: RateLimitedError) -> str:
    """Say how long the pause is, when Telegram bothered to say."""
    wait = exc.retry_after
    if wait is None:
        return "rate-limited by Telegram"
    return f"rate-limited by Telegram, resuming in {wait:.0f}s"


async def _disconnect(client: Any) -> None:
    # Telethon returns a coroutine when the loop is running and None when it is
    # not; both are legal returns from the same call.
    result = client.disconnect()
    if inspect.isawaitable(result):
        await result
