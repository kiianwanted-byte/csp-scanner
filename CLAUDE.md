# CSP Scanner

A cash-secured put screener. Scans an approved ticker universe once daily,
applies a fixed gate checklist, and sends passing candidates to Telegram.

This is a **rules compliance tool**, not an idea generator. It does not discover
new tickers. It checks whether contracts on an already-approved list meet
pre-agreed criteria.

## Non-negotiables

1. **Log-only mode is the default.** `alerts_enabled: false` in config. Do not
   flip it. The owner flips it after reviewing 8 weeks of logged output.
2. **Never suggest a trade on a ticker not in `data/tickers.csv` with
   `approved=TRUE`.** No exceptions, no dynamic universe expansion.
3. **Fail closed.** If any data field needed for a gate is missing or null,
   the contract fails that gate. Never assume, never interpolate.
4. **No scope creep.** No backtesting engine, no web dashboard, no database,
   no config UI, no broker integration, no auto-execution. If it feels like
   a good idea to add, it is out of scope. Ask first.

## Stack

- Python 3.11
- `yfinance` for chains, quotes, earnings dates
- GitHub Actions daily cron
- Telegram Bot API for delivery
- CSV files in `data/` for state and logs. No database.

## Module structure

Keep these as separate files with clean boundaries.

| Module | Responsibility |
|---|---|
| `src/config.py` | Loads and validates `config.yaml`. Fails loudly on bad values. |
| `src/universe.py` | Reads `data/tickers.csv`, returns approved tickers only. |
| `src/data.py` | **Data provider interface.** All market data access goes through this. `YFinanceProvider` implements it. Swapping to Tradier later must be a one-file change. |
| `src/gates.py` | Pure functions, one per gate. Each returns `(passed: bool, reason: str, value: Any)`. No side effects, no I/O. |
| `src/scoring.py` | Scores contracts that pass all gates. |
| `src/exits.py` | Computes the exit plan for a candidate. |
| `src/telegram.py` | Message formatting and sending. |
| `src/logging_csv.py` | Appends every evaluation to `data/scan_log.csv`. |
| `main.py` | Orchestration only. Wires the modules, no business logic. |

## The gates

Run in this order. Short-circuit on first failure. Log the failing gate name
and reason for every contract, including failures.

**Gate 1 — Universe**
Ticker must be in `data/tickers.csv` with `approved=TRUE`. Handled by
`universe.py` before anything else runs.

**Gate 2 — Events**
- No earnings date between today and expiry. Fail if earnings date is
  unavailable (fail closed).
- Flag (do not fail) if an ex-dividend date falls before expiry.

**Gate 3 — Volatility**
- Current IV must exceed 30-day realised volatility of the underlying.
- Compute realised vol from daily closes: stdev of log returns over 30
  trading days, annualised by sqrt(252).
- If `iv_rank` history exists in `data/iv_history.csv` with >= 250 rows for
  the ticker, additionally require IV rank >= 30. Otherwise skip that check.

**Gate 4 — Strike**
- Delta between `delta_min` and `delta_max` (absolute value).
- Breakeven (strike minus premium) must be at least one expected move below
  spot. Expected move = spot * IV * sqrt(DTE / 365).

**Gate 5 — Contract quality**
- DTE between `dte_min` and `dte_max`.
- Bid >= tier `min_bid`.
- Premium >= tier `min_premium_pct_of_strike` * strike.
- Spread test passes if EITHER `(ask - bid) / mid <= max_spread_pct_of_mid`
  OR `(ask - bid) <= max_spread_abs`. Whichever is more permissive.
- Open interest >= tier `min_open_interest`.
- Contract volume >= tier `min_contract_volume`.
- Underlying 30-day average volume >= tier `min_underlying_avg_volume`.

**Gate 6 — Return**
- ROC = bid / (strike - bid)
- Annualised ROC = ROC * (365 / DTE)
- Must be >= `annualised_roc_min`.
- Use the **bid**, never the mid. Assume the worse fill.

**Gate 7 — Portfolio fit**
- Collateral required = (strike - bid) * 100.
- Must be <= `max_pct_capital_per_name` * `account_capital`.
- Read `data/open_positions.csv`. Reject if the ticker already has an open
  position, or if adding it would breach `max_pct_capital_per_sector`.

**Gate 8 — Exit plan**
Not a filter. Always computed for passing candidates:
- Profit target: 50% of premium received. Give the buy-back price.
- Time stop: the calendar date at 21 DTE.
- Roll rule: down and out for a net credit only, maximum 2 rolls.
- Assignment plan: cost basis = strike - premium.

## Tiers

Tier is chosen by strike price, not underlying price. Tiers are enabled via
`enabled_tiers` in config. Cheaper stocks have structurally wider relative
spreads, so gates loosen going down.

## Scoring

Only for contracts passing all gates. 0 to 10.
- Protection, 40%: (spot - breakeven) / expected_move. Ratio >= 1.5 scores full.
- Payoff, 35%: annualised ROC scaled between the minimum and 40%.
- Liquidity, 25%: spread tightness, open interest, volume.

Rank descending. Send the top 5 only.

## Output format

Plain text Telegram message. One block per candidate. Match the owner's
existing bot conventions: plain text title, monospace box, explicit labels,
minimal emoji.

```
CSP Candidate

TICKER      AAPL
STRIKE      215 PUT
EXPIRY      2026-10-16 (38 DTE)
DELTA       0.24
BID         3.40
COLLATERAL  $21,160
ANN. ROC    15.4%
BREAKEVEN   211.60 (-4.8%)
BUFFER      1.6x expected move
SCORE       7.8 / 10

EXIT
TARGET      buy back at 1.70
TIME STOP   2026-09-25 (21 DTE)
COST BASIS  211.60 if assigned
```

## Operational requirements

- **Market calendar check.** Exit silently on US market holidays and weekends.
- **Weekly liveness ping.** Every Monday, send a message stating the bot ran,
  how many tickers were scanned, and how many passed. A silent bot and a
  broken bot look identical.
- **Full logging.** Every contract evaluated goes to `data/scan_log.csv`
  with timestamp, ticker, strike, expiry, and either the failing gate or the
  score. This becomes the performance record.
- **Commit logs back to the repo** at the end of each run.

## Secrets

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` come from environment variables.
Never hardcode. Never commit `.env`.

## Build order

Do these in sequence and verify each before moving on.

1. Config loading and validation
2. Universe loading from CSV
3. Data provider interface plus yfinance implementation, tested on ONE ticker
4. Gates as pure functions, with unit tests using fixture data
5. Scoring and exit plan
6. Telegram formatting, tested by sending one message
7. Full universe orchestration in `main.py`
8. GitHub Actions workflow

Write tests for `gates.py`. A silent bug there costs real money.

## Known design note: delta and buffer overlap

Gate 4 checks two things that measure the same underlying quantity from
different angles. Delta and distance-from-spot are not independent.

Measured at spot 100, IV 40%, 38 DTE:

| Delta | Buffer before premium |
|---|---|
| 0.15 | 0.88x expected move |
| 0.20 | 0.71x |
| 0.25 | 0.56x |
| 0.30 | 0.42x |

So `min_buffer_ratio` above roughly 0.90 rejects the entire 0.15-0.30 delta
band. Default is 0.85. Raising it is equivalent to tightening delta_max, not
an independent safety check. Do not treat them as two separate protections.
