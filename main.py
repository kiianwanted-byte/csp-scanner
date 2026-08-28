"""CSP Scanner. Orchestration only.

Run:  python main.py
      python main.py --tickers AAPL,F     (limit to specific tickers)
      python main.py --dry-run            (never send Telegram, even liveness)
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from src import gates, telegram
from src.market_hours import market_status
from src.config import ConfigError, load_config
from src.data import YFinanceProvider, bs_put_delta, get_usd_sgd
from src.exits import build_exit_plan
from src.logging_csv import ScanLog
from src.scoring import score_contract
from src.universe import load_open_positions, load_universe

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday"]


def load_iv_history(path="data/iv_history.csv") -> dict[str, list[float]]:
    p = Path(path)
    hist: dict[str, list[float]] = {}
    if not p.exists():
        return hist
    with p.open(newline="") as f:
        for row in csv.DictReader(f):
            t = (row.get("ticker") or "").strip().upper()
            try:
                iv = float(row.get("iv30", ""))
            except (TypeError, ValueError):
                continue
            hist.setdefault(t, []).append(iv)
    return hist


def append_iv_snapshot(ticker: str, iv: float, path="data/iv_history.csv") -> None:
    p = Path(path)
    new = not p.exists() or p.stat().st_size == 0
    with p.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "ticker", "iv30"])
        w.writerow([date.today().isoformat(), ticker, round(iv, 4)])


def gate_breakdown(path="data/scan_log.csv", days=7) -> list[tuple[str, int]]:
    """Which gate rejected the most over the last N days."""
    from collections import Counter
    from datetime import timedelta
    p = Path(path)
    if not p.exists():
        return []
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    c: Counter = Counter()
    with p.open(newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("timestamp") or "") < cutoff:
                continue
            g = row.get("failed_gate") or "PASSED"
            c[g] += 1
    return c.most_common()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", help="comma separated, overrides the universe")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}")
        return 1

    today = date.today()
    is_liveness_day = DAYS[today.weekday()] == cfg.liveness_day
    open_now, why = market_status()
    if not open_now:
        print(f"Not scanning: {why}")
        if (not args.dry_run and cfg.liveness_ping and is_liveness_day
                and "weekend" not in why and "holiday" not in why):
            pass  # a skipped weekday still reports on Monday below
        else:
            return 0
        return 0
    print(f"Market {why}")

    universe = load_universe()
    if args.tickers:
        wanted = {t.strip().upper() for t in args.tickers.split(",")}
        universe = [u for u in universe if u["ticker"] in wanted]
    if not universe:
        print("No approved tickers. Set approved=TRUE in data/tickers.csv.")
        return 0

    open_positions = load_open_positions()
    iv_history = load_iv_history()
    log = ScanLog()
    provider = YFinanceProvider(cfg.request_delay_seconds, cfg.max_retries)
    fx, fx_live = get_usd_sgd()
    print(f"USD/SGD {fx:.4f} ({'live' if fx_live else 'fallback'})")

    print(f"Scanning {len(universe)} tickers, tiers {cfg.enabled_tiers}, "
          f"alerts={'ON' if cfg.alerts_enabled else 'OFF'}")

    candidates: list[dict] = []
    evaluated = 0

    for entry in universe:
        ticker, sector = entry["ticker"], entry["sector"]
        print(f"\n{ticker}")

        snap = provider.get_underlying(ticker)
        if snap is None:
            print("  no underlying data, skipping")
            log.add(ticker=ticker, failed_gate="data",
                    reason="underlying fetch failed")
            continue

        puts = provider.get_puts(ticker, cfg.dte_min, cfg.dte_max)
        if not puts:
            print("  no puts in DTE window")
            log.add(ticker=ticker, failed_gate="data",
                    reason="no contracts in DTE window")
            continue

        atm_iv = None
        for c in puts:
            evaluated += 1
            base = dict(ticker=ticker, strike=c.strike, expiry=c.expiry.isoformat(),
                        dte=c.dte, bid=c.bid, ask=c.ask, iv=c.implied_volatility)

            tier = cfg.tier_for_strike(c.strike)
            if tier is None:
                log.add(**base, failed_gate="tier",
                        reason="strike outside enabled tiers")
                continue

            r = gates.gate_events(snap.earnings_date, snap.ex_div_date,
                                  c.expiry, today, cfg.exclude_earnings)
            if not r.passed:
                log.add(**base, failed_gate=r.gate, reason=r.reason)
                continue

            r = gates.gate_volatility(c.implied_volatility, snap.closes,
                                      iv_history.get(ticker, []), cfg)
            if not r.passed:
                log.add(**base, failed_gate=r.gate, reason=r.reason)
                continue
            iv = float(c.implied_volatility)
            if atm_iv is None or abs(c.strike - snap.spot) < abs(atm_iv[0] - snap.spot):
                atm_iv = (c.strike, iv)

            delta = c.delta
            if delta is None:
                delta = bs_put_delta(snap.spot, c.strike, iv, c.dte)
            base["delta"] = round(delta, 4) if delta else ""

            r = gates.gate_strike(snap.spot, c.strike, c.bid or 0, delta,
                                  iv, c.dte, cfg)
            if not r.passed:
                log.add(**base, failed_gate=r.gate, reason=r.reason)
                continue
            strike_vals = r.value

            r = gates.gate_contract_quality(
                c.strike, c.bid, c.ask, c.dte, c.open_interest, c.volume,
                snap.avg_volume_30d, tier, cfg)
            if not r.passed:
                log.add(**base, failed_gate=r.gate, reason=r.reason)
                continue
            quality = r.value

            r = gates.gate_return(c.bid, c.strike, c.dte, cfg)
            if not r.passed:
                log.add(**base, failed_gate=r.gate, reason=r.reason,
                        ann_roc=round(r.value, 4))
                continue
            ann_roc = r.value

            r = gates.gate_portfolio(ticker, sector, c.strike, c.bid,
                                     open_positions, cfg)
            if not r.passed:
                log.add(**base, failed_gate=r.gate, reason=r.reason,
                        ann_roc=round(ann_roc, 4))
                continue
            collateral = r.value

            s = score_contract(strike_vals["buffer_ratio"], ann_roc,
                               quality["spread_pct"], quality["open_interest"],
                               c.volume or 0, cfg)

            cand = {
                "ticker": ticker, "sector": sector, "strike": c.strike,
                "expiry": c.expiry.isoformat(), "dte": c.dte, "delta": delta,
                "bid": c.bid, "collateral": collateral, "ann_roc": ann_roc,
                "breakeven": strike_vals["breakeven"],
                "breakeven_pct": (strike_vals["breakeven"] - snap.spot) / snap.spot,
                "buffer_ratio": strike_vals["buffer_ratio"],
                "score": s["score"],
                "exit": build_exit_plan(c.strike, c.bid, c.expiry, cfg),
            }
            candidates.append(cand)
            log.add(**base, failed_gate="", reason="PASSED",
                    ann_roc=round(ann_roc, 4), score=s["score"])
            print(f"  PASS {c.strike:g}P {c.expiry} "
                  f"roc={ann_roc:.1%} score={s['score']}")

        if atm_iv:
            append_iv_snapshot(ticker, atm_iv[1])

    written = log.flush()
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top = candidates[: cfg.top_n_alerts]

    print(f"\nEvaluated {evaluated} contracts, {len(candidates)} passed, "
          f"{written} rows logged")

    if not args.dry_run and cfg.alerts_enabled and top:
        for c in top:
            telegram.send(telegram.format_candidate(c, fx))
        print(f"Sent {len(top)} alerts")
    elif top:
        print("Alerts disabled. Top candidates:")
        for c in top:
            print(f"  {c['ticker']} {c['strike']:g}P {c['expiry']} "
                  f"score {c['score']}")

    if not args.dry_run and cfg.liveness_ping and is_liveness_day:
        telegram.send(telegram.format_digest(
            len(universe), evaluated, len(candidates), cfg.alerts_enabled,
            gate_breakdown(), top, fx))
        print("Weekly digest sent")

    return 0


if __name__ == "__main__":
    sys.exit(main())
