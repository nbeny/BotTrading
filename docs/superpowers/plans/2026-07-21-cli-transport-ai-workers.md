## CLI Transport pour ai-worker-haiku & ai-worker-sonnet — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Faire exécuter les appels Claude des deux workers d'analyse via le CLI `claude` sous abonnement OAuth au lieu de l'API Anthropic facturée au token, sans changer leur logique métier.

**Architecture:** `ClaudeClient` devient un dispatcher sur trois transports (`api`, `cli`, `stub`). Le `CliTransport` lance un process `claude -p` frais et isolé par appel (jamais de session partagée), plafonné par un sémaphore asyncio, avec dégradation propre sur erreur/timeout/quota.

**Tech Stack:** Python 3.12, asyncio subprocess, pytest (`asyncio_mode=auto`), prometheus_client, Docker Compose, Node + `@anthropic-ai/claude-code`.

---

## Notes préalables (à lire avant de commencer)

- **Ce dépôt n'est PAS initialisé en git.** Les étapes « Commit » supposent que tu lances `git init` d'abord ; sinon, saute-les.
- **La lib partagée doit être importable en mode dev.** Si `import cmi_common` échoue dans les tests, installe-la en editable une fois :
  `pip install -e libs/cmi_common`
- **Commande de test de référence :** `pytest tests/ -v` (config dans `pyproject.toml`, `asyncio_mode = "auto"`, `testpaths = ["tests"]`).
- **Raffinement vs spec :** la spec proposait un `docker/Dockerfile.ai-worker` dédié. On le remplace par le **Dockerfile partagé + build-arg `INSTALL_CLAUDE_CLI`** (DRY). Seules les images ai-worker installent Node+CLI.

## Structure des fichiers

| Fichier | Rôle | Action |
|---|---|---|
| `libs/cmi_common/cmi_common/config.py` | Champs CLI dans `AISettings` | Modifier |
| `libs/cmi_common/cmi_common/observability/metrics.py` | Compteur `AI_CLI_CALLS` | Modifier |
| `libs/cmi_common/cmi_common/ai/claude.py` | Dispatcher + `ApiTransport`/`StubTransport`/`CliTransport` + `CliOptions` | Modifier |
| `libs/cmi_common/cmi_common/ai/__init__.py` | Exporter `CliOptions` | Modifier |
| `services/ai-worker-haiku/app/main.py` | Câbler transport + `CliOptions` | Modifier |
| `services/ai-worker-sonnet/app/main.py` | Câbler transport + `CliOptions` | Modifier |
| `docker/Dockerfile` | Build-arg `INSTALL_CLAUDE_CLI` + `HOME` | Modifier |
| `docker-compose.yml` | Args/env/volumes des 2 workers | Modifier |
| `.env.example` | Nouvelles variables | Modifier |
| `tests/test_ai_config.py` | Tests config CLI | Créer |
| `tests/test_cli_transport.py` | Tests dispatcher + CliTransport | Créer |

On garde **un seul fichier** `ai/claude.py` (~220 lignes, sections claires) plutôt qu'un package `ai/transports/` : churn minimal, interface publique (`ClaudeClient`, `ClaudeResponse`) stable.

---

## Task 1: Config — champs CLI dans `AISettings`

**Files:**
- Modify: `libs/cmi_common/cmi_common/config.py:70-78`
- Test: `tests/test_ai_config.py`

- [ ] **Step 1: Write the failing test**

