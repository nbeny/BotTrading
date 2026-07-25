"""Deterministic opportunity triage — no LLM, no hidden state.

Mirrors TradingBot's quant-first router (`signals/llm/router/complexity.py`):
each correlated market feature is normalized to [0,1] and combined with a
versioned weighted sum. This runs on *every* symbol for free, so the pipeline
always has data. The LLM (Sonnet) is invoked only on the few candidates this
scorer flags as strong *and* ambiguous — where human-like judgement adds value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ScorerConfig:
    # Weights sum to 1.0 (validated below).
    w_momentum: float = 0.35
    w_volume: float = 0.25
    w_sentiment: float = 0.25
    w_liquidity: float = 0.15
    # Normalization caps — a factor at/above its cap saturates to 1.0.
    mom_cap_pct: float = 15.0        # |24h change %|
    vol_cap: float = 5.0             # volume spike ratio
    liq_cap_usd: float = 1_000_000.0  # liquidity for the safety factor
    thin_liq_usd: float = 50_000.0   # below this, a big move is "thin/ambiguous"
    escalate_score: int = 60         # min local score to consider LLM escalation

    def __post_init__(self) -> None:
        total = self.w_momentum + self.w_volume + self.w_sentiment + self.w_liquidity
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0, got {total}")


@dataclass(frozen=True, slots=True)
class ScoreResult:
    opportunity_score: int          # 0-100
    confidence: float               # 0-1
    reason: str
    escalate: bool                  # worth a senior (LLM) look?
    ambiguous: bool
    # Diagnostics — why a signal did or did not reach the senior analyst, and
    # how much evidence the score was actually computed from. A score built on
    # 2 of 4 factors is not comparable to one built on 4 of 4; the funnel needs
    # to tell them apart before any threshold is tuned.
    block_reason: str = "escalated"  # escalated | score_below_threshold | gate_not_met
    factors_present: int = 0         # 0-4
    liquidity_source: str = "unknown"  # dex | volume_proxy | unknown
    factors: dict[str, float] = field(default_factory=dict)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def local_opportunity(f: dict, cfg: ScorerConfig | None = None) -> ScoreResult:
    """Deterministic opportunity score for one symbol's correlated features."""
    cfg = cfg or ScorerConfig()
    chg = f.get("price_change_pct_24h")
    vol_spike = f.get("volume_spike_ratio")
    sent = f.get("sentiment_score")          # expected ~[-1, 1]
    liq = f.get("liquidity_usd")

    # How many of the four factors were actually supplied. Absent factors are
    # normalized to a neutral value, so the score alone hides thin evidence.
    factors_present = sum(
        1 for v in (chg, vol_spike, sent, liq) if v is not None
    )
    liquidity_source = "dex" if liq else "unknown"

    # Normalize each factor to [0,1] (magnitude of a tradeable setup).
    mom = _clamp(abs(chg) / cfg.mom_cap_pct, 0.0, 1.0) if chg is not None else 0.0
    vol = _clamp((vol_spike - 1.0) / (cfg.vol_cap - 1.0), 0.0, 1.0) if vol_spike else 0.0
    sent_mag = _clamp(abs(sent), 0.0, 1.0) if sent is not None else 0.0
    # Unknown liquidity is treated as neutral (0.5) rather than penalized to 0.
    liq_f = (
        _clamp(math.log10(max(liq, 1.0)) / math.log10(cfg.liq_cap_usd), 0.0, 1.0)
        if liq
        else 0.5
    )

    raw = (
        cfg.w_momentum * mom
        + cfg.w_volume * vol
        + cfg.w_sentiment * sent_mag
        + cfg.w_liquidity * liq_f
    )
    score = int(round(100 * raw))

    # Ambiguity / signal disagreement — where the LLM earns its keep.
    disagreement = (
        chg is not None
        and sent is not None
        and abs(chg) > 2.0
        and abs(sent) > 0.2
        and (chg > 0) != (sent > 0)
    )
    thin_liq_big_move = liq is not None and liq < cfg.thin_liq_usd and mom > 0.5
    ambiguous = bool(disagreement or thin_liq_big_move)

    # Confidence measures how much we trust the *data*, not how clean the setup
    # looks. Ambiguity used to subtract 0.3 here, which pushed ambiguous signals
    # (exactly the ones we escalate) below the risk engine's confidence floor.
    confidence = round(_clamp(0.3 + 0.4 * liq_f + 0.15 * (factors_present / 4), 0.0, 1.0), 2)

    bits: list[str] = []
    if chg is not None:
        bits.append(f"24h {chg:+.1f}%")
    if vol_spike:
        bits.append(f"vol x{vol_spike:.1f}")
    if sent is not None:
        bits.append(f"sent {sent:+.2f}")
    if liq is not None:
        bits.append(f"liq ${liq:,.0f}")
    if disagreement:
        bits.append("price/sentiment disagree")
    reason = "deterministic triage — " + (", ".join(bits) or "insufficient signal")

    # Escalate only strong setups that are also ambiguous or high-conviction —
    # a calm, unanimous move needs no LLM.
    strong = score >= cfg.escalate_score
    gate = ambiguous or vol >= 0.6 or mom >= 0.6
    escalate = strong and gate
    if escalate:
        block_reason = "escalated"
    elif not strong:
        block_reason = "score_below_threshold"
    else:
        block_reason = "gate_not_met"

    return ScoreResult(
        opportunity_score=score,
        confidence=confidence,
        reason=reason,
        escalate=escalate,
        ambiguous=ambiguous,
        block_reason=block_reason,
        factors_present=factors_present,
        liquidity_source=liquidity_source,
        factors={
            "momentum": round(mom, 3),
            "volume": round(vol, 3),
            "sentiment": round(sent_mag, 3),
            "liquidity": round(liq_f, 3),
        },
    )
