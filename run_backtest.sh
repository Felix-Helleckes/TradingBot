#!/bin/bash
# Run a backtest using the TradingBot Backtester. Safe-guarded with flock.
# Adds optional flags: --output-json (-j) to produce machine-readable JSON/JSONL
# and --dry-run (-n) kept for compatibility (no-op for offline backtests).
LOCKFILE="/var/lock/tradingbot-backtest.lock"
exec 200>"$LOCKFILE" || exit 1
flock -n 200 || exit 0

# CLI flags
OUTPUT_JSON=0
DRY_RUN=0
USAGE="Usage: $0 [--output-json|-j] [--dry-run|-n]"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-json|-j)
      OUTPUT_JSON=1; shift ;;
    --dry-run|-n)
      DRY_RUN=1; shift ;;
    -h|--help)
      echo "$USAGE"; exit 0 ;;
    *)
      echo "Unknown argument: $1"; echo "$USAGE"; exit 1 ;;
  esac
done

TIMESTAMP=$(date +%Y%m%d_%H%M)
OUT="/home/felix/tradingbot/reports/backtest_${TIMESTAMP}.txt"
JSON_OUT="/home/felix/tradingbot/reports/backtest_${TIMESTAMP}.json"
JSONL_FILE="/home/felix/tradingbot/reports/backtest_results.jsonl"

mkdir -p "$(dirname "$OUT")"
mkdir -p "$(dirname "$JSON_OUT")"

echo "Backtest run at $(date -u +'%Y-%m-%dT%H:%M:%SZ')" > "$OUT"

# Export a compatibility env var; Backtester may ignore it (no side-effects expected)
export BACKTEST_DRY_RUN="$DRY_RUN"

# Run Backtester inside the project's venv
/home/felix/tradingbot/venv/bin/python - <<'PY' >> "$OUT" 2>&1
import sys, toml, os
sys.path.insert(0, '/home/felix/tradingbot')
from trading_bot import Backtester
from kraken_interface import KrakenAPI
cfg = toml.load('/home/felix/tradingbot/config.toml')
# Note: KrakenAPI instantiated with empty keys for offline backtests that use cached OHLC
bt = Backtester(KrakenAPI('',''), cfg)
bt.run()
PY

chmod 644 "$OUT"

# Optional: produce machine-readable JSON + append to a cumulative JSONL for automation
if [ "$OUTPUT_JSON" -eq 1 ]; then
  export REPORT_TXT="$OUT"
  /home/felix/tradingbot/venv/bin/python - <<'PY' > "$JSON_OUT"
import os, re, json
path = os.environ.get('REPORT_TXT')
if not path:
    raise SystemExit(1)
with open(path, 'r') as f:
    txt = f.read()
d = {}
m = re.search(r"Total Return:\s*([0-9.+-]+)%", txt)
if m: d['total_return_pct'] = float(m.group(1))
m = re.search(r"Sharpe Ratio:\s*([0-9.+-]+)", txt)
if m: d['sharpe'] = float(m.group(1))
m = re.search(r"Sortino Ratio:\s*([0-9.+-]+)", txt)
if m: d['sortino'] = float(m.group(1))
m = re.search(r"Max Drawdown:\s*([0-9.+-]+)%", txt)
if m: d['max_drawdown_pct'] = float(m.group(1))
m = re.search(r"Total Trades:\s*([0-9]+)", txt)
if m: d['total_trades'] = int(m.group(1))
m = re.search(r"Win Rate:\s*([0-9.+-]+)%", txt)
if m: d['win_rate_pct'] = float(m.group(1))
# Metadata
from datetime import datetime
d['timestamp'] = datetime.utcnow().isoformat() + 'Z'
d['report_txt'] = path
print(json.dumps(d))
PY
  # Ensure JSONL file exists and append
  mkdir -p "$(dirname "$JSONL_FILE")"
  echo "$(cat "$JSON_OUT")" >> "$JSONL_FILE"
  chmod 644 "$JSON_OUT"
  chmod 644 "$JSONL_FILE"
fi

# release lock automatically on exit
exit 0
