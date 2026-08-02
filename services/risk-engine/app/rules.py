"""Pure risk rules: stop-loss / take-profit / position sizing. No I/O."""

from __future__ import annotations

from dataclasses import dataclass

from cmi_common.events.decision import Direction


@dataclass(slots=True)
class RiskConfig:
    stop_loss_pct: float = 0.05  # 5% below entry (long)
    take_profit_pct: float = 0.10  # 10% above entry (long)
    max_position_pct: float = 0.05  # max 5% of portfolio per trade
    #: Rescale x0.92 en meme temps que WEIGHTS lors de l'ajout du huitieme
    #: axe. `confidence` est une somme *absolue* de poids, exactement comme
    #: _MIN_PRESENT_WEIGHT: chaque axe valant 8% de moins, laisser 0.55 en
    #: place aurait rejete 18 combinaisons d'evidence auparavant approuvees
    #: et, la taille de position etant lineaire en confiance, aurait
    #: retreci *toutes* les positions de 8%. Ajouter un axe ne doit pas
    #: durcir un critere que personne n'a decide de durcir.
    #:
    #: Le lien est verifie par tests/test_risk_confidence_floor.py, faute de
    #: quoi rien ne relie ce nombre a WEIGHTS.
    min_confidence: float = 0.506
    min_score: int = 70
    min_risk_reward: float = 1.5


@dataclass(slots=True)
class RiskLevels:
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size_pct: float
    risk_reward_ratio: float


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    reason: str
    levels: RiskLevels | None = None


def evaluate(
    *,
    entry_price: float,
    direction: Direction,
    confidence: float,
    opportunity_score: int,
    is_blacklisted: bool,
    current_exposure_pct: float,
    config: RiskConfig,
) -> RiskDecision:
    """Deterministically validate a decision and size the position."""
    if is_blacklisted:
        return RiskDecision(False, "token blacklisted")
    if entry_price <= 0:
        return RiskDecision(False, "no valid entry price")
    # WATCH means "keep an eye on this", not "take a position". It is rejected
    # categorically — before the numeric floors — so no tuning of those floors
    # can ever let one through. Downstream, the trading engine maps every
    # non-LONG direction to a SELL, so an approved watch would open a real short.
    if direction == Direction.WATCH:
        return RiskDecision(False, "watch is not an actionable direction")
    if confidence < config.min_confidence:
        return RiskDecision(False, f"confidence {confidence:.2f} below floor")
    if opportunity_score < config.min_score:
        return RiskDecision(False, f"score {opportunity_score} below floor")

    levels = _compute_levels(entry_price, direction, confidence, config)
    if levels.risk_reward_ratio < config.min_risk_reward:
        return RiskDecision(
            False, f"risk/reward {levels.risk_reward_ratio:.2f} too low"
        )
    if current_exposure_pct + levels.position_size_pct > 1.0:
        return RiskDecision(False, "max portfolio exposure reached")
    return RiskDecision(True, "approved", levels)


def _compute_levels(
    entry: float, direction: Direction, confidence: float, cfg: RiskConfig
) -> RiskLevels:
    if direction == Direction.SHORT:
        stop = entry * (1 + cfg.stop_loss_pct)
        target = entry * (1 - cfg.take_profit_pct)
        risk = stop - entry
        reward = entry - target
    else:  # LONG only — evaluate() rejects WATCH before any sizing happens
        stop = entry * (1 - cfg.stop_loss_pct)
        target = entry * (1 + cfg.take_profit_pct)
        risk = entry - stop
        reward = target - entry

    rr = round(reward / risk, 3) if risk > 0 else 0.0
    # Confidence-scaled fractional sizing, capped at the per-trade max.
    size = round(cfg.max_position_pct * min(1.0, confidence), 4)
    return RiskLevels(
        entry_price=round(entry, 8),
        stop_loss=round(stop, 8),
        take_profit=round(target, 8),
        position_size_pct=size,
        risk_reward_ratio=rr,
    )
