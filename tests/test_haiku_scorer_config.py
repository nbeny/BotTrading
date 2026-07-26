"""HAIKU_ESCALATE_SCORE overrides the hardcoded escalation floor.

This floor decides what fraction of traffic reaches the paid Sonnet analyst, so
it is the one knob an operator must be able to turn against live funnel data
without waiting for an image rebuild.
"""

from __future__ import annotations

from service_modules import load_service_module

hm = load_service_module("ai-worker-haiku", "main")


def test_default_preserves_current_behaviour(monkeypatch) -> None:
    """Introducing the override must not change what a deploy does today."""
    monkeypatch.delenv("HAIKU_ESCALATE_SCORE", raising=False)
    assert hm.scorer_config_from_env().escalate_score == 60


def test_env_override_is_applied(monkeypatch) -> None:
    monkeypatch.setenv("HAIKU_ESCALATE_SCORE", "35")
    assert hm.scorer_config_from_env().escalate_score == 35


def test_other_scorer_settings_keep_their_defaults(monkeypatch) -> None:
    """Only the escalation floor is operator-tunable. The weights and caps are
    model parameters, not operations knobs; silently exposing them would let a
    deploy change the meaning of the score itself."""
    monkeypatch.setenv("HAIKU_ESCALATE_SCORE", "35")
    cfg = hm.scorer_config_from_env()
    assert cfg.w_momentum == 0.35
    assert cfg.w_volume == 0.25
    assert cfg.w_sentiment == 0.25
    assert cfg.w_liquidity == 0.15
    assert cfg.mom_cap_pct == 15.0
