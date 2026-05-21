#!/bin/bash
# Run paper backtest hourly for DURATION_HOURS (default 24)
BASE=/home/felix/tradingbot
DURATION_HOURS=${1:-24}
OUTDIR=$BASE/reports/paper_canary
mkdir -p "$OUTDIR"

for i in $(seq 1 $DURATION_HOURS); do
  echo "Paper canary run $i / $DURATION_HOURS at $(date -u)"
  $BASE/run_backtest_paper.sh --output-json || true
  # move last outputs if any (the script writes with timestamp)
  sleep 3600
done

# mark completion
touch $OUTDIR/COMPLETED_$(date -u +%Y%m%d_%H%M%S)
