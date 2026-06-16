#!/bin/bash
LOG_FILE="/home/felix/tradingbot/reports/trade_journal.csv"
STATE_FILE="/home/felix/tradingbot/reports/.monitor_pnl_state"
THRESHOLD=-2.0  # alert if cumulative loss > 2 EUR
# Initialize state file if not exists
if [[ ! -f "$STATE_FILE" ]]; then
    echo "0" > "$STATE_FILE"
fi
LAST_LINE=$(cat "$STATE_FILE")
TOTAL_LINES=$(wc -l < "$LOG_FILE")
if [[ "$LAST_LINE" -ge "$TOTAL_LINES" ]]; then
    exit 0
fi
# Process new lines
NEW_PROFIT=0
while IFS= read -r line; do
    # Extract profit field (6th)
    profit=$(echo "$line" | cut -d',' -f6)
    NEW_PROFIT=$(echo "$NEW_PROFIT + $profit" | bc -l)
done < <(tail -n +$((LAST_LINE+1)) "$LOG_FILE")
# Update state
echo "$TOTAL_LINES" > "$STATE_FILE"
# Check threshold
if (( $(echo "$NEW_PROFIT < $THRESHOLD" | bc -l) )); then
    # Get last few trades for context
    LAST_TRADES=$(tail -n 5 "$LOG_FILE")
    MESSAGE="⚠️ Tradingbot PnL Alert: Neue Verluste seit letztem Check: ${NEW_PROFIT} EUR (Schwelle: $THRESHOLD EUR).\nLetzte 5 Trades:\n$LAST_TRADES"
    send_message action=send target=telegram message="$MESSAGE"
fi
