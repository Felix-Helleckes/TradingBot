#!/bin/bash
set -euo pipefail

# Autosim main loop with safety: lock, trap cleanup, rsync tuning, mount retries
BASE="/home/felix/TradingBot"
PY="$BASE/venv/bin/python3"
OUT_DIR="$BASE/reports/autosim"
NAS_OUT_DIR="/home/felix/mnt_nas_v2/Volume/kraken_research_data/autosim"
LOG="$OUT_DIR/autosim_loop.log"
LOCKFILE="/tmp/autosim_main.lock"

mkdir -p "$OUT_DIR"

# Stale-lock detection: if lockfile is older than 7 hours, the previous run
# either crashed or was OOM-killed while holding the lock. Remove it so this
# run is not silently blocked forever.
STALE_LOCK_AGE_SECONDS=25200  # 7 hours
if [ -f "$LOCKFILE" ]; then
  lock_mtime=$(stat -c %Y "$LOCKFILE" 2>/dev/null || stat -f %m "$LOCKFILE" 2>/dev/null || echo 0)
  now_ts=$(date +%s)
  lock_age=$((now_ts - lock_mtime))
  if [ "$lock_age" -gt "$STALE_LOCK_AGE_SECONDS" ]; then
    echo "[$(date -Iseconds)] WARNING: stale lockfile ($lock_age s old) removed" | tee -a "$LOG"
    rm -f "$LOCKFILE"
  fi
fi

# Prevent parallel runs
exec 200>"$LOCKFILE" || { echo "Cannot open lockfile $LOCKFILE"; exit 1; }
flock -n 200 || { echo "Autosim already running; exiting"; exit 0; }

# Create cache dir early (so trap can clean it)
CACHE_DIR=$(mktemp -d /tmp/sim_ohlc_XXXX)

cleanup(){ rc=$?; if [ -n "$CACHE_DIR" ] && [ -d "$CACHE_DIR" ]; then rm -rf "$CACHE_DIR" || true; fi; exit $rc; }
trap cleanup EXIT INT TERM

log(){ echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

# rotate log if >5MB
if [ -f "$LOG" ]; then
  sz=$(du -b "$LOG" | cut -f1)
  if [ "$sz" -gt $((5*1024*1024)) ]; then
    mv "$LOG" "$LOG.$(date +%Y%m%d%H%M%S)" || true
    gzip -9 "$LOG.$(date +%Y%m%d%H%M%S)" || true
  fi
fi

log(){ echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

run_backtest(){
  local days="$1" out_json="$2"
  log "Running backtest for $days days..."
  # create fresh cache for this run
  CACHE_DIR=$(mktemp -d /tmp/sim_ohlc_$(date +%s)_XXXX)
  mkdir -p "$CACHE_DIR"

  # rsync with retries and limited bandwidth (avoid saturating network)
  RSYNC_SRC="/home/felix/mnt_nas_v2/Volume/kraken_research_data/"
  RSYNC_OPTS=( -az --partial --bwlimit=2000 --include='*/' --include='*.csv' --exclude='*' --timeout=60 )
  rr=0
  while [ $rr -lt 3 ]; do
    log "rsync attempt $((rr+1)) from NAS to $CACHE_DIR"
    rsync "${RSYNC_OPTS[@]}" "$RSYNC_SRC" "$CACHE_DIR" && break
    rr=$((rr+1))
    sleep $((rr * 5))
  done
  if [ $rr -ge 3 ]; then
    log "rsync failed after 3 attempts, attempting local copy fallback"
    cp -r "$RSYNC_SRC"* "$CACHE_DIR" || true
  fi

  # Use timeout + nice + ionice to limit resource impact. Kill if >6h.
  if command -v ionice >/dev/null 2>&1; then
    IONICE_CMD=(ionice -c2 -n7)
  else
    IONICE_CMD=()
  fi
  timeout --kill-after=1m 6h nice -n 10 "${IONICE_CMD[@]}" "$PY" "$BASE/scripts/backtest_v3_detailed.py" --days "$days" --out "$out_json" >>"$LOG" 2>&1 || log "backtest command failed or timed out"

  # cleanup cache (leftover will be removed by trap)
  if [ -d "$CACHE_DIR" ]; then
    # copy result to NAS with retry
    cp_attempts=0
    while [ $cp_attempts -lt 3 ]; do
      cp "$out_json" "$NAS_OUT_DIR/backtest_$(date +%Y%m%d_%H%M%S).json" && break
      cp_attempts=$((cp_attempts+1))
      sleep $((cp_attempts*3))
    done
    rm -rf "$CACHE_DIR" || true
  fi
}

# Check Raspberry Pi CPU temperature. Abort backtest if the Pi is throttling
# (>65 °C) to avoid a 6-hour backtest run that takes 12+ hours and overlaps the
# next scheduled cycle.
check_thermal(){
  local TEMP_FILE="/sys/class/thermal/thermal_zone0/temp"
  if [ ! -f "$TEMP_FILE" ]; then
    return 0  # Not a Pi or temp not readable; continue
  fi
  local temp_raw
  temp_raw=$(cat "$TEMP_FILE" 2>/dev/null || echo 0)
  local temp_c=$(( temp_raw / 1000 ))
  log "Pi CPU temperature: ${temp_c}°C"
  if [ "$temp_c" -ge 65 ]; then
    log "THERMAL GUARD: CPU is ${temp_c}°C (>= 65°C). Skipping backtest cycle to prevent throttled run."
    return 1
  fi
  return 0
}

main(){
  log "Autosim Cycle START"

  # Abort early if Pi is thermally throttled
  if ! check_thermal; then
    exit 0
  fi
  
  # Ensure NAS is mounted (retry on failure)
  if ! mountpoint -q "/home/felix/mnt_nas_v2"; then
    log "NAS not mounted. Attempting mount (up to 3 attempts)..."
    mtry=0
    while [ $mtry -lt 3 ]; do
      sudo mount -t cifs //192.168.178.1/fritz.nas /home/felix/mnt_nas_v2 -o credentials=/root/.smb/fritz_nas_creds,vers=3.0,iocharset=utf8,uid=1000,gid=1000,noserverino && break || true
      mtry=$((mtry+1))
      sleep $((mtry * 3))
    done
    if ! mountpoint -q "/home/felix/mnt_nas_v2"; then
      log "Warning: NAS mount failed after retries. Will continue, rsync will attempt fallback copy."
    else
      log "NAS mount succeeded"
    fi
  fi

  # Run backtest for 30 days
  run_backtest 30 "$OUT_DIR/latest_backtest.json"
  
  # Copy result to NAS for history
  cp "$OUT_DIR/latest_backtest.json" "$NAS_OUT_DIR/backtest_$(date +%Y%m%d_%H%M%S).json" || true
  
  log "Autosim Cycle DONE"
}

main
