"""Telegram notification helper for the trading bot.

Sends a message to a Telegram chat whenever a trade is executed.
Requires TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in the environment
(set in /home/felix/tradingbot/.env).

If either variable is missing the notifier silently skips — the bot
keeps running without notifications.

Setup:
    1. Start your Telegram bot and send it /start (or any message).
    2. Run:  python3 scripts/setup_telegram.py
    3. Copy the printed chat ID into .env as TELEGRAM_CHAT_ID=<id>
    4. Restart the bot service.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_BASE_URL = f"https://api.telegram.org/bot{_TOKEN}"


def send(message: str) -> bool:
    """Send *message* to the configured Telegram chat.

    Returns True on success, False on failure (e.g. no credentials configured).
    Never raises — all errors are logged and swallowed so the bot keeps running.
    """
    if not _TOKEN or not _CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"{_BASE_URL}/sendMessage",
            json={"chat_id": _CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=5,
        )
        if not resp.ok:
            logger.warning("Telegram notification failed: %s", resp.text)
            return False
        return True
    except Exception as exc:
        logger.warning("Telegram notification error: %s", exc)
        return False
