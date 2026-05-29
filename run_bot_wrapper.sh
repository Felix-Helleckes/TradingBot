#!/bin/bash
LOG_FILE="/home/felix/tradingbot/logs/bot_activity.log"
while true; do
    echo "$(date) - Starting bot" >> "$LOG_FILE"
    /home/felix/tradingbot/venv/bin/python /home/felix/tradingbot/main.py >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?
    echo "$(date) - Bot crashed with exit code $EXIT_CODE" >> "$LOG_FILE"
    sleep 5
done
