"""Gate functions. Pure, no I/O, no side effects.

Every gate returns a GateResult. Fail closed: if data needed for a decision
is missing or null, the gate FAILS. Never assume, never interpolate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from .config import Config, Tier


@dataclass(frozen=True)
class GateResult:
    passed: bool
    gate: str
    reason: str
    value: Any = None


def _missing(x: Any) -> bool:
    """True if a value is absent or not a usable number."""
    if x is None:
        return True
    if isinstance(x, str):
        return True
    try:
        return math.isnan(float(x))
    except (TypeError, ValueError):
        return True


# ---------------------------------------------------------------- Gate 2


def gate_events(
    earnings_date: date | None,
    ex_div_date: date | None,
    expiry: date,
    today: date,
    exclude_earnings: bool = True,
) -> GateResult:
    """No earnings between now and expiry. Fails closed on unknown."""
    if exclude_earnings:
        if earnings_date is None:
            return GateResult(
                False, "events", "earnings date unavailable, failing closed"
            )
        if today <= earnings_date <= expiry:
            return GateResult(
                False, "events", f"earnings {earnings_date} falls before expiry"
            )

    flag = ""
    if ex_div_date is not None and today <= ex_div_date <= expiry:
        flag = f" (ex-div {ex_div_date}, early assignment risk)"
    return GateResult(True, "events", "no earnings before expiry" + flag)


# ---------------------------------------------------------------- Gate 3


def realised_volatility(closes: list[float], window: int = 30) -> float | None:
    """Annualised stdev of log returns. None if not enough data."""
    if closes is None or len(closes) < window + 1:
        return None
    recent = closes[-(window + 1):]
    rets = []
    for prev, curr in zip(recent[:-1], recent[1:]):
        if prev is None or curr is None or prev <= 0 or curr <= 0:
            return None
        rets.append(math.log(curr / prev))
    n = len(rets)
    if n < 2:
        return None
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var) * math.sqrt(252)


def iv_rank(current_iv: float, iv_history: list[float]) -> float | None:
    """Percentile position of current IV within its own history, 0-100."""
    if not iv_history or len(iv_history) < 250:
        return None
    lo, hi = min(iv_history), max(iv_history)
    if hi <= lo:
        return None
    return max(0.0, min(100.0, (current_iv - lo) / (hi - lo) * 100))


def gate_volatility(
    iv: float | None,
    closes: list[float],
    iv_history: list[float],
    cfg: Config,
) -> GateResult:
    if _missing(iv):
        return GateResult(False, "volatility", "IV unavailable, failing closed")
    iv = float(iv)

    if cfg.require_iv_above_realised:
        rv = realised_volatility(closes)
        if rv is None:
            return GateResult(
                False, "volatility", "insufficient price history for realised vol"
            )
        if iv <= rv:
            return GateResult(
                False,
                "volatility",
                f"IV {iv:.1%} not above realised vol {rv:.1%}",
                {"iv": iv, "rv": rv},
            )

    rank = iv_rank(iv, iv_history)
    if rank is not None and rank < cfg.iv_rank_min:
        return GateResult(
            False, "volatility", f"IV rank {rank:.0f} below {cfg.iv_rank_min:.0f}"
        )

    detail = f"IV {iv:.1%}"
    if rank is not None:
        detail += f", rank {rank:.0f}"
    return GateResult(True, "volatility", detail, {"iv": iv, "iv_rank": rank})


# ---------------------------------------------------------------- Gate 4


def expected_move(spot: float, iv: float, dte: int) -> float:
    """One standard deviation move over the holding period."""
    return spot * iv * math.sqrt(dte / 365.0)


def gate_strike(
    spot: float,
    strike: float,
    bid: float,
    delta: float | None,
    iv: float,
    dte: int,
    cfg: Config,
) -> GateResult:
    if _missing(delta):
        return GateResult(False, "strike", "delta unavailable, failing closed")
    d = abs(float(delta))

    if not (cfg.delta_min <= d <= cfg.delta_max):
        return GateResult(
            False,
            "strike",
            f"delta {d:.3f} outside {cfg.delta_min}-{cfg.delta_max}",
            d,
        )

    breakeven = strike - bid
    em = expected_move(spot, iv, dte)
    if em <= 0:
        return GateResult(False, "strike", "expected move computed as zero")

    buffer_ratio = (spot - breakeven) / em
    if buffer_ratio < cfg.min_buffer_ratio:
        return GateResult(
            False,
            "strike",
            f"buffer {buffer_ratio:.2f}x below min {cfg.min_buffer_ratio:.2f}x "
            f"expected move",
            buffer_ratio,
        )

    return GateResult(
        True,
        "strike",
        f"delta {d:.3f}, buffer {buffer_ratio:.2f}x expected move",
        {"delta": d, "breakeven": breakeven, "buffer_ratio": buffer_ratio,
         "expected_move": em},
    )


# ---------------------------------------------------------------- Gate 5


def gate_contract_quality(
    strike: float,
    bid: float | None,
    ask: float | None,
    dte: int,
    open_interest: int | None,
    contract_volume: int | None,
    underlying_avg_volume: float | None,
    tier: Tier,
    cfg: Config,
) -> GateResult:
    if not (cfg.dte_min <= dte <= cfg.dte_max):
        return GateResult(
            False, "contract", f"DTE {dte} outside {cfg.dte_min}-{cfg.dte_max}", dte
        )

    if _missing(bid) or _missing(ask):
        return GateResult(False, "contract", "bid or ask unavailable, failing closed")
    bid, ask = float(bid), float(ask)

    if bid < tier.min_bid:
        return GateResult(
            False, "contract", f"bid {bid:.2f} below tier min {tier.min_bid:.2f}", bid
        )

    min_prem = tier.min_premium_pct_of_strike * strike
    if bid < min_prem:
        return GateResult(
            False,
            "contract",
            f"premium {bid:.2f} below {tier.min_premium_pct_of_strike:.2%} "
            f"of strike ({min_prem:.2f})",
            bid,
        )

    mid = (bid + ask) / 2
    if mid <= 0:
        return GateResult(False, "contract", "mid price is zero or negative")
    spread = ask - bid
    spread_pct = spread / mid
    # Passes on EITHER test, whichever is more permissive.
    if spread_pct > tier.max_spread_pct_of_mid and spread > tier.max_spread_abs:
        return GateResult(
            False,
            "contract",
            f"spread {spread:.2f} ({spread_pct:.1%}) too wide",
            spread_pct,
        )

    if _missing(open_interest) or int(open_interest) < tier.min_open_interest:
        oi = "unavailable" if _missing(open_interest) else int(open_interest)
        return GateResult(
            False, "contract", f"open interest {oi} below {tier.min_open_interest}"
        )

    if _missing(contract_volume) or int(contract_volume) < tier.min_contract_volume:
        v = "unavailable" if _missing(contract_volume) else int(contract_volume)
        return GateResult(
            False, "contract", f"contract volume {v} below {tier.min_contract_volume}"
        )

    if (_missing(underlying_avg_volume)
            or float(underlying_avg_volume) < tier.min_underlying_avg_volume):
        return GateResult(
            False,
            "contract",
            f"underlying volume below {tier.min_underlying_avg_volume:,}",
        )

    return GateResult(
        True,
        "contract",
        f"spread {spread_pct:.1%}, OI {int(open_interest)}",
        {"spread_pct": spread_pct, "open_interest": int(open_interest)},
    )


# ---------------------------------------------------------------- Gate 6


def annualised_roc(bid: float, strike: float, dte: int) -> float:
    """Return on collateral, annualised. Uses bid, never mid."""
    collateral = strike - bid
    if collateral <= 0 or dte <= 0:
        return 0.0
    return (bid / collateral) * (365.0 / dte)


def gate_return(bid: float, strike: float, dte: int, cfg: Config) -> GateResult:
    roc = annualised_roc(bid, strike, dte)
    if roc < cfg.annualised_roc_min:
        return GateResult(
            False,
            "return",
            f"annualised ROC {roc:.1%} below {cfg.annualised_roc_min:.1%}",
            roc,
        )
    return GateResult(True, "return", f"annualised ROC {roc:.1%}", roc)


# ---------------------------------------------------------------- Gate 7


def gate_portfolio(
    ticker: str,
    sector: str,
    strike: float,
    bid: float,
    open_positions: list[dict],
    cfg: Config,
) -> GateResult:
    collateral = (strike - bid) * 100

    cap_per_name = cfg.max_pct_capital_per_name * cfg.capital
    if collateral > cap_per_name:
        return GateResult(
            False,
            "portfolio",
            f"collateral ${collateral:,.0f} exceeds per-name cap "
            f"${cap_per_name:,.0f}",
            collateral,
        )

    if len(open_positions) >= cfg.max_concurrent_positions:
        return GateResult(
            False,
            "portfolio",
            f"already at max {cfg.max_concurrent_positions} concurrent positions",
        )

    if any(p.get("ticker", "").upper() == ticker.upper() for p in open_positions):
        return GateResult(False, "portfolio", f"already holding a position in {ticker}")

    sector_used = sum(
        float(p.get("collateral", 0) or 0)
        for p in open_positions
        if str(p.get("sector", "")).lower() == sector.lower()
    )
    cap_per_sector = cfg.max_pct_capital_per_sector * cfg.capital
    if sector_used + collateral > cap_per_sector:
        return GateResult(
            False,
            "portfolio",
            f"{sector} exposure would reach "
            f"${sector_used + collateral:,.0f}, cap ${cap_per_sector:,.0f}",
        )

    return GateResult(
        True, "portfolio", f"collateral ${collateral:,.0f}", collateral
    )
