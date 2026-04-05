#!/usr/bin/env bash
# Incremental Kraken Data Collector - Saves to NAS
set -euo pipefail

BASE="/home/felix/tradingbot"
# Mount point aligned with main system mount
NAS_MOUNT="/mnt/fritz_nas"
OUT_DIR="$NAS_MOUNT/Volume/kraken/2026/ohlc"
PY="$BASE/venv/bin/python3"
SCRIPT="$BASE/scripts/collect_kraken_history_incremental.py"

# Ensure mount is active
if ! mountpoint -q "$NAS_MOUNT"; then
  echo "NAS not mounted. Attempting mount..."
  sudo mount -t cifs //192.168.178.1/fritz.nas "$NAS_MOUNT" -o credentials=/root/.smb/fritz_nas_creds,vers=3.0,iocharset=utf8,uid=1000,gid=1000
fi

export COLLECT_BASE_DIR="$OUT_DIR"

PAIRS=("XXBTZEUR" "XETHZEUR" "ADAEUR" "SOLEUR" "DOTEUR" "XXRPZEUR" "LINKEUR")
for pair in "${PAIRS[@]}"; do
  echo "Collecting $pair..."
  $PY "$SCRIPT" --pair "$pair" --resume || true
done

echo "Collection Cycle Done."
