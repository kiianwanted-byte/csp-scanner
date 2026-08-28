"""Scores contracts that passed every gate. 0 to 10."""
from __future__ import annotations

from .config import Config


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def score_contract(
    buffer_ratio: float,
    ann_roc: float,
    spread_pct: float,
    open_interest: int,
    contract_volume: int,
    cfg: Config,
) -> dict:
    """Weighted score. Returns total plus the components for logging."""
    protection = _clamp01(buffer_ratio / cfg.protection_full_score_ratio)

    roc_floor = cfg.annualised_roc_min
    roc_ceiling = cfg.payoff_full_score_roc
    if roc_ceiling <= roc_floor:
        payoff = 1.0 if ann_roc >= roc_ceiling else 0.0
    else:
        payoff = _clamp01((ann_roc - roc_floor) / (roc_ceiling - roc_floor))

    # Liquidity: tight spread matters most, then depth.
    spread_score = _clamp01(1 - (spread_pct / 0.10))
    oi_score = _clamp01(open_interest / 2000)
    vol_score = _clamp01(contract_volume / 200)
    liquidity = spread_score * 0.6 + oi_score * 0.25 + vol_score * 0.15

    total = (
        protection * cfg.weight_protection
        + payoff * cfg.weight_payoff
        + liquidity * cfg.weight_liquidity
    ) * 10

    return {
        "score": round(total, 1),
        "protection": round(protection * 10, 1),
        "payoff": round(payoff * 10, 1),
        "liquidity": round(liquidity * 10, 1),
    }
