# tests/test_cli_transport.py
"""Dispatcher + CliTransport behavior for the AI workers."""

from __future__ import annotations

import asyncio
import json

import pytest


def test_ai_cli_calls_metric_exists() -> None:
    from cmi_common.observability.metrics import AI_CLI_CALLS

    # Labels: service, model, outcome — increment must not raise.
    AI_CLI_CALLS.labels("svc", "haiku", "success").inc()
    assert AI_CLI_CALLS.labels("svc", "haiku", "success")._value.get() >= 1


def test_no_key_uses_stub() -> None:
    from cmi_common.ai import ClaudeClient

    client = ClaudeClient(api_key="", model="claude-haiku-4-5")
    resp = asyncio.run(
        client.complete(system="s", prompt="hello", service="svc")
    )
    data = resp.json()
    assert "opportunity_score" in data
    assert "offline-stub" in data["reason"]


def test_response_json_strips_code_fence() -> None:
    from cmi_common.ai import ClaudeResponse

    resp = ClaudeResponse(text='```json\n{"a": 1}\n```')
    assert resp.json() == {"a": 1}


def test_cli_options_importable() -> None:
    from cmi_common.ai import CliOptions

    opts = CliOptions()
    assert opts.cli_path == "claude"
    assert opts.timeout_ms == 120000
    assert opts.concurrency == 4


class FakeProc:
    """Stand-in for an asyncio subprocess."""

    def __init__(self, out: bytes = b"", err: bytes = b"", returncode: int = 0,
                 delay: float = 0.0) -> None:
        self._out, self._err = out, err
        self.returncode = returncode
        self._delay = delay
        self.killed = False
        self.sent_stdin: bytes | None = None

    async def communicate(self, stdin: bytes | None = None):
        self.sent_stdin = stdin
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._out, self._err

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def _patch_exec(monkeypatch, proc: FakeProc, captured: dict) -> None:
    async def _fake(*argv, stdin=None, stdout=None, stderr=None, cwd=None, env=None):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
        captured["env"] = env
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake)


