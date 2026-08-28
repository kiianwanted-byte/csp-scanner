"""Market data access. Everything goes through DataProvider.

Swapping yfinance for Tradier later must only touch this file.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol


@dataclass
class PutContract:
    ticker: str
    strike: float
    expiry: date
    dte: int
    bid: float | None
    ask: float | None
    last: float | None
    implied_volatility: float | None
    delta: float | None
    open_interest: int | None
    volume: int | None


@dataclass
class UnderlyingSnapshot:
    ticker: str
    spot: float
    avg_volume_30d: float | None
    closes: list[float] = field(default_factory=list)
    earnings_date: date | None = None
    ex_div_date: date | None = None


class DataProvider(ABC):
    @abstractmethod
    def get_underlying(self, ticker: str) -> UnderlyingSnapshot | None:
        ...

    @abstractmethod
    def get_puts(
        self, ticker: str, dte_min: int, dte_max: int
    ) -> list[PutContract]:
        ...


# ------------------------------------------------------------ yfinance impl


class YFinanceProvider(DataProvider):
    """Free but unofficial. Expect breakage a few times a year."""

    def __init__(self, request_delay: float = 0.5, max_retries: int = 3):
        self.delay = request_delay
        self.max_retries = max_retries
        import yfinance as yf  # imported lazily so tests don't need it
        self._yf = yf

    def _retry(self, fn, label: str):
        last = None
        for attempt in range(self.max_retries):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(self.delay * (attempt + 1))
        print(f"  [data] {label} failed after {self.max_retries} tries: {last}")
        return None

    def get_underlying(self, ticker: str) -> UnderlyingSnapshot | None:
        tk = self._yf.Ticker(ticker)

        hist = self._retry(
            lambda: tk.history(period="6mo", interval="1d"), f"{ticker} history"
        )
        if hist is None or hist.empty:
            return None

        closes = [float(c) for c in hist["Close"].dropna().tolist()]
        if not closes:
            return None
        spot = closes[-1]

        vols = hist["Volume"].dropna().tolist()[-30:]
        avg_vol = float(sum(vols) / len(vols)) if vols else None

        earnings_date = self._earnings_date(tk)
        ex_div = self._ex_div_date(tk)

        time.sleep(self.delay)
        return UnderlyingSnapshot(
            ticker=ticker,
            spot=spot,
            avg_volume_30d=avg_vol,
            closes=closes,
            earnings_date=earnings_date,
            ex_div_date=ex_div,
        )

    def _earnings_date(self, tk) -> date | None:
        """Returns None on failure. Caller must fail closed."""
        try:
            cal = tk.calendar
            if isinstance(cal, dict):
                vals = cal.get("Earnings Date")
                if vals:
                    v = vals[0] if isinstance(vals, (list, tuple)) else vals
                    return v.date() if isinstance(v, datetime) else v
            elif cal is not None and hasattr(cal, "empty") and not cal.empty:
                v = cal.iloc[0, 0]
                return v.date() if hasattr(v, "date") else v
        except Exception:  # noqa: BLE001
            pass
        return None

    def _ex_div_date(self, tk) -> date | None:
        try:
            info = tk.info or {}
            ts = info.get("exDividendDate")
            if ts:
                return datetime.fromtimestamp(ts).date()
        except Exception:  # noqa: BLE001
            pass
        return None

    def get_puts(self, ticker: str, dte_min: int, dte_max: int) -> list[PutContract]:
        tk = self._yf.Ticker(ticker)
        expiries = self._retry(lambda: tk.options, f"{ticker} expiries")
        if not expiries:
            return []

        today = date.today()
        out: list[PutContract] = []

        for exp_str in expiries:
            try:
                exp = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            dte = (exp - today).days
            if not (dte_min <= dte <= dte_max):
                continue

            chain = self._retry(
                lambda e=exp_str: tk.option_chain(e), f"{ticker} chain {exp_str}"
            )
            if chain is None:
                continue
            puts = chain.puts
            if puts is None or puts.empty:
                continue

            for _, r in puts.iterrows():
                out.append(PutContract(
                    ticker=ticker,
                    strike=float(r.get("strike", 0)),
                    expiry=exp,
                    dte=dte,
                    bid=_num(r.get("bid")),
                    ask=_num(r.get("ask")),
                    last=_num(r.get("lastPrice")),
                    implied_volatility=_num(r.get("impliedVolatility")),
                    delta=None,  # yfinance does not supply greeks
                    open_interest=_int(r.get("openInterest")),
                    volume=_int(r.get("volume")),
                ))
            time.sleep(self.delay)

        return out


def _num(x) -> float | None:
    try:
        v = float(x)
        return None if v != v else v  # NaN check
    except (TypeError, ValueError):
        return None


def _int(x) -> int | None:
    v = _num(x)
    return None if v is None else int(v)


# ------------------------------------------------------- delta computation

def bs_put_delta(spot: float, strike: float, iv: float, dte: int,
                 rate: float = 0.04) -> float | None:
    """Black-Scholes put delta. yfinance gives no greeks so we compute it.

    Returned as a positive number for convenience. Approximate: uses a
    fixed risk-free rate and ignores dividends.
    """
    import math

    if spot <= 0 or strike <= 0 or iv <= 0 or dte <= 0:
        return None
    t = dte / 365.0
    try:
        d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t) / (
            iv * math.sqrt(t)
        )
    except (ValueError, ZeroDivisionError):
        return None
    # N(-d1) via the error function
    nd1 = 0.5 * (1 + math.erf(-d1 / math.sqrt(2)))
    return nd1
