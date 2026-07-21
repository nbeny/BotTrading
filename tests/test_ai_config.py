# tests/test_ai_config.py
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
