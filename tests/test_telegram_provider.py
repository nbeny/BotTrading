"""TelegramProvider: channel messages -> RawItem, with a fake MTProto client.

Telethon is never imported here: the provider takes its client *and* its error
classes from an injected factory, precisely so the library stays out of the test
environment (and out of CI, which installs no service dependencies).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from service_modules import load_service_module

from cmi_common.sources import RateLimitedError

# The shared loader, not a hand-rolled `importlib` block: it registers the
# service's `app` package (and `app.providers`) under a per-service alias, so a
# relative import added to `telegram.py` tomorrow resolves against *this*
# service rather than whichever one happened to load its `app` first.
tg = load_service_module("collector-social", "providers.telegram")

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


class UnauthorizedError(Exception):
    """Telethon's 401 base class.

    Modelled with a subclass because that is the shape the provider relies on:
    ``AuthKeyUnregisteredError``, ``SessionRevokedError`` and
    ``UserDeactivatedError`` all inherit from it, and catching the base is what
    makes "the session died mid-life" detectable without naming each of them.
    """


class AuthKeyUnregisteredError(UnauthorizedError):
    pass


ERRORS = SimpleNamespace(
    FloodWaitError=FloodWaitError,
    ChannelPrivateError=ChannelPrivateError,
    UsernameInvalidError=UsernameInvalidError,
    UsernameNotOccupiedError=UsernameNotOccupiedError,
    UnauthorizedError=UnauthorizedError,
)


def _msg(
    msg_id: int,
    text: str,
    *,
    views: int | None = 10,
    date: datetime | None = None,
    **extra: Any,
) -> Any:
    """A message carrying only the attributes it is given.

    `forwards` and `reactions` go through ``**extra`` rather than being defaulted
    parameters on purpose: a real telethon message can be missing them entirely,
    and a fake that always sets them would never exercise the ``getattr``
    fallbacks the provider reads them through.
    """
    return SimpleNamespace(
        id=msg_id,
        message=text,
        views=views,
        date=date or datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
        **extra,
    )


def _reactions(*counts: int) -> Any:
    """Telethon's ``MessageReactions``: ``.results``, each row with a ``.count``.

    Called with no arguments it is the case the whole distinction turns on — a
    reactions block that *is* present and reports nothing.
    """
    return SimpleNamespace(results=[SimpleNamespace(count=c) for c in counts])


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
        # Setup faults, settable after construction. `connect` and
        # `is_user_authorized` are both network round trips, so both raise on an
        # ordinary flap — and neither has published the client anywhere yet.
        self.connect_error: Exception | None = None
        self.auth_error: Exception | None = None
        # Set *after* construction to break a client that already connected —
        # the only way to reproduce a fault that appears mid-life, which is the
        # case `_ensure_client`'s short-circuit makes invisible.
        self.fail_with: Exception | None = None
        # Per-channel history failures, for the case `fail_with` cannot express:
        # a cycle that reads some channels *successfully* and then aborts on a
        # later one. That asymmetry is the whole subject of the cursor tests.
        self.fail_channels: dict[str, Exception] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.resolved: list[str] = []
        self.disconnected = False
        # Counted, not just observed through their effects: "the session was
        # never opened" is the assertion an empty channel list has to make.
        self.connects = 0
        self.auth_checks = 0

    async def connect(self) -> None:
        self.connects += 1
        if self.connect_error is not None:
            raise self.connect_error

    async def is_user_authorized(self) -> bool:
        self.auth_checks += 1
        if self.auth_error is not None:
            raise self.auth_error
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
            if self.fail_with is not None:
                raise self.fail_with
            if handle in self.fail_channels:
                raise self.fail_channels[handle]
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


def _provider(client: FakeClient, *, cache: Any | None = None, **kw: Any) -> Any:
    return tg.TelegramProvider(
        api_id=1,
        api_hash="hash",
        session="session",
        cache=cache or FakeCache(["alpha"]),
        client_factory=lambda: (client, ERRORS),
        **kw,
    )


def _health(cache: Any) -> Any:
    """The status blob minus `updated_at`, which every publish moves.

    Pinning the rest by equality is what catches a field quietly appearing or
    disappearing from a payload control-api forwards verbatim.
    """
    return {k: v for k, v in cache.status().items() if k != "updated_at"}


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


# --- engagement: views + forwards + reactions, over what was reported -----


async def test_engagement_sums_every_counter_telegram_reported() -> None:
    # Views alone understate reach: a forward carries the call to another desk
    # and a reaction is the cheapest signal a reader can leave.
    client = FakeClient(
        messages={
            "alpha": [_msg(1, "call", views=10, forwards=3, reactions=_reactions(4, 2))]
        }
    )
    provider = _provider(client)

    items = await provider.fetch()

    assert items[0].engagement == 19.0


async def test_no_counter_at_all_is_not_measured_rather_than_zero() -> None:
    # A post is unmeasured here, not unread: views are absent on non-broadcast
    # peers, and the message carries neither `forwards` nor `reactions` at all.
    # `engagement` feeds `content_sentiment_agg.engagement_sum`, which weights
    # the social signal — a 0.0 is the claim "nobody engaged", and would drag
    # the weighted signal down on evidence Telegram never supplied.
    client = FakeClient(messages={"alpha": [_msg(1, "call", views=None)]})
    provider = _provider(client)

    items = await provider.fetch()

    assert items[0].engagement is None


async def test_one_present_counter_is_not_diluted_by_the_absent_ones() -> None:
    # An absent counter contributes nothing — it must not be summed in as a 0
    # and it must not drag the total to None either.
    client = FakeClient(messages={"alpha": [_msg(1, "call", views=None, forwards=7)]})
    provider = _provider(client)

    items = await provider.fetch()

    assert items[0].engagement == 7.0


async def test_a_present_but_empty_reactions_block_is_a_measured_zero() -> None:
    # The distinction the whole function exists for: `reactions is None` means
    # Telegram said nothing, while a block with an empty `results` is Telegram
    # saying "reactions are on and nobody used one" — a reading, not a silence.
    client = FakeClient(
        messages={"alpha": [_msg(1, "call", views=None, reactions=_reactions())]}
    )
    provider = _provider(client)

    items = await provider.fetch()

    # Zero, and specifically not None: `None == 0.0` is False, so this pins the
    # side of the distinction the block falls on with nothing else absent.
    assert items[0].engagement == 0.0


async def test_reactions_are_summed_across_every_kind() -> None:
    # `results` is one row per emoji; only their total is engagement.
    client = FakeClient(
        messages={"alpha": [_msg(1, "call", views=None, reactions=_reactions(1, 2, 3))]}
    )
    provider = _provider(client)

    items = await provider.fetch()

    assert items[0].engagement == 6.0


async def test_media_only_posts_are_skipped() -> None:
    client = FakeClient(messages={"alpha": [_msg(1, ""), _msg(2, "  "), _msg(3, "hi")]})
    provider = _provider(client)

    items = await provider.fetch()

    assert [i.external_id.split(":")[1] for i in items] == ["3"]


async def test_cursor_advances_so_the_next_cycle_only_pulls_new_posts() -> None:
    client = FakeClient(messages={"alpha": [_msg(4, "a"), _msg(5, "b")]})
    provider = _provider(client)

    first = await provider.fetch()
    await provider.commit()  # what AdaptivePollLoop does once they are stored
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
    await provider.commit()
    await provider.fetch()

    assert client.calls[1][1]["min_id"] == 9


# --- the cursor is a claim that the items were persisted ------------------
#
# Telegram is the only provider in this repo holding a consumption cursor.
# Every other one re-reads a window each cycle and lets
# UNIQUE(source, external_id) absorb the overlap — an argument that covers a
# restart but not an aborted cycle, because there the cursor moved forward while
# nothing was written. AdaptivePollLoop catches the exception and `continue`s
# without ever calling `insert_items`, so the items are discarded; if the cursor
# advanced anyway, the next cycle's `min_id` skips straight past them and they
# can never be fetched again. No exception, no log, no metric.


async def test_an_aborted_cycle_leaves_the_earlier_channels_cursors_untouched() -> None:
    # The reachable case: a FloodWaitError on channel k of 24, every 300s. The
    # channels walked *before* it were read into the local list and are about to
    # be thrown away with the exception — their cursors must not have moved.
    client = FakeClient(
        messages={"alpha": [_msg(1, "a"), _msg(2, "b")], "beta": [_msg(5, "c")]}
    )
    client.fail_channels["beta"] = FloodWaitError(30)
    provider = _provider(client, cache=FakeCache(["alpha", "beta"]))

    with pytest.raises(RateLimitedError):
        await provider.fetch()  # the loop discards whatever `fetch` had read

    client.fail_channels.clear()
    items = await provider.fetch()

    # Both of alpha's messages come back. Nothing was persisted, so nothing may
    # have been skipped.
    assert sorted(i.external_id.split(":")[1] for i in items) == ["1", "2", "5"]
    assert "min_id" not in client.calls[-2][1]


async def test_a_mid_cycle_unauthorized_leaves_the_earlier_cursors_untouched() -> None:
    # Same shape, different abort: the session is revoked from the phone while
    # the walk is halfway through. `fetch` raises, the loop discards the items.
    client = FakeClient(messages={"alpha": [_msg(1, "a")], "beta": [_msg(2, "b")]})
    client.fail_channels["beta"] = AuthKeyUnregisteredError("revoked")
    provider = _provider(client, cache=FakeCache(["alpha", "beta"]))

    with pytest.raises(RuntimeError, match="TELEGRAM_SESSION"):
        await provider.fetch()

    client.fail_channels.clear()
    items = await provider.fetch()

    assert sorted(i.external_id.split(":")[1] for i in items) == ["1", "2"]


async def test_a_cycle_whose_items_were_never_persisted_is_read_again() -> None:
    # The half part (a) cannot reach: `fetch` returns cleanly, the loop calls
    # `insert_items`, and *that* raises — a transient DB failure, which
    # AdaptivePollLoop's own comment documents as an expected path. The provider
    # never hears about it, so the advance has to stay pending until it does.
    client = FakeClient(messages={"alpha": [_msg(1, "a"), _msg(2, "b")]})
    provider = _provider(client)

    first = await provider.fetch()  # loop: insert_items raises -> no commit()
    second = await provider.fetch()

    assert [i.external_id for i in first] == [i.external_id for i in second]
    assert "min_id" not in client.calls[1][1]


async def test_commit_is_what_advances_the_cursor() -> None:
    # The other side of the same seam: once the loop confirms the items are
    # durable, the next cycle must not re-read them.
    client = FakeClient(messages={"alpha": [_msg(1, "a"), _msg(2, "b")]})
    provider = _provider(client)

    await provider.fetch()
    await provider.commit()
    second = await provider.fetch()

    assert second == []
    assert client.calls[1][1]["min_id"] == 2


async def test_committing_a_cycle_does_not_replay_an_earlier_discarded_one() -> None:
    # A channel read *successfully* in a discarded cycle, then dropped from the
    # list, has nothing persisted. Its stale advance must not ride along on the
    # commit of a later, unrelated cycle: re-adding the channel would then skip
    # exactly the posts that were thrown away. Only the walk that produced the
    # committed items may advance anything — the pending set is replaced by each
    # cycle, not accumulated across them.
    client = FakeClient(
        messages={"alpha": [_msg(1, "a")], "beta": [_msg(7, "b")], "gamma": []}
    )
    cache = FakeCache(["alpha", "beta", "gamma"])
    provider = _provider(client, cache=cache)

    client.fail_channels["gamma"] = FloodWaitError(5)
    with pytest.raises(RateLimitedError):
        await provider.fetch()  # alpha and beta were read, then discarded

    client.fail_channels.clear()
    cache.set_channels(["alpha"])  # operator drops beta and gamma
    await provider.fetch()
    await provider.commit()  # commits alpha only

    cache.set_channels(["beta"])  # ...and puts beta back
    items = await provider.fetch()

    assert [i.external_id.split(":")[1] for i in items] == ["7"]


async def test_a_cycle_that_polls_nobody_commits_nothing() -> None:
    # The early return for an empty list is still a cycle the loop commits: it
    # persists `[]` successfully and calls `commit()`. Advances left pending by
    # a *discarded* cycle must not be applied by it — clearing the list is the
    # likeliest way to reach this, and re-adding the channel afterwards would
    # then skip exactly the posts that were thrown away.
    client = FakeClient(messages={"alpha": [_msg(3, "a")]})
    cache = FakeCache(["alpha"])
    provider = _provider(client, cache=cache)

    await provider.fetch()  # completes, staging alpha -> 3...
    # ...and the loop's `insert_items` raises, so `commit` is never called.

    cache.set_channels([])  # operator clears the list
    assert await provider.fetch() == []
    await provider.commit()

    cache.set_channels(["alpha"])
    items = await provider.fetch()

    assert [i.external_id.split(":")[1] for i in items] == ["3"]


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
    cache = FakeCache(["alpha", "beta"])
    provider = _provider(client, cache=cache)
    provider._entities["beta"] = SimpleNamespace(id=-1002, username="beta")

    items = await provider.fetch()

    assert [i.author for i in items] == ["beta"]
    assert "alpha" in cache.status()["channels"]
    # Second cycle must not spend another ResolveUsername call on the dead one.
    await provider.fetch()
    assert client.resolved == ["alpha"]


# --- client setup hangs up on every path out ------------------------------
#
# `_ensure_client` publishes to `self._client` only on the very last line, so
# anything raising before that leaves a client `_drop_client` cannot reach. The
# loop backs off 120s and builds another; over a multi-hour outage that is
# hundreds of dangling clients and sockets.


async def test_a_failed_connect_is_hung_up_rather_than_stranded() -> None:
    client = FakeClient(messages={"alpha": [_msg(1, "a")]})
    client.connect_error = ConnectionResetError("connection reset by peer")
    provider = _provider(client)

    with pytest.raises(ConnectionResetError):
        await provider.fetch()

    assert client.disconnected


async def test_an_authorization_check_that_raises_is_hung_up_too() -> None:
    # The one that actually happens: `is_user_authorized` is an MTProto round
    # trip, so a network flap raises here routinely — and here the client is
    # already *connected*, which the `returned False` path below hangs up but
    # the raising path did not.
    client = FakeClient(messages={"alpha": [_msg(1, "a")]})
    client.auth_error = TimeoutError("no response from the datacenter")
    provider = _provider(client)

    with pytest.raises(TimeoutError):
        await provider.fetch()

    assert client.disconnected


async def test_the_next_cycle_builds_a_fresh_client_after_a_setup_failure() -> None:
    # Nothing was cached, so the recovery path is a rebuild — the same shape as
    # the mid-life revocation, reached without ever having a usable client.
    client = FakeClient(messages={"alpha": [_msg(1, "a")]})
    client.auth_error = TimeoutError("no response from the datacenter")
    provider = _provider(client)

    with pytest.raises(TimeoutError):
        await provider.fetch()

    client.auth_error = None
    items = await provider.fetch()

    assert [i.author for i in items] == ["alpha"]
    assert (client.connects, client.auth_checks) == (2, 2)


async def test_unauthorized_session_raises_instead_of_reporting_silence() -> None:
    client = FakeClient(authorized=False)
    provider = _provider(client)

    with pytest.raises(RuntimeError, match="TELEGRAM_SESSION"):
        await provider.fetch()
    assert client.disconnected


# `parse_channels` and the handle normalization it builds on now live in
# `cmi_common.sources.runtime` — control-api validates operator input with the
# same rules and may not import a collector. Their tests moved with them, to
# `tests/test_collector_runtime.py`.


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
    assert set(cache.status()["channels"]) == {"alpha"}

    cache.set_channels([])  # operator drops it
    await provider.fetch()
    assert cache.status()["channels"] == {}

    cache.set_channels(["alpha"])  # ...and puts it back
    await provider.fetch()
    assert client.resolved == ["alpha", "alpha"]


# --- health key: every fault that was otherwise log-only -------------------


async def test_a_cycle_that_read_every_channel_publishes_a_healthy_status() -> None:
    client = FakeClient(messages={"alpha": [_msg(1, "a")]})
    cache = FakeCache(["alpha"])
    provider = _provider(client, cache=cache)

    await provider.fetch()

    assert _health(cache) == {"ok": True, "reason": None, "channels": {}}
    # Durable: an expiring "unhealthy" would read as healthy once it lapsed.
    assert cache.ttls[tg.STATUS_KEY] == 0


async def test_every_publish_stamps_when_the_source_was_last_measured() -> None:
    # The operator can disable the platform from the terminal, at which point the
    # poll loop skips `fetch` entirely and this key serves whatever it last said,
    # forever. A frozen key has to be visibly frozen: without `updated_at` a
    # consumer cannot tell "healthy 30s ago" from "last healthy in March".
    client = FakeClient(messages={"alpha": [_msg(1, "a")]})
    cache = FakeCache(["alpha"])
    provider = _provider(client, cache=cache)

    await provider.fetch()

    stamp = datetime.fromisoformat(cache.status()["updated_at"])
    assert stamp.tzinfo is not None  # naive would be unreadable across timezones
    assert abs((datetime.now(UTC) - stamp).total_seconds()) < 60


async def test_a_session_revoked_mid_life_flips_the_key_and_rebuilds_the_client() -> (
    None
):
    # The paradigm case: the session is killed from the phone's active-sessions
    # screen *after* the process connected. `_ensure_client` short-circuits on the
    # cached client, so nothing re-checks `is_user_authorized` and the auth error
    # surfaces from the history read instead — where it used to escape unpublished
    # and leave the last success standing for as long as the fault lasted.
    client = FakeClient(messages={"alpha": [_msg(1, "a")]})
    cache = FakeCache(["alpha"])
    provider = _provider(client, cache=cache)

    await provider.fetch()
    assert cache.status()["ok"] is True

    client.fail_with = AuthKeyUnregisteredError("the key is not registered")
    with pytest.raises(RuntimeError, match="TELEGRAM_SESSION"):
        await provider.fetch()

    status = cache.status()
    assert status["ok"] is False
    assert "TELEGRAM_SESSION" in status["reason"]

    # The dead client is dropped, so the next cycle rebuilds and re-runs
    # `is_user_authorized` — the only check that can name what actually happened.
    client.fail_with = None
    await provider.fetch()
    assert (client.connects, client.auth_checks) == (2, 2)


async def test_any_failed_cycle_replaces_the_success_it_would_otherwise_leave() -> None:
    # Not just the errors we can name: AdaptivePollLoop turns *every* exception
    # into a log warning and a 120s backoff, which is exactly the invisibility
    # this key exists to end.
    client = FakeClient(messages={"alpha": [_msg(1, "a")]})
    cache = FakeCache(["alpha"])
    provider = _provider(client, cache=cache)

    await provider.fetch()
    client.fail_with = ConnectionResetError("connection reset by peer")
    with pytest.raises(ConnectionResetError):
        await provider.fetch()

    status = cache.status()
    assert status["ok"] is False
    assert "ConnectionResetError" in status["reason"]


async def test_an_unreadable_channel_list_is_reported_rather_than_swallowed() -> None:
    # `_configured_channels` fails closed on a Redis error, which costs the cycle.
    # The failure must still reach the terminal: the publish goes through the same
    # cache, so it is best-effort and must not replace the original exception.
    class _ReadBroken(FakeCache):
        async def get_json(self, key: str) -> Any:
            raise ConnectionRefusedError("redis is down")

    cache = _ReadBroken(["alpha"])
    provider = _provider(FakeClient(), cache=cache)

    with pytest.raises(ConnectionRefusedError):
        await provider.fetch()

    assert cache.status()["ok"] is False


async def test_a_flood_wait_refreshes_the_key_without_reporting_a_fault() -> None:
    # Telegram working as designed: the loop pauses and resumes this same
    # provider. Red here would be a lie — but so would a success nothing restamps.
    client = FakeClient(resolve_error=FloodWaitError(42))
    cache = FakeCache(["alpha"])
    provider = _provider(client, cache=cache)

    with pytest.raises(RateLimitedError):
        await provider.fetch()

    status = cache.status()
    assert status["ok"] is True
    assert "42" in status["reason"]
    assert status["updated_at"]


async def test_a_source_with_every_channel_written_off_is_not_healthy() -> None:
    # One dead channel among many is a channel fault; all of them dead means the
    # source contributes nothing, and the terminal drives a light off `ok`.
    client = FakeClient(resolve_error=ChannelPrivateError())
    cache = FakeCache(["alpha", "beta"])
    provider = _provider(client, cache=cache)

    await provider.fetch()

    status = cache.status()
    assert status["ok"] is False
    assert "unreadable" in status["reason"]
    assert set(status["channels"]) == {"alpha", "beta"}


async def test_a_malformed_message_does_not_write_off_a_healthy_channel() -> None:
    # `except ValueError` used to span the whole channel, so one message with a
    # junk id wrote a working channel off for the life of the process. Only a
    # resolution failure may; a malformed message is a bug to surface.
    junk = SimpleNamespace(id="not-an-int", message="hi", views=1, date=None)
    cache = FakeCache(["alpha"])
    provider = _provider(FakeClient(messages={"alpha": [junk]}), cache=cache)

    with pytest.raises(ValueError):
        await provider.fetch()

    assert cache.status()["channels"] == {}


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
    client = FakeClient(
        messages={"beta": [_msg(1, "still ingesting")]},
        resolve_error=ChannelPrivateError(),
    )
    cache = FakeCache(["alpha", "beta"])
    provider = _provider(client, cache=cache)
    provider._entities["beta"] = SimpleNamespace(id=-1002, username="beta")

    await provider.fetch()

    status = cache.status()
    # One unreadable channel out of two is a channel fault, not a source fault:
    # the rest of the desk is still ingesting.
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
    # Green, but the reason says *which* green it is: polling nobody is a valid
    # operator choice, not evidence that ingestion works.
    assert _health(cache) == {
        "ok": True,
        "reason": "no channels configured",
        "channels": {},
    }
    assert (client.connects, client.auth_checks) == (0, 0)
    assert client.resolved == []
