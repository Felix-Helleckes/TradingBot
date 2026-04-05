NAS Data Structure — Unified Layout
=====================================

All Kraken trading data lives under a single root folder on the NAS:

  /mnt/fritz_nas/Volume/kraken/
  ├── 2025/
  │   ├── ohlcvt/       — Q4 2025 OHLCVT master archive (raw CSVs, no header)
  │   │                   Files: {PAIR}_{INTERVAL_MIN}.csv  e.g. ETHEUR_60.csv
  │   └── time_sales/   — 2025 Time & Sales data
  ├── 2026/
  │   ├── ohlc/         — Per-pair OHLC CSVs (authoritative source)
  │   │   ├── XETHZEUR/
  │   │   │   ├── ohlc_1m.csv
  │   │   │   ├── ohlc_5m.csv
  │   │   │   ├── ohlc_15m.csv
  │   │   │   └── ohlc_60m.csv
  │   │   └── ... (XXBTZEUR, SOLEUR, ADAEUR, DOTEUR, XXRPZEUR, LINKEUR)
  │   ├── autosim/      — Autosim backtest results (JSON)
  │   ├── _state/       — Collector state (collector_state.json)
  │   └── trade_history/ — trades_2026.json
  └── bot_cache/        — Bot cache files (cross-year)
      ├── daytrading_15m/
      ├── mentor_cache_1h/
      └── ohlc_cache/

CSV format: ts,open,high,low,close[,vwap,volume,count]
Timestamp in epoch seconds (UTC).

Adding a new year
-----------------
Create a new year sub-folder following the same pattern:
  mkdir -p /mnt/fritz_nas/Volume/kraken/2027/ohlc
  mkdir -p /mnt/fritz_nas/Volume/kraken/2027/trade_history
  mkdir -p /mnt/fritz_nas/Volume/kraken/2027/_state
No code changes required — scripts use the year dynamically.

Migration
---------
Run  scripts/migrate_nas_structure.sh  to copy data from the old 3-folder
layout (kraken_data, kraken_daten, kraken_research_data) into this structure.
The old folders are not deleted automatically — verify first, then remove them.
