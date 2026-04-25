#!/bin/bash
# Simple notifier: if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID present, send a message
REASON="$1"
MSG="[TradingBot] Pause activated: $REASON"
LOG=/home/felix/TradingBot/logs/pause_notify.log
mkdir -p $(dirname "$LOG")
# Telegram disabled: do not perform outbound requests. Always log as NO-TELEGRAM.
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) NO-TELEGRAM (suppressed): $MSG" >> "$LOG"
