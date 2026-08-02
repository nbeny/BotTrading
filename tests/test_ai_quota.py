"""ClaudeClient quota pause/resume loop.

On a subscription usage limit the transport returns quota_exceeded=True; the
client must pause until the reported reset, publish status to the cache, then
retry — never returning the empty quota response to the caller.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "libs" / "cmi_common"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from cmi_common.ai.claude import ClaudeClient, ClaudeResponse  # noqa: E402


class _FakeTransport:
    """Returns quota_exceeded for the first N calls, then a real response."""

    def __init__(self, quota_times: int, reset_at: int | None) -> None:
        self.calls = 0
        self._quota_times = quota_times
        self._reset_at = reset_at

    async def complete(self, *, system, prompt, service) -> ClaudeResponse:
        self.calls += 1
        if self.calls <= self._quota_times:
            return ClaudeResponse(text="", quota_exceeded=True, reset_at=self._reset_at)
        return ClaudeResponse(text='{"ok": 1}', output_tokens=3)


class _FakeCache:
    def __init__(self) -> None:
        self.writes: list[dict] = []

    async def set_json(self, key, value, ttl_seconds=60) -> None:
        self.writes.append({"key": key, **value})


def _client(transport, cache) -> ClaudeClient:
    c = ClaudeClient("", "claude-haiku-4-5-20251001", cache=cache)
    c._transport = transport
    return c


async def test_pauses_then_resumes(monkeypatch) -> None:
    slept: list[float] = []

    async def _fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("cmi_common.ai.claude.asyncio.sleep", _fake_sleep)
    monkeypatch.setattr("cmi_common.ai.claude.time.time", lambda: 1_000_000)

    transport = _FakeTransport(quota_times=1, reset_at=1_000_060)  # +60s
    cache = _FakeCache()
    resp = await _client(transport, cache).complete(
        system="s", prompt="p", service="ai-worker-haiku"
    )

    assert resp.text == '{"ok": 1}'  # real response, not the empty quota one
    assert transport.calls == 2  # one quota hit, one successful retry
    assert slept == [60]  # slept exactly until the reported reset
    assert cache.writes[0]["paused"] is True
    assert cache.writes[0]["resume_at"] == 1_000_060
    assert cache.writes[-1]["paused"] is False  # status cleared on resume


async def test_cooldown_when_no_reset_stamp(monkeypatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(
        "cmi_common.ai.claude.asyncio.sleep",
        lambda s: slept.append(s) or _noop(),
    )
    monkeypatch.setattr("cmi_common.ai.claude.time.time", lambda: 0)

    transport = _FakeTransport(quota_times=1, reset_at=None)
    client = ClaudeClient("", "m", cache=None, quota_cooldown_s=900)
    client._transport = transport
    resp = await client.complete(system="s", prompt="p", service="svc")

    assert resp.text == '{"ok": 1}'
    assert slept == [900]  # fell back to the configured cooldown


async def _noop() -> None:
    return None
