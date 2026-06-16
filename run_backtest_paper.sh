#!/bin/bash
# Run the backtester using the PAPER config (config.paper.toml)
LOCKFILE="/var/lock/tradingbot-backtest-paper.lock"
exec 200>"$LOCKFILE" || exit 1
flock -n 200 || exit 0

TIMESTAMP=$(date +%Y%m%d_%H%M)
OUT="/home/felix/tradingbot/reports/paper_canary/backtest_${TIMESTAMP}.txt"
JSON_OUT="/home/felix/tradingbot/reports/paper_canary/backtest_${TIMESTAMP}.json"
JSONL_FILE="/home/felix/tradingbot/reports/paper_canary/backtest_results.jsonl"

mkdir -p "$(dirname "$OUT")"
mkdir -p "$(dirname "$JSON_OUT")"

echo "Paper Backtest run at $(date -u +'%Y-%m-%dT%H:%M:%SZ')" > "$OUT"

/home/felix/tradingbot/venv/bin/python - <<PY >> "$OUT" 2>&1
import sys, toml, os
sys.path.insert(0, '/home/felix/tradingbot')
from trading_bot import Backtester
from kraken_interface import KrakenAPI
cfg = toml.load('/home/felix/tradingbot/config.paper.toml')
bt = Backtester(KrakenAPI('',''), cfg)
bt.run()
PY

chmod 644 "$OUT"

if [ -f "$JSON_OUT" ]; then
  echo "JSON exists"
fi

# Optional JSON extraction (re-use logic from run_backtest.sh)
if [ -f "$OUT" ]; then
  /home/felix/tradingbot/venv/bin/python - <<'PY' > "$JSON_OUT"
import os, re, json
path = '$OUT'
with open(path,'r') as f:
    txt = f.read()
d = {}
m = re.search(r"Total Return:\s*([0-9.+-]+)%", txt)
if m: d['total_return_pct'] = float(m.group(1))
m = re.search(r"Total Trades:\s*([0-9]+)", txt)
if m: d['total_trades'] = int(m.group(1))
from datetime import datetime
d['timestamp'] = datetime.utcnow().isoformat()+'Z'
print(json.dumps(d))
PY
  echo "$(cat $JSON_OUT)" >> "$JSONL_FILE"
  chmod 644 "$JSON_OUT"
  chmod 644 "$JSONL_FILE"
fi

exit 0
