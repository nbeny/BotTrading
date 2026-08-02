"""TelegramProvider: channel messages -> RawItem, with a fake MTProto client.

Telethon is never imported here: the provider takes its client *and* its error
classes from an injected factory, precisely so the library stays out of the test
environment (and out of CI, which installs no service dependencies).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_spec = importlib.util.spec_from_file_location(
    "telegram_provider",
    Path(__file__).resolve().parents[1]
    / "services"
    / "collector-social"
    / "app"
    / "providers"
    / "telegram.py",
)
tg = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = tg
_spec.loader.exec_module(tg)

from cmi_common.sources import TELEGRAM_SEED_CHANNELS, RateLimitedError  # noqa: E402

# --- fake telethon -------------------------------------------------------


class FloodWaitError(Exception):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"flood wait {seconds}")
        self.seconds = seconds


class ChannelPrivateError(Exception):
    pass


class UsernameInvalidError(Exception):
    pass


class UsernameNotOccupiedError(Exception):
    pass


ERRORS = SimpleNamespace(
    FloodWaitError=FloodWaitError,
    ChannelPrivateError=ChannelPrivateError,
    UsernameInvalidError=UsernameInvalidError,
    UsernameNotOccupiedError=UsernameNotOccupiedError,
)


def _msg(
    msg_id: int, text: str, *, views: int | None = 10, date: datetime | None = None
) -> Any:
    return SimpleNamespace(
        id=msg_id,
        message=text,
        views=views,
        date=date or datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    )


class FakeClient:
    """Minimal stand-in for telethon.TelegramClient."""

    def __init__(
        self,
        *,
        messages: dict[str, list[Any]] | None = None,
        entities: dict[str, Any] | None = None,
        resolve_error: Exception | None = None,
        authorized: bool = True,
    ) -> None:
        self._messages = messages or {}
        self._entities = entities or {}
        self._resolve_error = resolve_error
        self._authorized = authorized
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.resolved: list[str] = []
        self.disconnected = False
        # Counted, not just observed through their effects: "the session was
        # never opened" is the assertion an empty channel list has to make.
        self.connects = 0
        self.auth_checks = 0

    async def connect(self) -> None:
        self.connects += 1

    async def is_user_authorized(self) -> bool:
        self.auth_checks += 1
        return self._authorized

    async def disconnect(self) -> None:
        self.disconnected = True

    async def get_entity(self, handle: str) -> Any:
        self.resolved.append(handle)
        if self._resolve_error is not None:
            raise self._resolve_error
        return self._entities.get(
            handle, SimpleNamespace(id=-100 - len(handle), username=handle)
        )

    def iter_messages(self, entity: Any, **kwargs: Any) -> Any:
        handle = entity.username
        self.calls.append((handle, kwargs))
        msgs = sorted(self._messages.get(handle, []), key=lambda m: m.id, reverse=True)
        min_id = kwargs.get("min_id")
        if min_id:
            msgs = [m for m in msgs if m.id > min_id]
        msgs = msgs[: kwargs.get("limit") or len(msgs)]

        async def _gen() -> Any:
            for m in msgs:
                yield m

        return _gen()


class FakeCache:
    """Stand-in for cmi_common.cache.Cache holding `collectors:runtime`.

    Only the two methods `get_runtime` uses are implemented; `set_channels` is
    the operator editing the list from the terminal, mid-flight.
    """

    def __init__(self, channels: list[str]) -> None:
        self._store: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}
        self.set_channels(channels)

    def set_channels(self, channels: list[str]) -> None:
        self._store["collectors:runtime"] = {"telegram_channels": list(channels)}

    async def get_json(self, key: str) -> Any:
        return self._store.get(key)

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        self._store[key] = value
        self.ttls[key] = ttl_seconds

    def status(self) -> Any:
        return self._store.get("collectors:status:telegram")


def _provider(
    client: FakeClient,
    *,
    cache: Any | None = None,
    channels: list[str] | None = None,
    **kw: Any,
) -> Any:
    return tg.TelegramProvider(
        api_id=1,
        api_hash="hash",
        session="session",
        cache=cache or FakeCache(channels or ["alpha"]),
        client_factory=lambda: (client, ERRORS),
        **kw,
    )


# --- tests ---------------------------------------------------------------


async def test_maps_messages_and_leaves_symbols_to_the_normalizer() -> None:
    client = FakeClient(messages={"alpha": [_msg(7, "BTC long entry 65000")]})
    provider = _provider(client)

    items = await provider.fetch()
    await provider.close()

    assert len(items) == 1
    it = items[0]
    assert it.source == "telegram"
    assert it.kind == "social"
    assert it.external_id.endswith(":7")
    assert it.text == "BTC long entry 65000"
    assert it.url == "https://t.me/alpha/7"
    assert it.author == "alpha"
    assert it.engagement == 10.0
    assert it.published_at == datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    assert it.symbols == []  # the collector's normalizer owns symbol resolution


async def test_absent_views_stay_none_rather_than_zero() -> None:
    # A post with no view count is unmeasured, not unread. Scoring excludes an
    # absent axis but prices a 0.0 as worst-case evidence.
    client = FakeClient(messages={"alpha": [_msg(1, "call", views=None)]})
    provider = _provider(client)

    items = await provider.fetch()

    assert items[0].engagement is None


async def test_media_only_posts_are_skipped() -> None:
    client = FakeClient(messages={"alpha": [_msg(1, ""), _msg(2, "  "), _msg(3, "hi")]})
    provider = _provider(client)

    items = await provider.fetch()

    assert [i.external_id.split(":")[1] for i in items] == ["3"]


async def test_cursor_advances_so_the_next_cycle_only_pulls_new_posts() -> None:
    client = FakeClient(messages={"alpha": [_msg(4, "a"), _msg(5, "b")]})
    provider = _provider(client)

    first = await provider.fetch()
    client._messages["alpha"].append(_msg(6, "c"))
    second = await provider.fetch()

    assert len(first) == 2
    assert [i.external_id.split(":")[1] for i in second] == ["6"]
    # First cycle backfills; later cycles are bounded by min_id.
    assert "min_id" not in client.calls[0][1]
    assert client.calls[1][1]["min_id"] == 5


async def test_cursor_advances_past_media_only_posts() -> None:
    # The cursor tracks the highest id *seen*, not the highest id kept — an
    # image-only post must not be re-fetched forever.
    client = FakeClient(messages={"alpha": [_msg(9, "")]})
    provider = _provider(client)

    await provider.fetch()
    await provider.fetch()

    assert client.calls[1][1]["min_id"] == 9


async def test_flood_wait_becomes_rate_limited_with_telegrams_own_delay() -> None:
    client = FakeClient(resolve_error=FloodWaitError(42))
    provider = _provider(client)

    with pytest.raises(RateLimitedError) as exc:
        await provider.fetch()

    assert exc.value.retry_after == 42.0


@pytest.mark.parametrize(
    "error",
    [
        ChannelPrivateError(),
        UsernameInvalidError(),
        UsernameNotOccupiedError(),
        ValueError("nope"),
    ],
)
async def test_unresolvable_channel_is_dropped_without_killing_the_cycle(
    error: Exception,
) -> None:
    client = FakeClient(
        messages={"beta": [_msg(1, "still ingesting")]}, resolve_error=error
    )
    # Only `alpha` fails: the fake raises on every resolve, so give beta a
    # pre-resolved entity to prove the loop carries on past the bad one.
    provider = _provider(client, channels=["alpha", "beta"])
    provider._entities["beta"] = SimpleNamespace(id=-1002, username="beta")

    items = await provider.fetch()

    assert [i.author for i in items] == ["beta"]
    assert "alpha" in provider._dead
    # Second cycle must not spend another ResolveUsername call on the dead one.
    await provider.fetch()
    assert client.resolved == ["alpha"]


async def test_unauthorized_session_raises_instead_of_reporting_silence() -> None:
    client = FakeClient(authorized=False)
    provider = _provider(client)

    with pytest.raises(RuntimeError, match="TELEGRAM_SESSION"):
        await provider.fetch()
    assert client.disconnected


def test_parse_channels_normalizes_handles_and_drops_duplicates() -> None:
    parsed = tg.parse_channels(
        " @CoinBureau , https://t.me/CoinBureau, t.me/wublockchainenglish/ ,coinbureau"
    )
    assert parsed == ["coinbureau", "wublockchainenglish"]


def test_parse_channels_falls_back_to_the_shared_seed_list() -> None:
    # An empty seed would make the two assertions below pass vacuously.
    assert TELEGRAM_SEED_CHANNELS
    assert tg.parse_channels(None) == list(TELEGRAM_SEED_CHANNELS)
    assert tg.parse_channels("   ") == list(TELEGRAM_SEED_CHANNELS)


# --- the list is operator-editable, read fresh every cycle ----------------


async def test_editing_the_list_changes_which_channels_the_next_cycle_polls() -> None:
    # The whole point: no redeploy, no restart — the next cycle reads the key.
    client = FakeClient(messages={"alpha": [_msg(1, "a")], "beta": [_msg(2, "b")]})
    cache = FakeCache(["alpha"])
    provider = _provider(client, cache=cache)

    first = await provider.fetch()
    cache.set_channels(["beta"])
    second = await provider.fetch()

    assert [i.author for i in first] == ["alpha"]
    assert [i.author for i in second] == ["beta"]


async def test_an_empty_list_polls_nothing_at_all() -> None:
    client = FakeClient(messages={"alpha": [_msg(1, "a")]})
    provider = _provider(client, cache=FakeCache([]))

    items = await provider.fetch()

    assert items == []
    # No ResolveUsername, no history read: an empty list is "poll nobody", not
    # "fall back to whatever the process started with".
    assert client.resolved == []
    assert client.calls == []


async def test_removing_a_channel_clears_its_write_off_so_re_adding_retries() -> None:
    # Write-offs are permanent by design (see the unresolvable-channel test), but
    # only for a channel still on the list. Editing the list is the operator
    # saying "try this again" — otherwise a typo, once fixed, stays dead.
    client = FakeClient(resolve_error=UsernameInvalidError())
    cache = FakeCache(["alpha"])
    provider = _provider(client, cache=cache)

    await provider.fetch()
    assert set(provider._dead) == {"alpha"}

    cache.set_channels([])  # operator drops it
    await provider.fetch()
    assert provider._dead == {}

    cache.set_channels(["alpha"])  # ...and puts it back
    await provider.fetch()
    assert client.resolved == ["alpha", "alpha"]


# --- health key: the two failure modes that were log-only ------------------


async def test_a_completed_cycle_publishes_a_healthy_status() -> None:
    client = FakeClient(messages={"alpha": [_msg(1, "a")]})
    cache = FakeCache(["alpha"])
    provider = _provider(client, cache=cache)

    await provider.fetch()

    assert cache.status() == {"ok": True, "reason": None, "channels": {}}
    # Durable: an expiring "unhealthy" would read as healthy once it lapsed.
    assert cache.ttls[tg.STATUS_KEY] == 0


async def test_an_unauthorized_session_is_published_before_the_raise() -> None:
    # The whole point of the key: without it a revoked session is a log line
    # every 120s and nothing at all in the terminal.
    client = FakeClient(authorized=False)
    cache = FakeCache(["alpha"])
    provider = _provider(client, cache=cache)

    with pytest.raises(RuntimeError):
        await provider.fetch()

    status = cache.status()
    assert status["ok"] is False
    assert status["reason"]
    assert "TELEGRAM_SESSION" in status["reason"]


async def test_a_written_off_channel_is_published_with_its_reason() -> None:
    client = FakeClient(resolve_error=ChannelPrivateError())
    cache = FakeCache(["alpha"])
    provider = _provider(client, cache=cache)

    await provider.fetch()

    status = cache.status()
    # The cycle itself completed — one unreadable channel is not a source fault.
    assert status["ok"] is True
    assert status["reason"] is None
    assert "ChannelPrivateError" in status["channels"]["alpha"]


async def test_a_channel_taken_off_the_list_disappears_from_the_status() -> None:
    client = FakeClient(resolve_error=UsernameInvalidError("no user has alpha"))
    cache = FakeCache(["alpha"])
    provider = _provider(client, cache=cache)

    await provider.fetch()
    assert "alpha" in cache.status()["channels"]

    cache.set_channels(["beta"])
    provider._entities["beta"] = SimpleNamespace(id=-1002, username="beta")
    await provider.fetch()

    # Reporting a write-off for a handle the operator has removed would send
    # them looking for a channel that is no longer configured anywhere.
    assert cache.status()["channels"] == {}


async def test_an_empty_list_reports_health_without_opening_a_session() -> None:
    # A dead session plus an empty list used to raise every cycle while polling
    # nobody: there is nothing to authorize for.
    client = FakeClient(authorized=False)
    cache = FakeCache([])
    provider = _provider(client, cache=cache)

    items = await provider.fetch()

    assert items == []
    assert cache.status() == {"ok": True, "reason": None, "channels": {}}
    assert (client.connects, client.auth_checks) == (0, 0)
    assert client.resolved == []
