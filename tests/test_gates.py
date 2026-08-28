"""Gate tests. A silent bug here costs real money."""
from datetime import date, timedelta

import pytest

from src import gates
from src.config import Config

TODAY = date(2026, 9, 1)
EXPIRY = date(2026, 10, 9)  # 38 DTE


def make_config(**over):
    raw = {
        "account": {"capital": 15000},
        "mode": {"alerts_enabled": False},
        "enabled_tiers": ["a", "b", "c"],
        "universal": {
            "dte_min": 30, "dte_max": 45,
            "delta_min": 0.15, "delta_max": 0.30,
            "annualised_roc_min": 0.15,
            "min_buffer_ratio": 0.85,
            "require_iv_above_realised": True,
            "iv_rank_min": 30,
            "earnings_before_expiry": "exclude",
            "max_pct_capital_per_name": 0.20,
            "max_pct_capital_per_sector": 0.30,
            "max_concurrent_positions": 3,
            "top_n_alerts": 5,
        },
        "tiers": {
            "a": {"strike_min": 0, "strike_max": 50, "min_bid": 0.15,
                  "min_premium_pct_of_strike": 0.006,
                  "max_spread_pct_of_mid": 0.12, "max_spread_abs": 0.06,
                  "min_open_interest": 500, "min_contract_volume": 20,
                  "min_underlying_avg_volume": 5000000},
            "b": {"strike_min": 50, "strike_max": 150, "min_bid": 0.40,
                  "min_premium_pct_of_strike": 0.007,
                  "max_spread_pct_of_mid": 0.08, "max_spread_abs": 0.10,
                  "min_open_interest": 300, "min_contract_volume": 20,
                  "min_underlying_avg_volume": 2000000},
            "c": {"strike_min": 150, "strike_max": 400, "min_bid": 1.00,
                  "min_premium_pct_of_strike": 0.008,
                  "max_spread_pct_of_mid": 0.05, "max_spread_abs": 0.20,
                  "min_open_interest": 200, "min_contract_volume": 10,
                  "min_underlying_avg_volume": 1000000},
        },
        "scoring": {"weight_protection": 0.40, "weight_payoff": 0.35,
                    "weight_liquidity": 0.25,
                    "protection_full_score_ratio": 1.5, "min_buffer_ratio": 0.85,
                    "payoff_full_score_roc": 0.40},
        "exits": {"profit_target_pct": 0.50, "time_stop_dte": 21,
                  "max_rolls": 2},
    }
    for k, v in over.items():
        raw["universal"][k] = v
    return Config(raw)


CFG = make_config()


# ------------------------------------------------------------ Gate 2 events

def test_earnings_before_expiry_fails():
    r = gates.gate_events(date(2026, 9, 20), None, EXPIRY, TODAY)
    assert not r.passed
    assert "earnings" in r.reason


def test_earnings_after_expiry_passes():
    r = gates.gate_events(date(2026, 11, 1), None, EXPIRY, TODAY)
    assert r.passed


def test_missing_earnings_fails_closed():
    """The critical one. Unknown must never mean allowed."""
    r = gates.gate_events(None, None, EXPIRY, TODAY)
    assert not r.passed
    assert "failing closed" in r.reason


def test_ex_div_flags_but_passes():
    r = gates.gate_events(date(2026, 12, 1), date(2026, 9, 15), EXPIRY, TODAY)
    assert r.passed
    assert "ex-div" in r.reason


# -------------------------------------------------------- Gate 3 volatility

def flat_closes(n=200, start=100.0, drift=0.0005):
    return [start * (1 + drift) ** i for i in range(n)]


def noisy_closes(n=200, start=100.0, amp=0.02):
    import math
    return [start * (1 + amp * math.sin(i)) for i in range(n)]


def test_iv_below_realised_fails():
    r = gates.gate_volatility(0.05, noisy_closes(), [], CFG)
    assert not r.passed
    assert "not above realised" in r.reason


def test_iv_above_realised_passes():
    r = gates.gate_volatility(0.45, flat_closes(), [], CFG)
    assert r.passed


def test_missing_iv_fails_closed():
    r = gates.gate_volatility(None, flat_closes(), [], CFG)
    assert not r.passed


def test_nan_iv_fails_closed():
    r = gates.gate_volatility(float("nan"), flat_closes(), [], CFG)
    assert not r.passed


def test_short_price_history_fails():
    r = gates.gate_volatility(0.45, [100.0] * 10, [], CFG)
    assert not r.passed


def test_iv_rank_ignored_without_enough_history():
    """Under 250 rows, the rank check must be skipped, not failed."""
    r = gates.gate_volatility(0.45, flat_closes(), [0.9] * 50, CFG)
    assert r.passed


def test_iv_rank_applied_with_full_history():
    history = [0.20 + i * 0.001 for i in range(300)]  # 0.20 to 0.50
    r = gates.gate_volatility(0.21, flat_closes(), history, CFG)
    assert not r.passed
    assert "rank" in r.reason


# ------------------------------------------------------------ Gate 4 strike

def test_delta_too_high_fails():
    r = gates.gate_strike(100, 98, 2.0, 0.45, 0.30, 38, CFG)
    assert not r.passed
    assert "delta" in r.reason


def test_delta_too_low_fails():
    r = gates.gate_strike(100, 80, 0.5, 0.05, 0.30, 38, CFG)
    assert not r.passed


def test_missing_delta_fails_closed():
    r = gates.gate_strike(100, 90, 1.0, None, 0.30, 38, CFG)
    assert not r.passed
    assert "failing closed" in r.reason


