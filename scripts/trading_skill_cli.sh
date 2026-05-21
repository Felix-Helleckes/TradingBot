#!/bin/bash
# Small wrapper to call trading_skill_adapter.py
PY=/home/felix/tradingbot/venv/bin/python
SCRIPT=/home/felix/tradingbot/trading_skill_adapter.py
if [ ! -f "$SCRIPT" ]; then
  echo "adapter not found"; exit 1
fi
$PY "$SCRIPT" "$@"
