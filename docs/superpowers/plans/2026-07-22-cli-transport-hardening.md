# CLI Transport Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port four hardening protections from TradingBot src/ai/_client.py into BotTrading existing CliTransport: model-family alias for --model, system-prompt override, model-tier mismatch detection, and env scrubbing / flag cleanup.

**Architecture:** All changes land in `libs/cmi_common/cmi_common/ai/claude.py` (CliTransport only) plus one new counter in `observability/metrics.py`. The pluggable transport design stays; ApiTransport and StubTransport are untouched. Subscription-only posture: no CLI->API fallback, no DB audit. Tests extend `tests/test_cli_transport.py` (TDD).

**Tech Stack:** Python 3.12, asyncio subprocess, dataclasses, prometheus_client, pytest + monkeypatch.

**Spec:** `docs/superpowers/specs/2026-07-22-cli-transport-hardening-design.md`

---

## File Structure

- **Modify** `libs/cmi_common/cmi_common/observability/metrics.py` -- add AI_MODEL_TIER_MISMATCH counter.
- **Modify** `libs/cmi_common/cmi_common/ai/claude.py` -- add `import os`; helpers _CLI_MODEL, _model_family, _output_tokens_of, _actual_model, _cli_env; extend CliOptions (system_prompt_mode) and ClaudeResponse (actual_model); rewire CliTransport._run argv + env; extend CliTransport._parse for tier check.
- **Modify** `tests/test_cli_transport.py` -- extend _patch_exec to capture env; update test_cli_argv_and_stdin; add new tests.

Order: metric first (leaf dependency), then transport helpers/behavior task-by-task, updating tests as we go.

---

## Task 1: Add the tier-mismatch metric

**Files:**
- Modify: `libs/cmi_common/cmi_common/observability/metrics.py`
- Test: `tests/test_cli_transport.py`

- [ ] **Step 1: Write the failing test** -- add to tests/test_cli_transport.py:

```python
def test_tier_mismatch_metric_exists() -> None:
    from cmi_common.observability.metrics import AI_MODEL_TIER_MISMATCH

    AI_MODEL_TIER_MISMATCH.labels("svc", "haiku", "opus").inc()
    assert AI_MODEL_TIER_MISMATCH.labels("svc", "haiku", "opus")._value.get() >= 1
```

- [ ] **Step 2: Run test, verify it fails** -- `pytest tests/test_cli_transport.py::test_tier_mismatch_metric_exists -v` -> FAIL ImportError cannot import name AI_MODEL_TIER_MISMATCH

- [ ] **Step 3: Implement** -- append to metrics.py after AI_CLI_CALLS:

```python
AI_MODEL_TIER_MISMATCH = Counter(
    "cmi_ai_model_tier_mismatch_total",
    "CLI served a different model family than requested",
    ["service", "requested_tier", "actual_tier"],
)
```

- [ ] **Step 4: Run test, verify it passes** -- same command -> PASS

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/observability/metrics.py tests/test_cli_transport.py
git commit -m "feat(cmi_common): add cmi_ai_model_tier_mismatch_total counter"
```

---

## Task 2: Model-family alias for --model (#5)

**Files:**
- Modify: `libs/cmi_common/cmi_common/ai/claude.py`
- Test: `tests/test_cli_transport.py`

- [ ] **Step 1: Write the failing tests** -- add to tests/test_cli_transport.py:

```python
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
```

- [ ] **Step 2: Run tests, verify they fail** -- `pytest tests/test_cli_transport.py::test_cli_model_alias_passed -v` -> FAIL (--model carries the dated id, not haiku).

- [ ] **Step 3: Implement** -- in claude.py, add module-level helpers after the imports, before ClaudeResponse:

```python
_CLI_MODEL: dict[str, str] = {
    "claude-haiku-4-5-20251001": "haiku",
    "claude-sonnet-4-6": "sonnet",
    "claude-opus-4-7": "opus",
    "claude-opus-4-8": "opus",
}


def _model_family(model_id: str) -> str | None:
    """Coarse Haiku/Sonnet/Opus bucket for a model id or alias."""
    s = model_id.lower()
    for family in ("haiku", "sonnet", "opus"):
        if family in s:
            return family
    return None
```

Then in CliTransport._run, change only the --model value from `self._model` to `_CLI_MODEL.get(self._model, self._model)`. (This value line is replaced fully in Task 3 argv rewrite; setting it here keeps Task 2 self-contained.)

- [ ] **Step 4: Run tests, verify they pass** -- both alias tests -> PASS

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/ai/claude.py tests/test_cli_transport.py
git commit -m "feat(cmi_common): pass model-family alias to claude --model"
```

