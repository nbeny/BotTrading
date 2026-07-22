# CLI transport hardening — design

**Date:** 2026-07-22
**Status:** approved (pending spec review)
**Scope owner:** `libs/cmi_common/cmi_common/ai/claude.py` (`CliTransport`), plus config, metrics, tests.

## Context

BotTrading already ships a working `CliTransport` (the `claude -p` subscription path),
wired end to end: `AIConfig.transport="cli"` → `ai-worker-{haiku,sonnet}/app/main.py` →
`ClaudeClient` → `CliTransport`. It runs a fresh, isolated, stateless subprocess per call
with a concurrency semaphore, a scratch cwd, timeout/quota/error classification, and
Prometheus metrics.

The sibling project `TradingBot` (`src/ai/_client.py`) has a more hardened version of the
same idea. Comparing the two surfaced four protections BotTrading is missing. This spec
ports those four into the existing `CliTransport` **without** rewriting it — the pluggable
transport architecture (`ApiTransport` / `CliTransport` / `StubTransport`) stays as is, and
`ApiTransport` / `StubTransport` are untouched.

## Goals (in scope)

Four hardening items, agreed as items 3–6 of the comparison:

1. **#5 — Model family alias for `--model`.** Pass the family alias (`haiku` / `sonnet` /
   `opus`) instead of the dated model id, so the subscription CLI always resolves the
   latest model in that family.
2. **#4 — System prompt override.** Replace `--append-system-prompt <system>` with
   `--system-prompt <system> --exclude-dynamic-system-prompt-sections`, stripping Claude
   Code's coding persona and the per-machine cwd/git/env/memory sections. Toggleable so the
   old append behaviour is still reachable.
3. **#3 — Model tier verification.** Read the model that *actually* ran from the CLI JSON
   envelope and, when its family differs from the requested family, emit a
   `log.warning("model_tier_mismatch", …)` and increment a dedicated Prometheus counter.
   Log-only — the decision still stands (no fail-closed).
4. **#6 — Cleanup + safety.** Drop `--dangerously-skip-permissions` (not needed for a
   non-interactive `-p` prompt), and scrub `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`
   from the subprocess env so the CLI can never bill per token even if a key is present in
   the process environment.

## Non-goals (explicitly out of scope)

