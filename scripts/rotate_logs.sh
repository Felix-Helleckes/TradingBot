#!/bin/bash
# Log rotation — keeps bot.log lean by clearing every 3 days
BOTDIR="/home/felix/tradingbot"
LOGFILE="$BOTDIR/bot.log"
MONLOG="$BOTDIR/logs/monitor.log"

# Rotate bot.log
if [ -f "$LOGFILE" ]; then
    cp "$LOGFILE" "${LOGFILE}.bak"
    > "$LOGFILE"
    echo "$(date '+%Y-%m-%d %H:%M:%S') [ROTATE] bot.log cleared (backup: bot.log.bak)" >> "$MONLOG"
fi

# Keep monitor.log itself small (keep last 500 lines)
if [ -f "$MONLOG" ]; then
    tail -500 "$MONLOG" > "${MONLOG}.tmp" && mv "${MONLOG}.tmp" "$MONLOG"
fi
