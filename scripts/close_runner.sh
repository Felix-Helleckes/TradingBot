#!/bin/bash
cd /home/felix/tradingbot
LOG=logs/close_runner.log
echo "Starting close runner at $(date)" >> "$LOG"
while true; do
  date >> "$LOG"
  /home/felix/tradingbot/venv/bin/python close_on_profit.py >> "$LOG" 2>&1
  sleep 300
done
