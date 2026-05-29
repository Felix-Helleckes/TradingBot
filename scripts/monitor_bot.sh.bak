#!/bin/bash
# Bot health monitor — restarts trading bot if it has crashed
BOTDIR="/home/felix/tradingbot"
LOGFILE="$BOTDIR/logs/monitor.log"
LOCKFILE="/tmp/kraken_bot.lock"

if ! pgrep -f "python main.py" > /dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [MONITOR] Bot not running — restarting..." >> "$LOGFILE"
    # Remove stale lock file left by crashed process
    rm -f "$LOCKFILE"
    cd "$BOTDIR"
    nohup python main.py >> "$BOTDIR/bot.log" 2>&1 &
    echo "$(date '+%Y-%m-%d %H:%M:%S') [MONITOR] Bot restarted (PID $!)" >> "$LOGFILE"
fi