def test_breakeven_inside_expected_move_fails():
    # spot 100, iv 40%, 38 DTE -> EM about 12.9. Strike 96 gives 0.39x.
    r = gates.gate_strike(100, 96, 1.0, 0.25, 0.40, 38, CFG)
    assert not r.passed
    assert "buffer" in r.reason


def test_good_strike_passes():
    r = gates.gate_strike(100, 85, 1.0, 0.20, 0.40, 38, CFG)
    assert r.passed
    assert r.value["buffer_ratio"] > 1.0


def test_expected_move_math():
    em = gates.expected_move(100, 0.40, 365)
    assert em == pytest.approx(40.0, rel=0.01)


# --------------------------------------------------- Gate 5 contract quality

def tier_a():
    return CFG.tiers["a"]


def test_dte_outside_window_fails():
    r = gates.gate_contract_quality(40, 0.50, 0.55, 15, 1000, 50,
                                    9_000_000, tier_a(), CFG)
    assert not r.passed
    assert "DTE" in r.reason


def test_bid_below_tier_min_fails():
    r = gates.gate_contract_quality(40, 0.10, 0.12, 38, 1000, 50,
                                    9_000_000, tier_a(), CFG)
    assert not r.passed


def test_premium_pct_floor_catches_cheap_premium_on_big_strike():
    """0.50 on a 300 strike is 0.17%. Must fail even though bid > min_bid."""
    r = gates.gate_contract_quality(300, 1.10, 1.15, 38, 1000, 50,
                                    9_000_000, CFG.tiers["c"], CFG)
    assert not r.passed
    assert "below" in r.reason


def test_wide_spread_fails():
    r = gates.gate_contract_quality(40, 0.50, 0.90, 38, 1000, 50,
                                    9_000_000, tier_a(), CFG)
    assert not r.passed
    assert "spread" in r.reason


def test_narrow_absolute_spread_passes_despite_pct():
    """0.02 spread on a 0.20 bid is 9.5% but only 2 cents. Must pass."""
    r = gates.gate_contract_quality(40, 0.30, 0.34, 38, 1000, 50,
                                    9_000_000, tier_a(), CFG)
    assert r.passed


def test_missing_open_interest_fails_closed():
    r = gates.gate_contract_quality(40, 0.50, 0.53, 38, None, 50,
                                    9_000_000, tier_a(), CFG)
    assert not r.passed


def test_missing_bid_fails_closed():
    r = gates.gate_contract_quality(40, None, 0.53, 38, 1000, 50,
                                    9_000_000, tier_a(), CFG)
    assert not r.passed


def test_good_contract_passes():
    r = gates.gate_contract_quality(40, 0.50, 0.53, 38, 1000, 50,
                                    9_000_000, tier_a(), CFG)
    assert r.passed


# ------------------------------------------------------------ Gate 6 return

def test_annualised_roc_math():
    # 1.00 premium on 39.00 collateral over 36.5 days
    roc = gates.annualised_roc(1.00, 40.0, 36.5)
    assert roc == pytest.approx((1 / 39) * 10, rel=0.01)


def test_low_roc_fails():
    r = gates.gate_return(0.20, 40.0, 38, CFG)
    assert not r.passed


def test_good_roc_passes():
    r = gates.gate_return(0.80, 40.0, 38, CFG)
    assert r.passed


# --------------------------------------------------------- Gate 7 portfolio

def test_collateral_over_per_name_cap_fails():
    # capital 15000, cap 20% = 3000. Strike 45 needs about 4400.
    r = gates.gate_portfolio("F", "Tech", 45, 0.50, [], CFG)
    assert not r.passed
    assert "per-name cap" in r.reason


def test_duplicate_ticker_fails():
    pos = [{"ticker": "F", "sector": "Consumer", "collateral": 1200}]
    r = gates.gate_portfolio("F", "Consumer", 12, 0.30, pos, CFG)
    assert not r.passed
    assert "already holding" in r.reason


def test_sector_cap_fails():
    # capital 15000, sector cap 30% = 4500
    pos = [{"ticker": "T", "sector": "Comms", "collateral": 4000}]
    r = gates.gate_portfolio("VZ", "Comms", 25, 0.40, pos, CFG)
    assert not r.passed
    assert "exposure" in r.reason


def test_max_positions_fails():
    pos = [{"ticker": t, "sector": "X", "collateral": 100}
           for t in ("A", "B", "C")]
    r = gates.gate_portfolio("F", "Consumer", 12, 0.30, pos, CFG)
    assert not r.passed
    assert "max" in r.reason


def test_clean_portfolio_passes():
    r = gates.gate_portfolio("F", "Consumer", 12, 0.30, [], CFG)
    assert r.passed


# ------------------------------------------------------------------ scoring

def test_scoring_bounds():
    from src.scoring import score_contract
    lo = score_contract(0.1, 0.15, 0.10, 0, 0, CFG)
    hi = score_contract(3.0, 0.60, 0.001, 5000, 500, CFG)
    assert 0 <= lo["score"] <= 10
    assert 0 <= hi["score"] <= 10
    assert hi["score"] > lo["score"]


# -------------------------------------------------------------- exit plan

def test_exit_plan():
    from src.exits import build_exit_plan
    p = build_exit_plan(40.0, 1.00, EXPIRY, CFG)
    assert p["profit_target_price"] == 0.50
    assert p["time_stop_date"] == EXPIRY - timedelta(days=21)
    assert p["cost_basis_if_assigned"] == 39.0