- **No CLI→API fallback (#2).** On a subscription usage-limit the transport keeps its
  current behaviour: classify the outcome as `quota` and return an empty `ClaudeResponse`.
  The worker degrades exactly as today. This is a deliberate "subscription-only" posture.
- **No DB audit table.** TradingBot writes `record_failsafe_event` rows; BotTrading is a
  Prometheus-metrics shop (every service exposes `/metrics`). Tier mismatches are surfaced
  via structured log + counter only.
- **No changes to `ApiTransport` or `StubTransport`.**
- **No fail-closed strict-tier mode.** (TradingBot's `TRADINGBOT_CLAUDE_STRICT_MODEL_TIER`
  is not ported; mismatch is always log-only here.)

## Design

All changes are in `libs/cmi_common/cmi_common/ai/claude.py` unless stated otherwise.

### 1. Model family alias (#5)

Add module-level helpers:

```python
_CLI_MODEL = {
    "claude-haiku-4-5-20251001": "haiku",
    "claude-sonnet-4-6": "sonnet",
    "claude-opus-4-7": "opus",
    "claude-opus-4-8": "opus",
}

def _model_family(model_id: str) -> str | None:
    s = model_id.lower()
    for family in ("haiku", "sonnet", "opus"):
        if family in s:
            return family
    return None
```

In `CliTransport._run`, the `--model` argument becomes `_CLI_MODEL.get(self._model, self._model)`
— alias when known, raw id otherwise (unknown/undated ids fall through unchanged).

### 2. System prompt override (#4)

Add a field to `CliOptions`:

```python
system_prompt_mode: str = "override"   # "override" | "append"
```

In `_run`, build the system-prompt argv from the mode:

- `override` (default): `["--system-prompt", system, "--exclude-dynamic-system-prompt-sections"]`
- `append`: `["--append-system-prompt", system]` (current behaviour)

The user prompt continues to be delivered via **stdin** (`proc.communicate(prompt.encode())`) —
unchanged. `--dangerously-skip-permissions` is removed from the argv (see #6).

### 3. Model tier verification (#3)

`ClaudeResponse` gains an optional field:

```python
actual_model: str | None = None
```

`_parse` extracts the actually-served model from the envelope, preferring `modelUsage`
(pick the entry with the most output tokens — the real responder, ignoring an incidental
auxiliary Haiku call) and falling back to a top-level `model` field:

```python
def _actual_model(envelope: dict) -> str | None:
    usage_by_model = envelope.get("modelUsage")
    if isinstance(usage_by_model, dict) and usage_by_model:
        primary = max(usage_by_model.items(),
                      key=lambda kv: _output_tokens_of(kv[1]))[0]
        return primary if isinstance(primary, str) else None
    m = envelope.get("model")
    return m if isinstance(m, str) else None
```

`_output_tokens_of` reads `outputTokens` / `output_tokens` from a per-model usage dict,
defaulting to 0.

After parsing, `CliTransport` compares `_model_family(self._model)` against
`_model_family(actual_model)`. If both are known and differ, it:

- `log.warning("model_tier_mismatch", extra={"service", "requested", "requested_tier",
  "actual", "actual_tier"})`, and
- increments the new counter (below).

The response is still returned normally (`actual_model` populated). No repair, no drop.

### 4. Metrics (#3)

Add to `libs/cmi_common/cmi_common/observability/metrics.py`:

```python
AI_MODEL_TIER_MISMATCH = Counter(
    "cmi_ai_model_tier_mismatch_total",
    "CLI served a different model family than requested",
    ["service", "requested_tier", "actual_tier"],
)
```

Dedicated counter (not a new label on `AI_CLI_CALLS`) because a mismatch is orthogonal to
call outcome — the call succeeds; the tier is simply wrong.

### 5. Env scrubbing + cleanup (#6)

Add:

```python
def _cli_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items()
            if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
```

(new `import os`.) Pass `env=_cli_env()` to `asyncio.create_subprocess_exec` in `_run`, and
remove `--dangerously-skip-permissions` from the argv.

## Data flow (unchanged shape)

`worker.handle` → `ClaudeClient.complete(system, prompt, service)` →
`CliTransport.complete` (semaphore) → `_run` (spawn `claude -p`, scrubbed env) → `_parse`
(text + usage + `actual_model` + tier check) → `ClaudeResponse`. Callers that don't read
`actual_model` are unaffected (new field defaults to `None`).

## Error handling

Unchanged and fail-safe: timeout → kill + `timeout` outcome + empty response; non-zero exit
→ `quota` (if usage-limit text) or `error` outcome + empty response; missing binary →
`error` + empty; unparseable envelope → `error` + empty. Tier mismatch never degrades the
response — it is observability only.

## Testing (TDD)

Extend `tests/test_cli_transport.py`. New/updated cases:

- **`test_cli_argv_and_stdin` (update):** now asserts `--system-prompt` carries `SYS`, that
  `--exclude-dynamic-system-prompt-sections` is present, that `--append-system-prompt` is
  **absent**, and that `--dangerously-skip-permissions` is **absent**. Stdin assertion
  unchanged.
- **`test_cli_model_alias_passed`:** a dated id (`claude-haiku-4-5-20251001`) yields
  `--model haiku`; an unknown id passes through unchanged.
- **`test_cli_system_prompt_append_mode`:** `CliOptions(system_prompt_mode="append")`
  restores `--append-system-prompt` and drops the override flags.
- **`test_cli_env_scrubs_api_key`:** with `ANTHROPIC_API_KEY` set in `os.environ`, the env
  handed to `create_subprocess_exec` omits it (capture `env` in the fake exec).
- **`test_cli_tier_mismatch_counted`:** envelope whose `modelUsage` reports an `opus` model
  for a requested `haiku` increments `AI_MODEL_TIER_MISMATCH` and still returns the parsed
  result; a matching-family envelope does not increment it.
- **`test_actual_model_surfaced`:** `ClaudeResponse.actual_model` reflects the
  most-output-tokens entry in `modelUsage`.

The fake `create_subprocess_exec` helper (`_patch_exec`) is extended to also capture the
`env` kwarg.

## Rollout

Pure `libs/cmi_common` change plus a `CliOptions` field with a safe default; no service
wiring change required (`main.py` in both workers keeps its current `CliOptions(...)`
construction — `system_prompt_mode` defaults to `override`). If the override mode ever
misbehaves against the installed CLI version, `system_prompt_mode="append"` is the escape
hatch (would need to be threaded from `AIConfig` — deferred until needed).