def test_cli_argv_and_stdin(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    envelope = json.dumps({"result": '{"opportunity_score": 80}',
                           "usage": {"input_tokens": 10, "output_tokens": 5}})
    proc = FakeProc(out=envelope.encode())
    captured: dict = {}
    _patch_exec(monkeypatch, proc, captured)

    t = CliTransport("claude-haiku-4-5", CliOptions())
    resp = asyncio.run(t.complete(system="SYS", prompt="PROMPT", service="svc"))

    argv = captured["argv"]
    assert argv[0] == "claude"
    assert "-p" in argv
    assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json"
    # Independence: never continue or resume a session.
    assert "--continue" not in argv
    assert "--resume" not in argv
    # System prompt override: replace persona + drop dynamic sections.
    assert argv[argv.index("--system-prompt") + 1] == "SYS"
    assert "--exclude-dynamic-system-prompt-sections" in argv
    assert "--append-system-prompt" not in argv
    assert "--dangerously-skip-permissions" not in argv
    assert proc.sent_stdin == b"PROMPT"
    # Isolated scratch cwd per call, cleaned up afterwards.
    import os

    assert captured["cwd"] and captured["cwd"] != os.getcwd()
    assert not os.path.exists(captured["cwd"])
    # Envelope parsed, tokens surfaced.
    assert resp.json() == {"opportunity_score": 80}
    assert resp.input_tokens == 10
    assert resp.output_tokens == 5


def _outcome_count(service: str, model: str, outcome: str) -> float:
    from cmi_common.observability.metrics import AI_CLI_CALLS

    return AI_CLI_CALLS.labels(service, model, outcome)._value.get()


def test_cli_timeout_degrades(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    proc = FakeProc(out=b"", delay=1.0)  # sleeps well past the timeout
    _patch_exec(monkeypatch, proc, {})

    before = _outcome_count("svc", "m", "timeout")
    t = CliTransport("m", CliOptions(timeout_ms=10))  # 10ms timeout
    resp = asyncio.run(t.complete(system="s", prompt="p", service="svc"))

    assert resp.text == ""
    assert proc.killed is True
    assert _outcome_count("svc", "m", "timeout") == before + 1


def test_cli_nonzero_exit_degrades(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    proc = FakeProc(out=b"", err=b"boom", returncode=1)
    _patch_exec(monkeypatch, proc, {})

    before = _outcome_count("svc", "m", "error")
    t = CliTransport("m", CliOptions())
    resp = asyncio.run(t.complete(system="s", prompt="p", service="svc"))

    assert resp.text == ""
    assert _outcome_count("svc", "m", "error") == before + 1


def test_cli_quota_classified(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    proc = FakeProc(out=b"", err=b"Claude usage limit reached", returncode=1)
    _patch_exec(monkeypatch, proc, {})

    before = _outcome_count("svc", "m", "quota")
    t = CliTransport("m", CliOptions())
    asyncio.run(t.complete(system="s", prompt="p", service="svc"))

    assert _outcome_count("svc", "m", "quota") == before + 1


def test_cli_missing_binary_degrades(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    async def _boom(*a, **k):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)

    before = _outcome_count("svc", "m", "error")
    t = CliTransport("m", CliOptions())
    resp = asyncio.run(t.complete(system="s", prompt="p", service="svc"))

    assert resp.text == ""
    assert _outcome_count("svc", "m", "error") == before + 1


def test_semaphore_caps_parallelism(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    state = {"concurrent": 0, "peak": 0}
    release = asyncio.Event()

    class GateProc:
        returncode = 0

        async def communicate(self, stdin=None):
            state["concurrent"] += 1
            state["peak"] = max(state["peak"], state["concurrent"])
            await release.wait()
            state["concurrent"] -= 1
            return b'{"result": "{}", "usage": {}}', b""

        def kill(self) -> None:  # pragma: no cover
            ...

        async def wait(self) -> int:  # pragma: no cover
            return 0

    async def _fake(*a, **k):
        return GateProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake)

    async def scenario() -> None:
        t = CliTransport("m", CliOptions(concurrency=2, timeout_ms=5000))
        tasks = [
            asyncio.create_task(t.complete(system="s", prompt="p", service="svc"))
            for _ in range(5)
        ]
        await asyncio.sleep(0.05)      # let as many as allowed enter
        assert state["peak"] <= 2      # never more than the cap
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert state["peak"] == 2          # the cap was actually reached


def test_tier_mismatch_metric_exists() -> None:
    from cmi_common.observability.metrics import AI_MODEL_TIER_MISMATCH

    AI_MODEL_TIER_MISMATCH.labels("svc", "haiku", "opus").inc()
    assert AI_MODEL_TIER_MISMATCH.labels("svc", "haiku", "opus")._value.get() >= 1


def test_cli_model_alias_passed(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    envelope = json.dumps({"result": "{}", "usage": {}})
    proc = FakeProc(out=envelope.encode())
    captured: dict = {}
    _patch_exec(monkeypatch, proc, captured)

    t = CliTransport("claude-haiku-4-5-20251001", CliOptions())
    asyncio.run(t.complete(system="s", prompt="p", service="svc"))
    argv = captured["argv"]
    assert argv[argv.index("--model") + 1] == "haiku"


def test_cli_model_alias_unknown_passthrough(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    envelope = json.dumps({"result": "{}", "usage": {}})
    proc = FakeProc(out=envelope.encode())
    captured: dict = {}
    _patch_exec(monkeypatch, proc, captured)

    t = CliTransport("some-custom-model", CliOptions())
    asyncio.run(t.complete(system="s", prompt="p", service="svc"))
    argv = captured["argv"]
    assert argv[argv.index("--model") + 1] == "some-custom-model"


def test_cli_system_prompt_append_mode(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    envelope = json.dumps({"result": "{}", "usage": {}})
    proc = FakeProc(out=envelope.encode())
    captured: dict = {}
    _patch_exec(monkeypatch, proc, captured)

    t = CliTransport("m", CliOptions(system_prompt_mode="append"))
    asyncio.run(t.complete(system="SYS", prompt="p", service="svc"))
    argv = captured["argv"]
    assert argv[argv.index("--append-system-prompt") + 1] == "SYS"
    assert "--system-prompt" not in argv
    assert "--exclude-dynamic-system-prompt-sections" not in argv


def test_actual_model_surfaced(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    envelope = json.dumps({
        "result": '{"opportunity_score": 42}',
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "modelUsage": {
            "claude-haiku-4-5-20251001": {"outputTokens": 3},
            "claude-sonnet-4-6": {"outputTokens": 20},
        },
    })
    proc = FakeProc(out=envelope.encode())
    _patch_exec(monkeypatch, proc, {})

    t = CliTransport("claude-sonnet-4-6", CliOptions())
    resp = asyncio.run(t.complete(system="s", prompt="p", service="svc"))
    assert resp.actual_model == "claude-sonnet-4-6"


def test_actual_model_none_when_absent(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    envelope = json.dumps({"result": "{}", "usage": {}})
    proc = FakeProc(out=envelope.encode())
    _patch_exec(monkeypatch, proc, {})

    t = CliTransport("m", CliOptions())
    resp = asyncio.run(t.complete(system="s", prompt="p", service="svc"))
    assert resp.actual_model is None


def test_cli_env_scrubs_api_key(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-should-not-leak")
    monkeypatch.setenv("SOME_OTHER_VAR", "keepme")

    envelope = json.dumps({"result": "{}", "usage": {}})
    proc = FakeProc(out=envelope.encode())
    captured: dict = {}
    _patch_exec(monkeypatch, proc, captured)

    t = CliTransport("m", CliOptions())
    asyncio.run(t.complete(system="s", prompt="p", service="svc"))

    env = captured["env"]
    assert env is not None
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env.get("SOME_OTHER_VAR") == "keepme"


def _mismatch_count(service: str, requested: str, actual: str) -> float:
    from cmi_common.observability.metrics import AI_MODEL_TIER_MISMATCH

    return AI_MODEL_TIER_MISMATCH.labels(service, requested, actual)._value.get()


def test_cli_tier_mismatch_counted(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    envelope = json.dumps({
        "result": '{"opportunity_score": 1}',
        "usage": {"output_tokens": 5},
        "modelUsage": {"claude-opus-4-8": {"outputTokens": 5}},
    })
    proc = FakeProc(out=envelope.encode())
    _patch_exec(monkeypatch, proc, {})

    before = _mismatch_count("svc", "haiku", "opus")
    t = CliTransport("claude-haiku-4-5-20251001", CliOptions())
    resp = asyncio.run(t.complete(system="s", prompt="p", service="svc"))

    assert resp.json() == {"opportunity_score": 1}
    assert _mismatch_count("svc", "haiku", "opus") == before + 1


def test_cli_tier_match_not_counted(monkeypatch) -> None:
    from cmi_common.ai.claude import CliOptions, CliTransport

    envelope = json.dumps({
        "result": "{}",
        "usage": {"output_tokens": 5},
        "modelUsage": {"claude-haiku-4-5-20251001": {"outputTokens": 5}},
    })
    proc = FakeProc(out=envelope.encode())
    _patch_exec(monkeypatch, proc, {})

    before = _mismatch_count("svc", "haiku", "haiku")
    t = CliTransport("claude-haiku-4-5-20251001", CliOptions())
    asyncio.run(t.complete(system="s", prompt="p", service="svc"))
    assert _mismatch_count("svc", "haiku", "haiku") == before
