#!/usr/bin/env python3
"""Incremental OHLC filler for the unified NAS kraken/ structure, with sharding support.

Usage:
  fill_missing_ohlc.py [--shard-index N] [--shard-count M]

If shard_count > 1 the script only processes every M-th pair starting at index N
(0-based). This lets us split the collector into multiple timed shards to avoid
hitting Kraken public API limits.
"""
from __future__ import annotations
import time
import json
import sys
import requests
import argparse
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import nas_paths

INTERVAL = 60  # minutes
SLEEP_BETWEEN = 0.45  # seconds between public API calls (conservative)
MAX_LOOPS = 1000

BASES = [nas_paths()["ohlc_2026"]]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--shard-index', type=int, default=0, help='0-based shard index')
    p.add_argument('--shard-count', type=int, default=1, help='total shard count')
    return p.parse_args()


def load_last_ts(fpath: Path) -> int:
    if not fpath.exists():
        return 0
    last = 0
    with fpath.open('r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(',')
            try:
                ts = int(float(parts[0]))
            except Exception:
                continue
            last = max(last, ts)
    return last


def append_rows(fpath: Path, rows):
    # rows: list of (ts, close)
    with fpath.open('a', encoding='utf-8') as f:
        for ts, close in rows:
            f.write(f"{int(ts)},{float(close)}\n")


def fetch_ohlc_pair(pair: str, since_ts: int, end_ts: int) -> list:
    out = []
    sess = requests.Session()
    loops = 0
    since = since_ts
    while since < end_ts and loops < MAX_LOOPS:
        loops += 1
        params = {'pair': pair, 'interval': INTERVAL, 'since': since}
        try:
            r = sess.get('https://api.kraken.com/0/public/OHLC', params=params, timeout=30)
            j = r.json()
        except Exception as e:
            print('API error', e)
            time.sleep(2)
            continue
        errs = j.get('error') or []
        if errs:
            print('Kraken error for', pair, errs)
            time.sleep(2)
            continue
        res = j.get('result', {})
        key = [k for k in res.keys() if k != 'last']
        if not key:
            break
        rows = res[key[0]]
        if not rows:
            break
        last_ts = since
        chunk = []
        for row in rows:
            ts = int(row[0])
            close = float(row[4])
            if ts > since_ts and ts <= end_ts:
                chunk.append((ts, close))
            last_ts = max(last_ts, ts)
        if chunk:
            out.extend(chunk)
        nxt = int(res.get('last', last_ts + 1))
        if nxt <= since:
            break
        since = nxt
        time.sleep(SLEEP_BETWEEN)
    return out


def main():
    args = parse_args()
    shard_index = int(args.shard_index)
    shard_count = int(args.shard_count)

    now_ts = int(datetime.now(timezone.utc).timestamp())
    # only fill 2026 onwards (keep 2025 intact)
    fill_start_ts = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())

    print(f'Starting fill_missing_ohlc: shard {shard_index}/{shard_count-1} (count={shard_count})')
    for BASE in BASES:
        if not BASE.exists():
            print('Base path missing:', BASE)
            continue
        # enumerate pairs and optionally shard
        all_pairs = sorted([p.name for p in BASE.iterdir() if p.is_dir()])
        if shard_count > 1:
            pairs = [p for i, p in enumerate(all_pairs) if (i % shard_count) == shard_index]
        else:
            pairs = all_pairs
        print(f'Processing base {BASE}: total_pairs={len(all_pairs)} shard_pairs={len(pairs)}')

        for p in pairs:
            pair_dir = BASE / p
            fpath = pair_dir / f'ohlc_{INTERVAL}m.csv'
            last = load_last_ts(fpath)
            if last == 0:
                since = fill_start_ts
                print(f'{BASE}/{p}: no file, will create from {datetime.utcfromtimestamp(since)}')
                pair_dir.mkdir(parents=True, exist_ok=True)
            else:
                since = last + 1 if last >= fill_start_ts else fill_start_ts
                print(f'{BASE}/{p}: last ts {datetime.utcfromtimestamp(last)} -> will fetch from {datetime.utcfromtimestamp(since)}')
            if since >= now_ts:
                print(f'{BASE}/{p}: up-to-date')
                continue
            try:
                rows = fetch_ohlc_pair(p, since, now_ts)
            except Exception as e:
                print('fetch failed for', p, e)
                continue
            if not rows:
                print(f'{BASE}/{p}: no new rows')
                continue
            rows.sort()
            append_rows(fpath, rows)
            print(f'{BASE}/{p}: appended {len(rows)} rows, newest {datetime.utcfromtimestamp(rows[-1][0])}')

if __name__ == '__main__':
    main()