```python
## tests/test_ai_config.py
"""AISettings exposes CLI-transport configuration via ANTHROPIC_ env prefix."""

from __future__ import annotations

from cmi_common.config import AISettings


def test_cli_defaults() -> None:
    s = AISettings()
    assert s.transport == "api"          # backward compatible default
    assert s.cli_path == "claude"
    assert s.cli_timeout_ms == 120000
    assert s.cli_concurrency == 4


def test_cli_env_override(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_TRANSPORT", "cli")
    monkeypatch.setenv("ANTHROPIC_CLI_PATH", "/usr/local/bin/claude")
    monkeypatch.setenv("ANTHROPIC_CLI_TIMEOUT_MS", "60000")
    monkeypatch.setenv("ANTHROPIC_CLI_CONCURRENCY", "8")
    s = AISettings()
    assert s.transport == "cli"
    assert s.cli_path == "/usr/local/bin/claude"
    assert s.cli_timeout_ms == 60000
    assert s.cli_concurrency == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ai_config.py -v`
Expected: FAIL — `AttributeError: 'AISettings' object has no attribute 'transport'`

- [ ] **Step 3: Add the fields**

In `libs/cmi_common/cmi_common/config.py`, extend `AISettings` (currently lines 70-78):

```python
class AISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANTHROPIC_", extra="ignore")

    api_key: str = Field(default="", repr=False)
    haiku_model: str = "claude-haiku-4-5-20251001"
    sonnet_model: str = "claude-sonnet-4-6"
    max_tokens: int = 1024
    # Only analyses at/above this score are escalated to Sonnet.
    escalation_threshold: int = 75
    # --- CLI (subscription) transport ---
    # transport selects the Claude backend: "api" (SDK), "cli" (claude -p), "stub".
    transport: str = "api"
    cli_path: str = "claude"
    cli_timeout_ms: int = 120000
    cli_concurrency: int = 4
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ai_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/config.py tests/test_ai_config.py
git commit -m "feat(config): add CLI transport settings to AISettings"
```

---

## Task 2: Métrique `AI_CLI_CALLS`

**Files:**
- Modify: `libs/cmi_common/cmi_common/observability/metrics.py:34-38`
- Test: `tests/test_cli_transport.py` (créé ici, complété aux tâches suivantes)

- [ ] **Step 1: Write the failing test**

```python
## tests/test_cli_transport.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_transport.py::test_ai_cli_calls_metric_exists -v`
Expected: FAIL — `ImportError: cannot import name 'AI_CLI_CALLS'`

- [ ] **Step 3: Add the counter**

Append to `libs/cmi_common/cmi_common/observability/metrics.py` after `AI_TOKENS` (line 38):

```python
AI_CLI_CALLS = Counter(
    "cmi_ai_cli_calls_total",
    "Claude CLI subprocess invocations by outcome",
    ["service", "model", "outcome"],
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_transport.py::test_ai_cli_calls_metric_exists -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/observability/metrics.py tests/test_cli_transport.py
git commit -m "feat(metrics): add AI_CLI_CALLS counter"
```

---

## Task 3: Refactor `ClaudeClient` en dispatcher (api/stub) — comportement préservé

**Files:**
- Modify: `libs/cmi_common/cmi_common/ai/claude.py` (entier)
- Modify: `libs/cmi_common/cmi_common/ai/__init__.py`
- Test: `tests/test_cli_transport.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_transport.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_transport.py -v`
Expected: FAIL — `ImportError: cannot import name 'CliOptions'`

- [ ] **Step 3: Rewrite `ai/claude.py` as a dispatcher**

Replace the entire contents of `libs/cmi_common/cmi_common/ai/claude.py` with:

