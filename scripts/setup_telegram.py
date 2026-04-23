#!/usr/bin/env python3
"""One-time setup: find your Telegram chat ID so notifications work.

Usage:
    1. Open Telegram and send any message to your bot.
    2. Run:  python3 scripts/setup_telegram.py
    3. Add the printed TELEGRAM_CHAT_ID to /home/felix/tradingbot/.env
    4. Restart the bot: sudo systemctl restart kraken-bot
"""
import os
import sys

import requests

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
if not TOKEN:
    # Load .env manually if dotenv not installed
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        for line in open(env_file):
            if line.startswith("TELEGRAM_TOKEN="):
                TOKEN = line.split("=", 1)[1].strip()
                break

if not TOKEN:
    print("ERROR: TELEGRAM_TOKEN not set. Add it to .env first.")
    sys.exit(1)

resp = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", timeout=10)
data = resp.json()

if not data.get("ok") or not data.get("result"):
    print("No messages found. Send any message to your bot first, then run this again.")
    sys.exit(1)

seen = set()
for update in data["result"]:
    chat = update.get("message", {}).get("chat", {})
    cid = chat.get("id")
    name = chat.get("first_name", "") or chat.get("username", "") or str(cid)
    if cid and cid not in seen:
        seen.add(cid)
        print(f"Found chat: {name} (id={cid})")
        print(f"\nAdd this to your .env:\n  TELEGRAM_CHAT_ID={cid}\n")
