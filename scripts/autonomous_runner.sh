#!/bin/bash
# Autonomous runner: hourly backtests + nightly sweep + hourly signal scan
BASE="/home/felix/tradingbot"
REPORTS="$BASE/reports"
VENV="$BASE/venv/bin/python"
LOG="$REPORTS/autonomous_runner.log"

mkdir -p "$REPORTS"

echo "Autonomous runner started at $(date -u +'%Y-%m-%dT%H:%M:%SZ')" >> "$LOG"

while true; do
  ts=$(date -u +"%Y%m%d_%H%M%S")
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Running hourly backtest" >> "$LOG"
  # run backtest with JSON output
  $BASE/run_backtest.sh --output-json >> "$LOG" 2>&1 || echo "backtest failed" >> "$LOG"

  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Running signal frequency scan" >> "$LOG"
  $VENV - <<'PY' >> "$LOG" 2>&1
import toml, json, os
from datetime import datetime, timedelta
import sys
sys.path.insert(0,'/home/felix/tradingbot')
from trading_bot import TechnicalAnalysis
from kraken_interface import KrakenAPI

BASE='/home/felix/tradingbot'
REPORTS=BASE+'/reports'
api = KrakenAPI('','')
TA = TechnicalAnalysis()
cfg = toml.load('/home/felix/tradingbot/config.toml')
pairs = cfg['bot_settings'].get('trade_pairs', ['XXBTZEUR'])
min_buy_score = cfg.get('risk_management', {}).get('min_buy_score', 18.0)
windows = [90]
results = {}
for days in windows:
    since = int((datetime.utcnow() - timedelta(days=days)).timestamp())
    results[days] = {}
    for pair in pairs:
        od = api.get_ohlc_data(pair, interval=15, since=since)
        if not od:
            results[days][pair] = {'error':'no data','candles':0}
            continue
        series = od[pair] if isinstance(od, dict) and pair in od else (list(od.values())[0] if isinstance(od, dict) else od)
        closes = [float(c[4]) for c in series]
        ta = TechnicalAnalysis()
        ta.seed_from_ohlc(pair, closes)
        buy = sell = buy_thresh = sell_thresh = total = 0
        for close in closes:
            signal, score = ta.generate_signal_with_score({pair: {'c':[close]}})
            if signal == 'BUY':
                buy += 1
                if abs(score) >= min_buy_score: buy_thresh += 1
            elif signal == 'SELL':
                sell += 1
                if abs(score) >= min_buy_score: sell_thresh += 1
            total += 1
        results[days][pair] = {'candles': total, 'buy_signals': buy, 'sell_signals': sell, 'buy_signals_minbuy': buy_thresh, 'sell_signals_minbuy': sell_thresh}

out_path = os.path.join(REPORTS, f'signal_frequency_scan_hourly_{ts}.json')
with open(out_path,'w') as f:
    json.dump({'timestamp': datetime.utcnow().isoformat()+'Z', 'min_buy_score': min_buy_score, 'results': results}, f, indent=2)
print('wrote', out_path)
PY

  # nightly sweep at 03:00 UTC
  hour=$(date -u +"%H")
  if [ "$hour" -eq "03" ]; then
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Running nightly quick sweep" >> "$LOG"
    if [ -f "$BASE/scripts/sweep_v3.py" ]; then
      $VENV $BASE/scripts/sweep_v3.py --quick --out "$REPORTS/sweep_quick_autorun_${ts}.json" >> "$LOG" 2>&1 || echo "sweep failed" >> "$LOG"
    fi
  fi

  # sleep until next hour (align to top of hour)
  sleep $((60*60 - $(date +%s) % 3600))
done