```python
"""Claude access for the AI workers, via a pluggable transport.

The public surface stays stable: ``ClaudeClient.complete(system, prompt, service)``
returns a ``ClaudeResponse``. Internally it dispatches to one of three transports:

* ``api``  — the Anthropic SDK (per-token billing).
* ``cli``  — the ``claude`` CLI under an OAuth subscription (fresh, isolated
             subprocess per call; never a shared session).
* ``stub`` — a deterministic offline fallback for tests / no-key envs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any

from ..observability.metrics import AI_CLI_CALLS, AI_TOKENS

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ClaudeResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0

    def json(self) -> dict[str, Any]:
        """Best-effort parse of a JSON object out of the model's reply."""
        text = self.text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{") : text.rfind("}") + 1]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            raise


@dataclass(slots=True)
class CliOptions:
    cli_path: str = "claude"
    timeout_ms: int = 120000
    concurrency: int = 4


class ClaudeClient:
    """Dispatcher over a transport chosen at construction."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_tokens: int = 1024,
        transport: str = "api",
        cli: CliOptions | None = None,
    ) -> None:
        self._model = model
        if transport == "cli":
            self._transport: _Transport = CliTransport(model, cli or CliOptions())
        elif api_key:
            self._transport = ApiTransport(api_key, model, max_tokens=max_tokens)
        else:
            self._transport = StubTransport()

    async def complete(
        self, *, system: str, prompt: str, service: str
    ) -> ClaudeResponse:
        return await self._transport.complete(
            system=system, prompt=prompt, service=service
        )


class _Transport:
    async def complete(
        self, *, system: str, prompt: str, service: str
    ) -> ClaudeResponse:  # pragma: no cover - interface
        raise NotImplementedError


class ApiTransport(_Transport):
    def __init__(self, api_key: str, model: str, *, max_tokens: int = 1024) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._client: Any | None = None

    def _ensure(self) -> Any | None:
        if self._client is not None:
            return self._client
        try:
            from anthropic import AsyncAnthropic  # type: ignore

            self._client = AsyncAnthropic(api_key=self._api_key)
        except ImportError:
            logger.warning("anthropic SDK not installed; using offline stub")
            self._client = None
        return self._client

    async def complete(
        self, *, system: str, prompt: str, service: str
    ) -> ClaudeResponse:
        client = self._ensure()
        if client is None:
            return _offline_stub(prompt)
        msg = await client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in msg.content if block.type == "text")
        usage = getattr(msg, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0)
        out_tok = getattr(usage, "output_tokens", 0)
        AI_TOKENS.labels(service, self._model, "input").inc(in_tok)
        AI_TOKENS.labels(service, self._model, "output").inc(out_tok)
        return ClaudeResponse(text=text, input_tokens=in_tok, output_tokens=out_tok)


class StubTransport(_Transport):
    async def complete(
        self, *, system: str, prompt: str, service: str
    ) -> ClaudeResponse:
        return _offline_stub(prompt)


class CliTransport(_Transport):
    """Runs ``claude -p`` once per call: fresh, isolated, stateless subprocess."""

    def __init__(self, model: str, options: CliOptions) -> None:
        self._model = model
        self._opts = options
        self._sem = asyncio.Semaphore(options.concurrency)

    async def complete(
        self, *, system: str, prompt: str, service: str
    ) -> ClaudeResponse:
        async with self._sem:
            return await self._run(system=system, prompt=prompt, service=service)

    async def _run(
        self, *, system: str, prompt: str, service: str
    ) -> ClaudeResponse:
        scratch = tempfile.mkdtemp(prefix="cmi-claude-")
        argv = [
            self._opts.cli_path,
            "-p",
            "--model",
            self._model,
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
            "--append-system-prompt",
            system,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=scratch,
            )
        except FileNotFoundError:
            AI_CLI_CALLS.labels(service, self._model, "error").inc()
            logger.error("claude CLI not found at %s", self._opts.cli_path)
            shutil.rmtree(scratch, ignore_errors=True)
            return ClaudeResponse(text="")
        try:
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(prompt.encode()),
                    timeout=self._opts.timeout_ms / 1000,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                AI_CLI_CALLS.labels(service, self._model, "timeout").inc()
                logger.warning("claude CLI timeout for %s", service)
                return ClaudeResponse(text="")
            if proc.returncode != 0:
                outcome = "quota" if _is_quota(err) else "error"
                AI_CLI_CALLS.labels(service, self._model, outcome).inc()
                logger.warning(
                    "claude CLI exit %s (%s): %s",
                    proc.returncode,
                    outcome,
                    err.decode(errors="replace")[:500],
                )
                return ClaudeResponse(text="")
            return self._parse(out, service)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _parse(self, out: bytes, service: str) -> ClaudeResponse:
        try:
            envelope = json.loads(out.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            AI_CLI_CALLS.labels(service, self._model, "error").inc()
            logger.warning("claude CLI unparseable output for %s", service)
            return ClaudeResponse(text="")
        if not isinstance(envelope, dict):
            AI_CLI_CALLS.labels(service, self._model, "error").inc()
            return ClaudeResponse(text="")
        text = envelope.get("result", "") or ""
        usage = envelope.get("usage", {}) or {}
        in_tok = int(usage.get("input_tokens", 0) or 0)
        out_tok = int(usage.get("output_tokens", 0) or 0)
        AI_TOKENS.labels(service, self._model, "input").inc(in_tok)
        AI_TOKENS.labels(service, self._model, "output").inc(out_tok)
        AI_CLI_CALLS.labels(service, self._model, "success").inc()
        return ClaudeResponse(text=text, input_tokens=in_tok, output_tokens=out_tok)


def _is_quota(err: bytes) -> bool:
    low = err.decode(errors="replace").lower()
    return "usage limit" in low or "rate limit" in low or "quota" in low


def _offline_stub(prompt: str) -> ClaudeResponse:
    """Deterministic pseudo-analysis when no real backend is configured."""
    score = 50 + (len(prompt) % 50)
    payload = {
        "opportunity_score": score,
        "confidence": 0.5,
        "reason": "offline-stub: no Claude backend configured",
        "summary": "stub analysis",
        "escalate": score >= 75,
    }
    return ClaudeResponse(text=json.dumps(payload))
```

