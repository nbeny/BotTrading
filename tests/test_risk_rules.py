"""Risk-engine rules: SL/TP, sizing, gating."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from cmi_common.events.decision import Direction

_spec = importlib.util.spec_from_file_location(
    "risk_rules",
    Path(__file__).resolve().parents[1]
    / "services" / "risk-engine" / "app" / "rules.py",
)
rules = importlib.util.module_from_spec(_spec)
assert _spec.loader
sys.modules[_spec.name] = rules  # required for dataclass(slots=True)
_spec.loader.exec_module(rules)


def _cfg(**kw):
    return rules.RiskConfig(**kw)


def test_blacklisted_token_rejected() -> None:
    d = rules.evaluate(
        entry_price=150, direction=Direction.LONG, confidence=0.9,
        opportunity_score=90, is_blacklisted=True, current_exposure_pct=0.0,
        config=_cfg(),
    )
    assert not d.approved and "blacklist" in d.reason


def test_low_confidence_rejected() -> None:
    d = rules.evaluate(
        entry_price=150, direction=Direction.LONG, confidence=0.2,
        opportunity_score=90, is_blacklisted=False, current_exposure_pct=0.0,
        config=_cfg(),
    )
    assert not d.approved


def test_long_levels_and_sizing() -> None:
    d = rules.evaluate(
        entry_price=150, direction=Direction.LONG, confidence=0.8,
        opportunity_score=85, is_blacklisted=False, current_exposure_pct=0.0,
        config=_cfg(stop_loss_pct=0.05, take_profit_pct=0.10, max_position_pct=0.05),
    )
    assert d.approved
    lv = d.levels
    assert lv.stop_loss < lv.entry_price < lv.take_profit
    assert lv.risk_reward_ratio == 2.0  # 10% reward / 5% risk
    assert lv.position_size_pct == 0.04  # 0.05 * 0.8


def test_exposure_cap_blocks_new_position() -> None:
    d = rules.evaluate(
        entry_price=150, direction=Direction.LONG, confidence=0.9,
        opportunity_score=90, is_blacklisted=False, current_exposure_pct=0.99,
        config=_cfg(max_position_pct=0.05),
    )
    assert not d.approved and "exposure" in d.reason


def test_short_levels_inverted() -> None:
    d = rules.evaluate(
        entry_price=150, direction=Direction.SHORT, confidence=0.7,
        opportunity_score=80, is_blacklisted=False, current_exposure_pct=0.0,
        config=_cfg(),
    )
    assert d.approved
    assert d.levels.take_profit < d.levels.entry_price < d.levels.stop_loss