---

## Task 3: System-prompt override + flag cleanup (#4, #6 flags)

**Files:**
- Modify: `libs/cmi_common/cmi_common/ai/claude.py`
- Test: `tests/test_cli_transport.py`

- [ ] **Step 1: Update argv test + add append-mode test** -- in test_cli_argv_and_stdin, replace:

```python
    # System prompt passed via flag, user prompt via stdin.
    assert argv[argv.index("--append-system-prompt") + 1] == "SYS"
    assert proc.sent_stdin == b"PROMPT"
```

with:

```python
    # System prompt override: replace persona + drop dynamic sections.
    assert argv[argv.index("--system-prompt") + 1] == "SYS"
    assert "--exclude-dynamic-system-prompt-sections" in argv
    assert "--append-system-prompt" not in argv
    assert "--dangerously-skip-permissions" not in argv
    assert proc.sent_stdin == b"PROMPT"
```

Add a new test:

```python
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
```

- [ ] **Step 2: Run tests, verify they fail** -- both -> FAIL

- [ ] **Step 3: Implement** -- add the field to CliOptions:

```python
@dataclass(slots=True)
class CliOptions:
    cli_path: str = "claude"
    timeout_ms: int = 120000
    concurrency: int = 4
    system_prompt_mode: str = "override"  # "override" | "append"
```

In CliTransport._run, replace the whole argv list:

```python
        if self._opts.system_prompt_mode == "append":
            system_args = ["--append-system-prompt", system]
        else:
            system_args = [
                "--system-prompt",
                system,
                "--exclude-dynamic-system-prompt-sections",
            ]
        argv = [
            self._opts.cli_path,
            "-p",
            "--model",
            _CLI_MODEL.get(self._model, self._model),
            "--output-format",
            "json",
            *system_args,
        ]
```

- [ ] **Step 4: Run tests, verify they pass** -- both -> PASS

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/ai/claude.py tests/test_cli_transport.py
git commit -m "feat(cmi_common): override system prompt + drop skip-permissions flag"
```

---

## Task 4: Scrub API credentials from the CLI subprocess env (#6 env)

**Files:**
- Modify: `libs/cmi_common/cmi_common/ai/claude.py`
- Test: `tests/test_cli_transport.py`

- [ ] **Step 1: Extend _patch_exec to capture env, then write the failing test** -- update _patch_exec:

```python
def _patch_exec(monkeypatch, proc: FakeProc, captured: dict) -> None:
    async def _fake(*argv, stdin=None, stdout=None, stderr=None, cwd=None, env=None):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
        captured["env"] = env
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake)
```

Add the test:

```python
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
```

- [ ] **Step 2: Run test, verify it fails** -- `pytest tests/test_cli_transport.py::test_cli_env_scrubs_api_key -v` -> FAIL (_run passes no env=, captured env is None).

- [ ] **Step 3: Implement** -- add `import os` at the top of claude.py (after `import logging`). Add a helper after _model_family:

```python
def _cli_env() -> dict[str, str]:
    """Subprocess env with API credentials removed so the CLI can never
    bill per token -- it must draw on the mounted subscription session."""
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    }
```

In CliTransport._run, add `env=_cli_env()` to the subprocess spawn (the create_subprocess_exec call), keeping the existing stdin/stdout/stderr/cwd kwargs.

- [ ] **Step 4: Run tests, verify they pass** -- `pytest tests/test_cli_transport.py -v` -> PASS all (the _patch_exec signature change must not break existing tests; they ignore env).

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/ai/claude.py tests/test_cli_transport.py
git commit -m "feat(cmi_common): scrub ANTHROPIC_API_KEY/AUTH_TOKEN from claude CLI env"
```

---

## Task 5: Surface the actually-served model (#3 part 1)

**Files:**
- Modify: `libs/cmi_common/cmi_common/ai/claude.py`
- Test: `tests/test_cli_transport.py`

- [ ] **Step 1: Write the failing tests** -- add to tests/test_cli_transport.py:

```python
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
```

- [ ] **Step 2: Run test, verify it fails** -- `pytest tests/test_cli_transport.py::test_actual_model_surfaced -v` -> FAIL (ClaudeResponse has no actual_model).

- [ ] **Step 3: Implement** -- add the field to ClaudeResponse:

```python
@dataclass(slots=True)
class ClaudeResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    actual_model: str | None = None
```

(Leave the json() method unchanged.) Add module-level helpers after _cli_env:

