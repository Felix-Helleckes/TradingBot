#!/usr/bin/env python3
"""Daily Telegram balance report — sends the total Kraken account value (EUR) at 09:00.

Reads live balance from Kraken API (same logic as the bot):
  ZEUR spot + unrealized P&L from TradeBalance.n

Also shows cumulative P&L since the bot was first started (from data/pnl_state.json).

Cron entry (runs every day at 09:00):
  0 9 * * * /home/felix/tradingbot/venv/bin/python /home/felix/tradingbot/scripts/daily_balance_report.py
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env
_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

import requests
from kraken_interface import KrakenAPI
from utils import load_config


def get_total_balance_eur(api: KrakenAPI) -> tuple[float, float, float]:
    """Return (spot_eur, unrealized_pnl, total_eur).

    Uses TradeBalance for margin-aware total so open positions are included.
    Falls back to spot-only if the margin call fails.
    """
    spot_eur = 0.0
    unrealized = 0.0

    try:
        bal = api.query_private("Balance")
        spot_eur = float((bal or {}).get("ZEUR", 0.0))
    except Exception:
        pass

    try:
        tb = api.query_private("TradeBalance")
        unrealized = float((tb or {}).get("n", 0.0))
    except Exception:
        pass

    return spot_eur, unrealized, spot_eur + unrealized


def load_pnl_state() -> dict:
    path = Path(__file__).resolve().parent.parent / "data" / "pnl_state.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def send_telegram(token: str, chat_id: str, message: str) -> bool:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.ok
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def main() -> None:
    token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set — skipping")
        return

    config = load_config(Path(__file__).resolve().parent.parent / "config.toml")
    api_key = os.getenv("KRAKEN_API_KEY", config.get("kraken", {}).get("api_key", ""))
    api_secret = os.getenv("KRAKEN_API_SECRET", config.get("kraken", {}).get("api_secret", ""))
    api = KrakenAPI(api_key, api_secret)

    spot, unrealized, total = get_total_balance_eur(api)
    pnl_state = load_pnl_state()
    start_eur = pnl_state.get("start_eur", total)
    start_date = pnl_state.get("created_at", "?")[:10]
    cumulative_pnl = total - start_eur
    pnl_sign = "🟢" if cumulative_pnl >= 0 else "🔴"
    pnl_pct = (cumulative_pnl / start_eur * 100.0) if start_eur > 0 else 0.0

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    message = (
        f"📊 <b>Täglicher Kontostand</b>\n"
        f"🕘 {now}\n\n"
        f"💶 <b>Gesamt: {total:.2f} EUR</b>\n"
        f"  Spot (ZEUR):  {spot:.2f} EUR\n"
        f"  Unrealized:   {unrealized:+.2f} EUR\n\n"
        f"{pnl_sign} <b>Gesamt-P&amp;L seit Start:</b> {cumulative_pnl:+.2f} EUR ({pnl_pct:+.2f}%)\n"
        f"  Startkapital: {start_eur:.2f} EUR (seit {start_date})"
    )

    ok = send_telegram(token, chat_id, message)
    print("Sent!" if ok else "Failed.")


if __name__ == "__main__":
    main()
