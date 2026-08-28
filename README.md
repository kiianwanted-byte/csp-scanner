# CSP Scanner

Daily cash-secured put screener. Checks an approved ticker universe against a
fixed gate checklist and reports passing candidates to Telegram.

Not a stock picker. Not an idea generator. A discipline tool that stops you
entering trades your own rules would reject.

## Setup

1. `cp .env.example .env` and fill in your Telegram token and chat ID
2. Set `account.capital` in `config.yaml`
3. Approve tickers in `data/tickers.csv` by setting `approved` to TRUE
4. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to GitHub repo secrets
5. `pip install -r requirements.txt`
6. `python main.py` to test locally

## Status

Log-only mode. `alerts_enabled: false` in config.yaml.
Flip to true only after reviewing 8 weeks of scan_log.csv.

## Not financial advice

This tool applies filters. It does not evaluate businesses, predict prices,
or know anything about market conditions. Every trade is your decision.
