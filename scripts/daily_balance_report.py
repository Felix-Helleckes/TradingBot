#!/usr/bin/env python3
"""Daily Telegram balance report — sends the total Kraken account value (EUR) at 09:00.

Total = ZEUR (spot cash) + EUR value of all held crypto (via live Ticker prices)
        + unrealized P&L from any open margin positions (TradeBalance.n)

This matches exactly what the YouTube stream overlay shows.
Also shows cumulative P&L since the bot was first started (from data/pnl_state.json).

Cron entry (runs every day at 09:00):
  0 9 * * * /home/felix/tradingbot/venv/bin/python /home/felix/tradingbot/scripts/daily_balance_report.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env
_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

import krakenex
import requests

# Kraken asset → EUR Ticker pair
_ASSET_PAIRS = {
    "XXBT": "XXBTZEUR",
    "XETH": "XETHZEUR",
    "SOL": "SOLEUR",
    "ADA": "ADAEUR",
    "DOT": "DOTEUR",
    "XXRP": "XXRPZEUR",
    "LINK": "LINKEUR",
}


def get_total_balance(api: krakenex.API) -> tuple[float, dict[str, float], float, float]:
    """Return (total_eur, breakdown, spot_eur, unrealized_pnl).

    Calculates total exactly as the stream overlay does:
      ZEUR + sum(crypto_qty * live_price) + unrealized margin P&L
    """
    bal_resp = api.query_private("Balance")
    balance = bal_resp.get("result", {})
    spot_eur = float(balance.get("ZEUR", 0.0))

    # Unrealized P&L from open margin positions
    unrealized = 0.0
    try:
        time.sleep(0.3)
        tb = api.query_private("TradeBalance")
        unrealized = float(tb.get("result", {}).get("n", 0.0))
    except Exception:
        pass

    # Get current prices for non-zero crypto holdings
    breakdown: dict[str, float] = {"EUR": spot_eur}
    total = spot_eur
    for asset, pair in _ASSET_PAIRS.items():
        qty = float(balance.get(asset, 0.0))
        if qty <= 0:
            continue
        try:
            time.sleep(0.2)
            ticker = api.query_public("Ticker", {"pair": pair})
            price = float(list(ticker.get("result", {}).values())[0]["c"][0])
            value = qty * price
            breakdown[asset] = value
            total += value
        except Exception:
            pass

    total += unrealized
    if unrealized != 0:
        breakdown["margin_pnl"] = unrealized

    return total, breakdown, spot_eur, unrealized


def load_pnl_state() -> dict:
    path = Path(__file__).resolve().parent.parent / "data" / "pnl_state.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def send_telegram(token: str, chat_id: str, message: str) -> bool:
    # Notifications disabled: do not make outbound HTTP requests to Telegram.
    # Keep function present so scripts can call it safely; return False to indicate no message sent.
    print("Telegram notifications are disabled (send_telegram suppressed)")
    return False


def main() -> None:
    token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set — skipping")
        return

    api_key = os.getenv("KRAKEN_API_KEY", "")
    api_secret = os.getenv("KRAKEN_API_SECRET", "")
    api = krakenex.API(api_key, api_secret)

    total, breakdown, spot_eur, unrealized = get_total_balance(api)

    pnl_state = load_pnl_state()
    start_eur = pnl_state.get("start_eur", total)
    start_date = pnl_state.get("created_at", "?")[:10]
    cumulative_pnl = total - start_eur
    pnl_sign = "🟢" if cumulative_pnl >= 0 else "🔴"
    pnl_pct = (cumulative_pnl / start_eur * 100.0) if start_eur > 0 else 0.0

    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    # Build breakdown lines for non-trivial holdings
    detail_lines = [f"  💶 Cash (EUR):  {spot_eur:.2f} EUR"]
    for asset, val in breakdown.items():
        if asset == "EUR" or val < 0.01:
            continue
        if asset == "margin_pnl":
            detail_lines.append(f"  📈 Margin P&amp;L: {val:+.2f} EUR")
        else:
            label = asset.replace("XXBT", "BTC").replace("XETH", "ETH").replace("XXRP", "XRP")
            detail_lines.append(f"  🪙 {label}:  {val:.2f} EUR")

    message = (
        f"📊 <b>Täglicher Kontostand</b>\n"
        f"🕘 {now}\n\n"
        f"💰 <b>Gesamt: {total:.2f} EUR</b>\n" + "\n".join(detail_lines) + "\n\n"
        f"{pnl_sign} <b>Gesamt-P&amp;L:</b> {cumulative_pnl:+.2f} EUR ({pnl_pct:+.2f}%)\n"
        f"  Startkapital: {start_eur:.2f} EUR (seit {start_date})"
    )

    ok = send_telegram(token, chat_id, message)
    print("Sent!" if ok else "Failed.")


if __name__ == "__main__":
    main()
