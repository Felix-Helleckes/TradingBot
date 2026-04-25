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

"""Notifier shim (disabled).

This repository previously sent Telegram messages via `core.notifier.send()`.
Per project configuration this has been disabled — `send()` is now a
no-op that returns False and logs a debug message when invoked.

Keeping the module and function avoids touching callers throughout the
codebase while ensuring no external HTTP requests are made.
"""

import logging

logger = logging.getLogger(__name__)


def send(message: str) -> bool:
    """No-op notifier. Returns False to indicate no message was sent.

    Callers can continue to call `core.notifier.send(...)` safely; this
    implementation prevents outgoing Telegram requests.
    """
    logger.debug("Notifier disabled: suppressed Telegram message: %s", (message[:200] + '...') if len(message) > 200 else message)
    return False
