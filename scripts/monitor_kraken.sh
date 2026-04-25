#!/bin/bash
LOGDIR="/home/felix/tradingbot/logs"
mkdir -p "$LOGDIR"
MONLOG="$LOGDIR/kraken_monitor.log"
ALERTLOG="$LOGDIR/kraken_monitor_alerts.log"
# Stream journal entries from now on and write to logs; capture WARN/ERROR/Exception/Too many requests
journalctl -u kraken-bot.service -f -o short-iso | while IFS= read -r line; do
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $line" >> "$MONLOG"
  if echo "$line" | grep -Ei "ERROR|WARN|WARNING|Traceback|Exception|Too many requests|Max Drawdown" >/dev/null; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ALERT: $line" >> "$ALERTLOG"
  fi
done