- [ ] **Step 4: Export `CliOptions`**

Replace `libs/cmi_common/cmi_common/ai/__init__.py` with:

```python
from .claude import ClaudeClient, ClaudeResponse, CliOptions

__all__ = ["ClaudeClient", "ClaudeResponse", "CliOptions"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cli_transport.py -v`
Expected: PASS (stub, code-fence, CliOptions tests all green)

- [ ] **Step 6: Run the full suite (no regressions)**

Run: `pytest tests/ -v`
Expected: PASS — existing tests unaffected (default transport still `api`/stub).

- [ ] **Step 7: Commit**

```bash
git add libs/cmi_common/cmi_common/ai/claude.py libs/cmi_common/cmi_common/ai/__init__.py tests/test_cli_transport.py
git commit -m "refactor(ai): make ClaudeClient a transport dispatcher"
```

---

## Task 4: CliTransport — argv, stdin, parsing, tokens (chemin nominal)

**Files:**
- Test: `tests/test_cli_transport.py`
- (Implementation already written in Task 3; these tests lock it in.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_transport.py`:

```python
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
    async def _fake(*argv, stdin=None, stdout=None, stderr=None, cwd=None):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
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
    # System prompt passed via flag, user prompt via stdin.
    assert argv[argv.index("--append-system-prompt") + 1] == "SYS"
    assert proc.sent_stdin == b"PROMPT"
    # Envelope parsed, tokens surfaced.
    assert resp.json() == {"opportunity_score": 80}
    assert resp.input_tokens == 10
    assert resp.output_tokens == 5
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_cli_transport.py::test_cli_argv_and_stdin -v`
Expected: PASS (implementation from Task 3 satisfies it)

> If it fails, the bug is in Task 3's `CliTransport`; fix there, not by weakening the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli_transport.py
git commit -m "test(ai): lock CliTransport argv/stdin/parsing"
```

---

## Task 5: CliTransport — dégradation (timeout, exit non-zéro, quota, CLI absent)

**Files:**
- Test: `tests/test_cli_transport.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_transport.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_cli_transport.py -k "timeout or nonzero or quota or missing" -v`
Expected: PASS (4 passed) — Task 3 implementation already handles these paths.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli_transport.py
git commit -m "test(ai): CliTransport degrades on timeout/error/quota/missing"
```

---

## Task 6: CliTransport — plafond de concurrence (sémaphore)

**Files:**
- Test: `tests/test_cli_transport.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_transport.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_cli_transport.py::test_semaphore_caps_parallelism -v`
Expected: PASS — the `asyncio.Semaphore(concurrency)` in `CliTransport` bounds parallelism.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli_transport.py
git commit -m "test(ai): CliTransport bounds parallelism to cli_concurrency"
```

---

## Task 7: Câbler les workers sur le transport configuré

**Files:**
- Modify: `services/ai-worker-haiku/app/main.py:10,22-24`
- Modify: `services/ai-worker-sonnet/app/main.py:10,19-21`

> Pas de test unitaire dédié : `main.py` est du câblage (startup FastAPI). Vérification par parse + `pytest tests/`.

- [ ] **Step 1: Update haiku wiring**

In `services/ai-worker-haiku/app/main.py`, change the import line (line 10) and the `ClaudeClient` construction (lines 22-24):

```python
from cmi_common.ai import ClaudeClient, CliOptions
```

```python
    claude = ClaudeClient(
        settings.ai.api_key,
        settings.ai.haiku_model,
        max_tokens=settings.ai.max_tokens,
        transport=settings.ai.transport,
        cli=CliOptions(
            cli_path=settings.ai.cli_path,
            timeout_ms=settings.ai.cli_timeout_ms,
            concurrency=settings.ai.cli_concurrency,
        ),
    )
```

- [ ] **Step 2: Update sonnet wiring**

In `services/ai-worker-sonnet/app/main.py`, change the import (line 10) and construction (lines 19-21):

```python
from cmi_common.ai import ClaudeClient, CliOptions
```

```python
    claude = ClaudeClient(
        settings.ai.api_key,
        settings.ai.sonnet_model,
        max_tokens=settings.ai.max_tokens,
        transport=settings.ai.transport,
        cli=CliOptions(
            cli_path=settings.ai.cli_path,
            timeout_ms=settings.ai.cli_timeout_ms,
            concurrency=settings.ai.cli_concurrency,
        ),
    )
```

- [ ] **Step 3: Verify modules parse cleanly**

Run: `python -c "import ast; ast.parse(open('services/ai-worker-haiku/app/main.py').read()); ast.parse(open('services/ai-worker-sonnet/app/main.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Run full suite**

Run: `pytest tests/ -v`
Expected: PASS (no regressions)

- [ ] **Step 5: Commit**

```bash
git add services/ai-worker-haiku/app/main.py services/ai-worker-sonnet/app/main.py
git commit -m "feat(workers): wire haiku/sonnet to configurable Claude transport"
```

---

## Task 8: Docker — installer le CLI dans les images ai-worker

**Files:**
- Modify: `docker/Dockerfile:31-40`

- [ ] **Step 1: Add the conditional CLI install + HOME**

In `docker/Dockerfile`, after `COPY ${SERVICE_PATH} /app` (line 31) and **before** the `useradd` block (line 34), insert:

```dockerfile
# Optionally install the Claude CLI (only for the AI worker images).
ARG INSTALL_CLAUDE_CLI=false
RUN if [ "$INSTALL_CLAUDE_CLI" = "true" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends nodejs npm \
        && rm -rf /var/lib/apt/lists/* \
        && npm install -g @anthropic-ai/claude-code; \
    fi
```

Then set an explicit `HOME` so the CLI finds `~/.claude`. Change the `useradd`/`USER` block:

```dockerfile
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
ENV HOME=/home/appuser
USER appuser
```

- [ ] **Step 2: Build the haiku image to verify the CLI installs**

Run:
```bash
docker build -f docker/Dockerfile \
  --build-arg SERVICE_PATH=services/ai-worker-haiku \
  --build-arg INSTALL_CLAUDE_CLI=true \
  -t cmi-ai-worker-haiku:cli-test .
```
Expected: build succeeds.

- [ ] **Step 3: Verify the CLI binary is present for appuser**

Run: `docker run --rm --entrypoint sh cmi-ai-worker-haiku:cli-test -c "claude --version"`
Expected: prints a version string (CLI is on PATH for `appuser`).

- [ ] **Step 4: Commit**

```bash
git add docker/Dockerfile
git commit -m "build: install Claude CLI in AI worker images via build arg"
```

---

## Task 9: Docker Compose — args, env et montage de l'auth

**Files:**
- Modify: `docker-compose.yml:205-220`

- [ ] **Step 1: Replace the two worker service blocks**

Replace lines 205-220 of `docker-compose.yml` with:

```yaml
  ai-worker-haiku:
    <<: *service-defaults
    build:
      context: .
      dockerfile: docker/Dockerfile
      args:
        SERVICE_PATH: services/ai-worker-haiku
        INSTALL_CLAUDE_CLI: "true"
    environment:
      <<: *common-env
      ANTHROPIC_HAIKU_MODEL: ${ANTHROPIC_HAIKU_MODEL:-claude-haiku-4-5-20251001}
      ANTHROPIC_ESCALATION_THRESHOLD: ${ANTHROPIC_ESCALATION_THRESHOLD:-75}
      ANTHROPIC_TRANSPORT: ${ANTHROPIC_TRANSPORT:-cli}
      ANTHROPIC_CLI_PATH: ${ANTHROPIC_CLI_PATH:-claude}
      ANTHROPIC_CLI_TIMEOUT_MS: ${ANTHROPIC_CLI_TIMEOUT_MS:-120000}
      ANTHROPIC_CLI_CONCURRENCY: ${ANTHROPIC_CLI_CONCURRENCY:-4}
    volumes:
      - ${CLAUDE_DIR}:/home/appuser/.claude:ro
      - ${CLAUDE_CONFIG}:/home/appuser/.claude.json:ro
    deploy:
      replicas: ${HAIKU_REPLICAS:-2}

  ai-worker-sonnet:
    <<: *service-defaults
    build:
      context: .
      dockerfile: docker/Dockerfile
      args:
        SERVICE_PATH: services/ai-worker-sonnet
        INSTALL_CLAUDE_CLI: "true"
    environment:
      <<: *common-env
      ANTHROPIC_SONNET_MODEL: ${ANTHROPIC_SONNET_MODEL:-claude-sonnet-4-6}
      ANTHROPIC_TRANSPORT: ${ANTHROPIC_TRANSPORT:-cli}
      ANTHROPIC_CLI_PATH: ${ANTHROPIC_CLI_PATH:-claude}
      ANTHROPIC_CLI_TIMEOUT_MS: ${ANTHROPIC_CLI_TIMEOUT_MS:-120000}
      ANTHROPIC_CLI_CONCURRENCY: ${ANTHROPIC_CLI_CONCURRENCY:-4}
    volumes:
      - ${CLAUDE_DIR}:/home/appuser/.claude:ro
      - ${CLAUDE_CONFIG}:/home/appuser/.claude.json:ro
```

- [ ] **Step 2: Validate the compose file**

Run: `docker compose config >/dev/null && echo "compose OK"`
Expected: `compose OK`. If it complains that `CLAUDE_DIR`/`CLAUDE_CONFIG` are unset, that's expected until Task 10 sets them in `.env`.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "build: mount Claude auth + set CLI env for AI workers"
```

---

## Task 10: `.env.example` — nouvelles variables

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Append the CLI transport section**

Append this block to `.env.example`:

```dotenv
## --- Claude CLI (subscription) transport for AI workers ---
## transport=cli runs `claude -p` under your OAuth subscription instead of the
## per-token Anthropic API. Set to `api` to fall back to the SDK.
ANTHROPIC_TRANSPORT=cli
ANTHROPIC_CLI_PATH=claude
ANTHROPIC_CLI_TIMEOUT_MS=120000
ANTHROPIC_CLI_CONCURRENCY=4
## Host paths to your authenticated Claude CLI credentials, mounted read-only
## into the ai-worker containers. Must contain the OAuth credential file (run
## `claude` and log in on the host first). Windows/Docker Desktop example:
CLAUDE_DIR=C:\Users\nbeny\.claude
CLAUDE_CONFIG=C:\Users\nbeny\.claude.json
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: document Claude CLI transport env vars"
```

---

## Task 11: Vérification d'authentification de bout en bout (manuelle, une fois)

**Files:** none — validation runtime.

> Confirme le prérequis critique de la spec : le CLI dans le conteneur est authentifié via les fichiers montés. À faire une fois `.env` rempli avec un `CLAUDE_DIR`/`CLAUDE_CONFIG` valides.

- [ ] **Step 1: Confirm the host has OAuth credentials**

Check `C:\Users\nbeny\.claude` for a credentials file (e.g. `.credentials.json`). If missing, run `claude` on the host and log in before continuing.

- [ ] **Step 2: Run an authenticated CLI call inside the worker image**

Run:
```bash
docker run --rm \
  -e HOME=/home/appuser \
  -v "${CLAUDE_DIR}:/home/appuser/.claude:ro" \
  -v "${CLAUDE_CONFIG}:/home/appuser/.claude.json:ro" \
  --entrypoint sh cmi-ai-worker-haiku:cli-test \
  -c 'echo "Reply with the single word: ok" | claude -p --model haiku --output-format json --dangerously-skip-permissions'
```
Expected: a JSON envelope with a `"result"` field and no auth error. An auth/login error means the mounted credentials are not valid in-container — resolve before deploying.

- [ ] **Step 3: Bring the stack up and watch the workers**

Run: `docker compose up -d --build ai-worker-haiku ai-worker-sonnet`
Then: `docker compose logs -f ai-worker-haiku ai-worker-sonnet`
Expected: workers start and (under real market flow) emit analysis/decision events without CLI auth errors. Watch `cmi_ai_cli_calls_total{outcome="success"}` climb and `outcome="error|quota"` stay low.

---

## Self-review — couverture de la spec

| Exigence spec | Tâche(s) |
|---|---|
| Dispatcher `api`/`cli`/`stub`, interface `complete()` stable | Task 3 |
| Process indépendant par appel, jamais `--continue`/`--resume` | Task 3 (impl) + Task 4 (test argv) |
| Sémaphore parallélisme + file en débordement | Task 3 (impl) + Task 6 (test) |
| Timeout + dégradation (timeout/error/quota/binaire absent) | Task 3 (impl) + Task 5 (tests) |
| cwd scratch isolé par appel | Task 3 (`tempfile.mkdtemp` + `rmtree`) |
| Parsing enveloppe CLI (`result`/`usage`) → `ClaudeResponse` | Task 3 (impl) + Task 4 (test) |
| Config `AISettings` (transport, cli_path, cli_timeout_ms, cli_concurrency) | Task 1 |
| Métrique `AI_CLI_CALLS` + `AI_TOKENS` alimenté | Task 2 + Task 3 |
| Câblage workers depuis la config | Task 7 |
| Dockerfile + CLI installé (raffiné en build-arg DRY) | Task 8 |
| Compose : montage `~/.claude` + env transport | Task 9 |
| `.env.example` : `CLAUDE_DIR`, `CLAUDE_CONFIG` + variables | Task 10 |
| Prérequis auth vérifié en conteneur | Task 11 |
| Tests avec faux CLI, aucun appel abonnement réel en CI | Tasks 4-6 (monkeypatch `create_subprocess_exec`) |
| Tests stub/api restent verts | Task 3 Step 6 |
| Hors périmètre (sidecar, X/Twitter, prompts/schemas) | non touché |

Toutes les exigences de la spec sont couvertes par au moins une tâche.
