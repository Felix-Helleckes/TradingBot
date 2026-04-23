#!/usr/bin/env python3
"""
Collect 15-minute OHLCV data from Kraken for daytrading backtesting.
Writes to data/daytrading_15m/{PAIR}_{since}_{end}_15m.json

Usage:
    python3 scripts/collect_15m_daytrading.py           # last 90 days all pairs
    python3 scripts/collect_15m_daytrading.py --days 30 # last 30 days
    python3 scripts/collect_15m_daytrading.py --pair XETHZEUR --days 60

Format: {timestamp: close_price} (same as mentor_cache_1h format, just 15m buckets)

Run once to seed data, then re-run to extend (incremental: reads existing file,
resumes from last known timestamp).
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import nas_paths as _nas_paths

PAIRS = ["XETHZEUR", "SOLEUR", "ADAEUR", "XXRPZEUR", "LINKEUR"]
INTERVAL = 15  # minutes
OUT_DIR = _nas_paths()["bot_cache"] / "daytrading_15m"
OUT_DIR.mkdir(parents=True, exist_ok=True)

sess = requests.Session()


def fetch_ohlc_15m(pair: str, since: int, end_ts: int) -> dict:
    """Fetch 15m OHLC from Kraken API incrementally. Returns {ts: close}."""
    out = {}
    current = since
    loops = 0

    while current < end_ts and loops < 1000:
        loops += 1
        for attempt in range(8):
            try:
                r = sess.get(
                    "https://api.kraken.com/0/public/OHLC",
                    params={"pair": pair, "interval": INTERVAL, "since": current},
                    timeout=30,
                )
                j = r.json()
            except Exception as e:
                print(f"  [{pair}] request error (attempt {attempt+1}): {e}")
                time.sleep(2 + attempt)
                continue

            errs = j.get("error") or []
            if errs and any("Too many requests" in e for e in errs):
                print(f"  [{pair}] rate-limited, waiting...")
                time.sleep(2 + attempt * 1.5)
                continue
            if errs:
                print(f"  [{pair}] API error: {errs}")
                return out
            break
        else:
            print(f"  [{pair}] gave up after retries")
            return out

        res = j.get("result", {})
        key = [k for k in res.keys() if k != "last"]
        if not key:
            break
        rows = res[key[0]]
        if not rows:
            break

        last_ts = current
        new_rows = 0
        for row in rows:
            ts = int(row[0])
            close = float(row[4])
            if since <= ts <= end_ts:
                out[ts] = close
                new_rows += 1
            last_ts = max(last_ts, ts)

        nxt = int(res.get("last", last_ts + 1))
        current = nxt if nxt > current else (last_ts + 1)

        print(
            f"  [{pair}] fetched {new_rows} bars up to {datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}"
        )

        if new_rows == 0:
            break
        # Stop when last bar is within one candle of the end of our window
        # (prevents infinite loop at the current-time boundary)
        if last_ts >= end_ts - INTERVAL * 60:
            break
        if current >= end_ts:
            break

        time.sleep(0.5)  # be polite to Kraken API

    return out


def collect_pair(pair: str, days: int) -> None:
    end_ts = int(datetime.now(timezone.utc).timestamp())
    since_full = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

    # Check if we have an existing file to extend
    existing_files = sorted(OUT_DIR.glob(f"{pair}_*_15m.json"))
    existing_data = {}
    resume_from = since_full

    if existing_files:
        latest = existing_files[-1]
        try:
            existing_data = {int(k): float(v) for k, v in json.loads(latest.read_text()).items()}
            if existing_data:
                resume_from = max(existing_data.keys()) + (INTERVAL * 60)
                print(
                    f"[{pair}] Resuming from {datetime.fromtimestamp(resume_from, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} ({len(existing_data)} bars already cached)"
                )
        except Exception:
            pass

    if resume_from >= end_ts - 60:
        print(f"[{pair}] Already up to date ({len(existing_data)} bars)")
        return

    print(
        f"[{pair}] Collecting {INTERVAL}m bars: {datetime.fromtimestamp(resume_from, tz=timezone.utc).strftime('%Y-%m-%d')} → now"
    )
    new_data = fetch_ohlc_15m(pair, resume_from, end_ts)

    if not new_data and not existing_data:
        print(f"[{pair}] No data fetched, skipping")
        return

    merged = {**existing_data, **new_data}
    # Filter to requested window
    merged = {k: v for k, v in merged.items() if since_full <= k <= end_ts}

    # Save: name encodes the actual coverage range
    actual_since = min(merged.keys()) if merged else since_full
    out_path = OUT_DIR / f"{pair}_{actual_since}_{end_ts}_15m.json"

    # Remove old files for this pair (replace with merged)
    for old in existing_files:
        try:
            old.unlink()
        except Exception:
            pass

    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged))
    tmp.replace(out_path)
    print(f"[{pair}] Saved {len(merged)} bars → {out_path.name}")


def main():
    ap = argparse.ArgumentParser(description="Collect 15m Kraken OHLC for daytrading backtest")
    ap.add_argument("--days", type=int, default=90, help="How many days of history to collect (default: 90)")
    ap.add_argument("--pair", type=str, default=None, help="Single pair to collect (default: all PAIRS)")
    args = ap.parse_args()

    pairs = [args.pair.upper()] if args.pair else PAIRS
    print(f"Collecting {args.days}d of 15m data for {len(pairs)} pair(s)...\n")

    for pair in pairs:
        collect_pair(pair, args.days)
        time.sleep(0.3)

    total_bars = sum(
        len(json.loads(f.read_text())) for p in pairs for f in sorted(OUT_DIR.glob(f"{p}_*_15m.json"))[-1:]
    )
    print(f"\nDone. Total bars available: {total_bars:,}")
    print(f"Output: {OUT_DIR.resolve()}/")


if __name__ == "__main__":
    main()