```python
def _output_tokens_of(usage: Any) -> int:
    if not isinstance(usage, dict):
        return 0
    raw = usage.get("outputTokens", usage.get("output_tokens", 0))
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _actual_model(envelope: dict[str, Any]) -> str | None:
    """The model that produced the answer per the CLI JSON payload.

    Prefer the modelUsage entry with the most output tokens (the real
    responder, ignoring an incidental auxiliary call); fall back to a
    top-level model field; else None.
    """
    usage_by_model = envelope.get("modelUsage")
    if isinstance(usage_by_model, dict) and usage_by_model:
        primary = max(
            usage_by_model.items(),
            key=lambda kv: _output_tokens_of(kv[1]),
        )[0]
        return primary if isinstance(primary, str) else None
    model = envelope.get("model")
    return model if isinstance(model, str) else None
```

In CliTransport._parse, replace the final return so it computes and passes actual_model:

```python
        actual = _actual_model(envelope)
        AI_CLI_CALLS.labels(service, self._model, "success").inc()
        return ClaudeResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            actual_model=actual,
        )
```

- [ ] **Step 4: Run tests, verify they pass** -- both -> PASS

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/ai/claude.py tests/test_cli_transport.py
git commit -m "feat(cmi_common): surface actually-served model from CLI envelope"
```

---

## Task 6: Detect + count tier mismatch (#3 part 2)

**Files:**
- Modify: `libs/cmi_common/cmi_common/ai/claude.py`
- Test: `tests/test_cli_transport.py`

- [ ] **Step 1: Write the failing tests** -- add to tests/test_cli_transport.py:

```python
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
```

- [ ] **Step 2: Run test, verify it fails** -- `pytest tests/test_cli_transport.py::test_cli_tier_mismatch_counted -v` -> FAIL (counter never incremented).

- [ ] **Step 3: Implement** -- in claude.py, change the metrics import to also import AI_MODEL_TIER_MISMATCH:

```python
from ..observability.metrics import (
    AI_CLI_CALLS,
    AI_MODEL_TIER_MISMATCH,
    AI_TOKENS,
)
```

Add a method to CliTransport (right after _parse):

```python
    def _check_tier(self, actual: str | None, service: str) -> None:
        """Warn + count when the CLI served a different model family."""
        if actual is None:
            return
        want = _model_family(self._model)
        got = _model_family(actual)
        if want is None or got is None or want == got:
            return
        logger.warning(
            "model_tier_mismatch",
            extra={
                "event": "model_tier_mismatch",
                "service": service,
                "requested": self._model,
                "requested_tier": want,
                "actual": actual,
                "actual_tier": got,
            },
        )
        AI_MODEL_TIER_MISMATCH.labels(service, want, got).inc()
```

In CliTransport._parse, insert `self._check_tier(actual, service)` immediately after the `actual = _actual_model(envelope)` line added in Task 5 (before the AI_CLI_CALLS success inc / return).

- [ ] **Step 4: Run tests, verify they pass** -- both -> PASS

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/ai/claude.py tests/test_cli_transport.py
git commit -m "feat(cmi_common): detect + count model-tier mismatch under CLI transport"
```

---

## Task 7: Full suite + lint gate

**Files:** none (verification only)

- [ ] **Step 1:** `pytest tests/test_cli_transport.py -v` -> PASS all (original + new tests).
- [ ] **Step 2:** `make test` -> PASS (same baseline as before this branch; no new failures in cmi_common or the AI workers).
- [ ] **Step 3:** `make lint` -> ruff + black clean, mypy clean for the two modified modules. If black reformats, run `make format`, re-run `make lint`, then `git add -A && git commit --amend --no-edit`.
- [ ] **Step 4:** Nothing further unless Step 3 amended.

---

## Self-Review notes

- **Spec coverage:** #5 -> Task 2; #4 -> Task 3; #6 flag removal -> Task 3; #6 env scrub -> Task 4; #3 actual_model -> Task 5; #3 detect+count+log -> Task 6; new metric -> Task 1. Non-goals (no CLI->API fallback, no DB audit, no strict fail-closed, ApiTransport/StubTransport untouched) respected.
- **Type consistency:** _actual_model(envelope) / _output_tokens_of(usage) / _model_family(id) / _cli_env() / _CLI_MODEL / CliTransport._check_tier / ClaudeResponse.actual_model / CliOptions.system_prompt_mode named identically throughout.
- **Placeholders:** none.
- **Any import:** _output_tokens_of and _actual_model use Any, already imported in claude.py (from typing import Any).
