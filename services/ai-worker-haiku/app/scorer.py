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

_N_FACTORS = 4


@dataclass(frozen=True, slots=True)
class ScorerConfig:
    # Weights sum to 1.0 (validated below).
    w_momentum: float = 0.35
    w_volume: float = 0.25
    w_sentiment: float = 0.25
    w_liquidity: float = 0.15
    # Normalization caps — a factor at/above its cap saturates to 1.0.
    mom_cap_pct: float = 15.0  # |24h change %|
    vol_cap: float = 5.0  # volume spike ratio
    liq_cap_usd: float = 1_000_000.0  # liquidity for the safety factor
    thin_liq_usd: float = 50_000.0  # below this, a big move is "thin/ambiguous"
    escalate_score: int = 60  # min local score to consider LLM escalation

    def __post_init__(self) -> None:
        total = self.w_momentum + self.w_volume + self.w_sentiment + self.w_liquidity
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0, got {total}")


@dataclass(frozen=True, slots=True)
class ScoreResult:
    opportunity_score: int  # 0-100
    confidence: float  # 0-1
    reason: str
    escalate: bool  # worth a senior (LLM) look?
    ambiguous: bool
    # Diagnostics — why a signal did or did not reach the senior analyst, and
    # how much evidence the score was actually computed from. A score built on
    # 2 of 4 factors is not comparable to one built on 4 of 4; the funnel needs
    # to tell them apart before any threshold is tuned.
    # unknown (never scored) | escalated | score_below_threshold | gate_not_met
    block_reason: str = "unknown"
    factors_present: int = 0  # 0-4
    # dex (DexScreener) | volume_proxy (24h volume stand-in) | unknown
    liquidity_source: str = "unknown"
    factors: dict[str, float] = field(default_factory=dict)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def local_opportunity(f: dict, cfg: ScorerConfig | None = None) -> ScoreResult:
    """Deterministic opportunity score for one symbol's correlated features."""
    cfg = cfg or ScorerConfig()
    chg = f.get("price_change_pct_24h")
    vol_spike = f.get("volume_spike_ratio")
    sent = f.get("sentiment_score")  # expected ~[-1, 1]
    liq = f.get("liquidity_usd")

    # A dex pair with no liquidity reading arrives as 0.0, not None
    # (worker.py: `float(event.liquidity_usd or 0)`), and a 0x volume ratio is
    # not a reading either. One predicate per factor, so the score, the reason
    # string and the diagnostics can never disagree about what was supplied.
    has_chg = chg is not None
    has_vol = vol_spike is not None and vol_spike > 0
    has_sent = sent is not None
    has_liq = liq is not None and liq > 0

    # CEX-listed pairs are not covered by DexScreener, so they used to land on
    # the neutral 0.5 -- indistinguishable from a genuinely thin market. The
    # 24h volume already rides on the PriceEvent and is a defensible stand-in,
    # normalized identically below. `liquidity_source` keeps the two apart:
    # without it, calibration would treat an estimate and a measurement as the
    # same evidence.
    proxy = f.get("volume_24h_usd")
    has_proxy = not has_liq and proxy is not None and float(proxy) > 0
    if has_proxy:
        liq = float(proxy)

    # How many of the four factors were actually supplied (0-_N_FACTORS).
    # Missing momentum/volume/sentiment normalize to 0.0 and missing liquidity
    # to a neutral 0.5, so the score alone cannot say whether a low value means
    # "weak" or "unknown". factors_present is what tells the two apart.
    factors_present = sum((has_chg, has_vol, has_sent, has_liq or has_proxy))
    liquidity_source = "dex" if has_liq else "volume_proxy" if has_proxy else "unknown"

    # Normalize each factor to [0,1] (magnitude of a tradeable setup).
    mom = _clamp(abs(chg) / cfg.mom_cap_pct, 0.0, 1.0) if has_chg else 0.0
    vol = _clamp((vol_spike - 1.0) / (cfg.vol_cap - 1.0), 0.0, 1.0) if has_vol else 0.0
    sent_mag = _clamp(abs(sent), 0.0, 1.0) if has_sent else 0.0
    # Unknown liquidity is treated as neutral (0.5) rather than penalized to 0.
    liq_f = (
        _clamp(math.log10(max(liq, 1.0)) / math.log10(cfg.liq_cap_usd), 0.0, 1.0)
        if (has_liq or has_proxy)
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
        has_chg
        and has_sent
        and abs(chg) > 2.0
        and abs(sent) > 0.2
        and (chg > 0) != (sent > 0)
    )
    # `has_liq`, not the proxy: a thin *measured* pool is a reason to pay for
    # an LLM look, an estimate derived from volume is not sure enough to
    # spend on.
    thin_liq_big_move = has_liq and liq < cfg.thin_liq_usd and mom > 0.5
    ambiguous = bool(disagreement or thin_liq_big_move)

    # Confidence measures how much we trust the *data*, so factor coverage must
    # outweigh the neutral value substituted for unknown liquidity — otherwise
    # knowing nothing scores higher than knowing everything about an illiquid
    # pair. Bounded to [0.25, 1.00] by construction; the attainable floor is
    # 0.35, since liq_f = 0 implies liquidity was supplied and so fp >= 1.
    # The risk engine floors at 0.55.
    # Monotonic in factors_present at fixed liq_f. A *measured* liquidity under
    # ~$20 still scores below an unknown one (0.65 vs 0.73) — that is deliberate
    # distrust of a dead pool, not an inversion.
    confidence = round(0.25 + 0.35 * liq_f + 0.4 * (factors_present / _N_FACTORS), 2)

    bits: list[str] = []
    if has_chg:
        bits.append(f"24h {chg:+.1f}%")
    if has_vol:
        bits.append(f"vol x{vol_spike:.1f}")
    if has_sent:
        bits.append(f"sent {sent:+.2f}")
    if has_liq:
        bits.append(f"liq ${liq:,.0f}")
    elif has_proxy:
        # Marked as estimated in the operator-facing reason: the number carries
        # the same weight in the score but not the same authority.
        bits.append(f"liq ~${liq:,.0f} (volume)")
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
