"""Loads and validates config.yaml. Fails loudly on bad values."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Raised when config.yaml is missing or invalid."""


@dataclass(frozen=True)
class Tier:
    name: str
    strike_min: float
    strike_max: float
    min_bid: float
    min_premium_pct_of_strike: float
    max_spread_pct_of_mid: float
    max_spread_abs: float
    min_open_interest: int
    min_contract_volume: int
    min_underlying_avg_volume: int


class Config:
    def __init__(self, raw: dict[str, Any]):
        self._raw = raw
        self.capital: float = float(self._get("account", "capital"))
        self.currency: str = raw["account"].get("currency", "USD")

        mode = raw.get("mode", {})
        self.alerts_enabled: bool = bool(mode.get("alerts_enabled", False))
        self.liveness_ping: bool = bool(mode.get("liveness_ping", True))
        self.liveness_day: str = str(mode.get("liveness_day", "monday")).lower()

        self.enabled_tiers: list[str] = [
            str(t).lower() for t in raw.get("enabled_tiers", ["a"])
        ]

        u = self._section("universal")
        self.dte_min: int = int(u["dte_min"])
        self.dte_max: int = int(u["dte_max"])
        self.delta_min: float = float(u["delta_min"])
        self.delta_max: float = float(u["delta_max"])
        self.annualised_roc_min: float = float(u["annualised_roc_min"])
        self.min_buffer_ratio: float = float(u.get("min_buffer_ratio", 0.85))
        self.require_iv_above_realised: bool = bool(
            u.get("require_iv_above_realised", True)
        )
        self.iv_rank_min: float = float(u.get("iv_rank_min", 30))
        self.exclude_earnings: bool = (
            str(u.get("earnings_before_expiry", "exclude")).lower() == "exclude"
        )
        self.max_pct_capital_per_name: float = float(u["max_pct_capital_per_name"])
        self.max_pct_capital_per_sector: float = float(u["max_pct_capital_per_sector"])
        self.max_concurrent_positions: int = int(u.get("max_concurrent_positions", 3))
        self.top_n_alerts: int = int(u.get("top_n_alerts", 5))

        self.tiers: dict[str, Tier] = {}
        for name, t in self._section("tiers").items():
            key = str(name).lower()
            self.tiers[key] = Tier(
                name=key,
                strike_min=float(t["strike_min"]),
                strike_max=float(t["strike_max"]),
                min_bid=float(t["min_bid"]),
                min_premium_pct_of_strike=float(t["min_premium_pct_of_strike"]),
                max_spread_pct_of_mid=float(t["max_spread_pct_of_mid"]),
                max_spread_abs=float(t["max_spread_abs"]),
                min_open_interest=int(t["min_open_interest"]),
                min_contract_volume=int(t["min_contract_volume"]),
                min_underlying_avg_volume=int(t["min_underlying_avg_volume"]),
            )

        s = self._section("scoring")
        self.weight_protection: float = float(s["weight_protection"])
        self.weight_payoff: float = float(s["weight_payoff"])
        self.weight_liquidity: float = float(s["weight_liquidity"])
        self.protection_full_score_ratio: float = float(s["protection_full_score_ratio"])
        self.payoff_full_score_roc: float = float(s["payoff_full_score_roc"])

        e = self._section("exits")
        self.profit_target_pct: float = float(e["profit_target_pct"])
        self.time_stop_dte: int = int(e["time_stop_dte"])
        self.max_rolls: int = int(e["max_rolls"])

        d = raw.get("data", {})
        self.provider: str = d.get("provider", "yfinance")
        self.request_delay_seconds: float = float(d.get("request_delay_seconds", 0.5))
        self.max_retries: int = int(d.get("max_retries", 3))

        self._validate()

    def _section(self, name: str) -> dict:
        if name not in self._raw:
            raise ConfigError(f"config.yaml missing required section: {name}")
        return self._raw[name]

    def _get(self, section: str, key: str):
        sec = self._section(section)
        if key not in sec:
            raise ConfigError(f"config.yaml missing {section}.{key}")
        return sec[key]

    def _validate(self) -> None:
        if self.capital <= 0:
            raise ConfigError("account.capital must be greater than zero")
        if self.dte_min >= self.dte_max:
            raise ConfigError("dte_min must be less than dte_max")
        if self.delta_min >= self.delta_max:
            raise ConfigError("delta_min must be less than delta_max")
        if not 0 < self.min_buffer_ratio <= 3:
            raise ConfigError("min_buffer_ratio must be between 0 and 3")
        if not 0 < self.delta_max < 1:
            raise ConfigError("delta_max must be between 0 and 1")
        if not self.enabled_tiers:
            raise ConfigError("enabled_tiers cannot be empty")
        for t in self.enabled_tiers:
            if t not in self.tiers:
                raise ConfigError(f"enabled tier '{t}' has no definition in tiers")
        weights = self.weight_protection + self.weight_payoff + self.weight_liquidity
        if abs(weights - 1.0) > 0.001:
            raise ConfigError(f"scoring weights must sum to 1.0, got {weights}")
        if not 0 < self.profit_target_pct < 1:
            raise ConfigError("profit_target_pct must be between 0 and 1")

    def tier_for_strike(self, strike: float) -> Tier | None:
        """Return the enabled tier covering this strike, or None."""
        for name in self.enabled_tiers:
            t = self.tiers[name]
            if t.strike_min < strike <= t.strike_max:
                return t
        return None


def load_config(path: str | Path = "config.yaml") -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    with p.open() as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml did not parse into a mapping")
    return Config(raw)
