"""Exit plan. Computed for every passing candidate, never optional."""
from __future__ import annotations

from datetime import date, timedelta

from .config import Config


def build_exit_plan(
    strike: float, bid: float, expiry: date, cfg: Config
) -> dict:
    buyback = round(bid * (1 - cfg.profit_target_pct), 2)
    time_stop = expiry - timedelta(days=cfg.time_stop_dte)
    return {
        "profit_target_price": buyback,
        "profit_target_pct": cfg.profit_target_pct,
        "time_stop_date": time_stop,
        "time_stop_dte": cfg.time_stop_dte,
        "max_rolls": cfg.max_rolls,
        "roll_rule": "down and out, net credit only",
        "cost_basis_if_assigned": round(strike - bid, 2),
    }
