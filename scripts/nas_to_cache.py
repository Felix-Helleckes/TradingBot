#!/usr/bin/env python3
"""Convert NAS Q4 2025 Kraken OHLCVT CSV files into the mentor_cache_1h format.

The NAS archive (master_q4/) stores files as {PAIR}_{INTERVAL_MIN}.csv with
columns: ts,open,high,low,close,vwap,volume,count  (NO HEADER ROW — pure data)
The bot cache format is: {PAIR}_{first_ts}_{last_ts}_60m.json → dict {ts: close}

Pairs available in master_q4 (confirmed):
  ETHEUR_60.csv   → XETHZEUR
  LINKEUR_60.csv  → LINKEUR
  SOLEURC_60.csv  → SOLEUR
  XBTEURC_60.csv  → XXBTZEUR

Missing from Q4 archive: ADAEUR, DOTEUR, XXRPZEUR — these are NOT covered.

Also merges with kraken_research_data/ (Jan–Feb 2026, all pairs) for maximum coverage.

Usage:
  python3 scripts/nas_to_cache.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]

Defaults to: 2025-10-01 → 2025-12-31 (Q4 2025 bull run period)
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

NAS_BASE_Q4 = Path("/Volumes/FRITZ.NAS/Volume/kraken_daten/2025/OHLCVT/master_q4")
NAS_BASE_RESEARCH = Path("/Volumes/FRITZ.NAS/Volume/kraken_research_data")
CACHE_OUT = Path("data/mentor_cache_1h")

# NAS filename → bot pair name
PAIR_MAP = {
    "ETHEUR": "XETHZEUR",
    "LINKEUR": "LINKEUR",
    "SOLEURC": "SOLEUR",
    "XBTEURC": "XXBTZEUR",
}

# Also pull from kraken_research_data (Jan–Feb 2026 format: pair/ohlc_60m.csv)
RESEARCH_PAIRS = [
    "XETHZEUR",
    "LINKEUR",
    "SOLEUR",
    "XXBTZEUR",
    "ADAEUR",
    "DOTEUR",
    "XXRPZEUR",
]


def parse_args():
    p = argparse.ArgumentParser(description="Convert NAS OHLCVT CSVs to bot cache format")
    p.add_argument("--start", default="2025-10-01", help="Start date YYYY-MM-DD (default: 2025-10-01)")
    p.add_argument("--end", default="2025-12-31", help="End date YYYY-MM-DD (default: 2025-12-31)")
    p.add_argument("--skip-q4", action="store_true", help="Skip Q4 master archive (only kraken_research_data)")
    p.add_argument("--skip-research", action="store_true", help="Skip kraken_research_data")
    return p.parse_args()


def to_ts(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def load_q4_csv(filepath: Path, start_ts: int, end_ts: int) -> dict:
    """Read master_q4 CSV (no header, columns: ts,open,high,low,close,vwap,vol,count)."""
    data = {}
    if not filepath.exists():
        print(f"  [WARN] Not found: {filepath}")
        return data
    try:
        with open(filepath, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 5:
                    continue
                try:
                    ts = int(row[0])
                    close = float(row[4])
                except (ValueError, IndexError):
                    continue
                if start_ts <= ts <= end_ts:
                    data[str(ts)] = close
    except OSError as e:
        print(f"  [ERR] Could not open {filepath}: {e}")
    return data


def load_research_csv(pair: str, start_ts: int, end_ts: int) -> dict:
    """Read kraken_research_data/{pair}/ohlc_60m.csv (format: ts,open,high,low,close,...)."""
    filepath = NAS_BASE_RESEARCH / pair / "ohlc_60m.csv"
    data = {}
    if not filepath.exists():
        print(f"  [WARN] Research file not found: {filepath}")
        return data
    try:
        with open(filepath, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 5:
                    continue
                try:
                    ts = int(row[0])
                    close = float(row[4])
                except (ValueError, IndexError):
                    continue
                if start_ts <= ts <= end_ts:
                    data[str(ts)] = close
    except OSError as e:
        print(f"  [ERR] Could not open {filepath}: {e}")
    return data


def save_cache(bot_pair: str, data: dict):
    if not data:
        print(f"  [SKIP] No data for {bot_pair}")
        return
    timestamps = sorted(int(k) for k in data.keys())
    first_ts = timestamps[0]
    last_ts = timestamps[-1]
    fname = f"{bot_pair}_{first_ts}_{last_ts}_60m.json"
    out_path = CACHE_OUT / fname
    CACHE_OUT.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    start_dt = datetime.utcfromtimestamp(first_ts).strftime("%Y-%m-%d %H:%M")
    end_dt = datetime.utcfromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M")
    print(f"  [OK] Saved {len(data):,} candles → {fname}")
    print(f"       Coverage: {start_dt} → {end_dt} UTC")


def main():
    args = parse_args()
    start_ts = to_ts(args.start)
    end_ts = to_ts(args.end) + 86399  # include full last day

    print(f"\nNAS → cache converter")
    print(f"Window: {args.start} → {args.end}")
    print(f"Range:  {start_ts} → {end_ts}")
    print(f"Output: {CACHE_OUT.resolve()}\n")

    if not NAS_BASE_Q4.exists() and not NAS_BASE_RESEARCH.exists():
        print("[ERR] NAS not mounted! Check /Volumes/FRITZ.NAS/")
        sys.exit(1)

    # ── Q4 master archive ──────────────────────────────────────────────────
    if not args.skip_q4 and NAS_BASE_Q4.exists():
        print("=== Q4 2025 master archive ===")
        for nas_name, bot_pair in PAIR_MAP.items():
            csv_path = NAS_BASE_Q4 / f"{nas_name}_60.csv"
            print(f"\n{nas_name} → {bot_pair}: {csv_path.name}")
            data = load_q4_csv(csv_path, start_ts, end_ts)
            save_cache(bot_pair, data)
    else:
        print("[SKIP] Q4 master archive")

    # ── kraken_research_data (Jan–Feb 2026, all pairs) ─────────────────────
    if not args.skip_research and NAS_BASE_RESEARCH.exists():
        print("\n=== kraken_research_data (Jan–Feb 2026) ===")
        # Use a broad window for research data (it's always 2026)
        research_start = to_ts("2026-01-01")
        research_end = to_ts("2026-12-31") + 86399
        for bot_pair in RESEARCH_PAIRS:
            print(f"\n{bot_pair}: ohlc_60m.csv")
            data = load_research_csv(bot_pair, research_start, research_end)
            save_cache(bot_pair, data)
    else:
        print("[SKIP] kraken_research_data")

    print("\nDone.")


if __name__ == "__main__":
    main()
