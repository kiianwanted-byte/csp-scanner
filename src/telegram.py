"""Telegram delivery. Plain text, monospace box, minimal emoji."""
from __future__ import annotations

import os
import requests

API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramError(Exception):
    pass


def _creds() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        raise TelegramError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
    return token, chat


def send(text: str) -> bool:
    token, chat = _creds()
    try:
        r = requests.post(
            API.format(token=token),
            json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        if r.status_code != 200:
            print(f"  [telegram] HTTP {r.status_code}: {r.text[:200]}")
            return False
        return True
    except requests.RequestException as e:
        print(f"  [telegram] request failed: {e}")
        return False


def format_candidate(c: dict) -> str:
    e = c["exit"]
    return (
        "CSP Candidate\n"
        "<pre>"
        f"TICKER      {c['ticker']}\n"
        f"STRIKE      {c['strike']:g} PUT\n"
        f"EXPIRY      {c['expiry']} ({c['dte']} DTE)\n"
        f"DELTA       {c['delta']:.2f}\n"
        f"BID         {c['bid']:.2f}\n"
        f"COLLATERAL  ${c['collateral']:,.0f}\n"
        f"ANN. ROC    {c['ann_roc']:.1%}\n"
        f"BREAKEVEN   {c['breakeven']:.2f} ({c['breakeven_pct']:+.1%})\n"
        f"BUFFER      {c['buffer_ratio']:.2f}x expected move\n"
        f"SCORE       {c['score']:.1f} / 10\n"
        "\n"
        "EXIT\n"
        f"TARGET      buy back at {e['profit_target_price']:.2f}\n"
        f"TIME STOP   {e['time_stop_date']} ({e['time_stop_dte']} DTE)\n"
        f"COST BASIS  {e['cost_basis_if_assigned']:.2f} if assigned\n"
        "</pre>"
    )


def format_liveness(scanned: int, evaluated: int, passed: int,
                    alerts_on: bool) -> str:
    mode = "ALERTS ON" if alerts_on else "LOG ONLY"
    return (
        "CSP Scanner weekly check\n"
        "<pre>"
        f"MODE        {mode}\n"
        f"TICKERS     {scanned}\n"
        f"CONTRACTS   {evaluated}\n"
        f"PASSED      {passed}\n"
        "</pre>"
        "Bot is alive."
    )
