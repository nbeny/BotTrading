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
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any

from ..observability.metrics import AI_CLI_CALLS, AI_TOKENS

logger = logging.getLogger(__name__)


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


def _cli_env() -> dict[str, str]:
    """Subprocess env with API credentials removed so the CLI can never
    bill per token -- it must draw on the mounted subscription session."""
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    }


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


@dataclass(slots=True)
class ClaudeResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    actual_model: str | None = None

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
    system_prompt_mode: str = "override"  # "override" | "append"


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
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=scratch,
                env=_cli_env(),
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
        actual = _actual_model(envelope)
        AI_CLI_CALLS.labels(service, self._model, "success").inc()
        return ClaudeResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            actual_model=actual,
        )


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
