#!/usr/bin/env python3
import csv
import gzip
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import nas_paths
import requests

PAIRS = ["XXBTZEUR", "XETHZEUR", "SOLEUR", "ADAEUR", "DOTEUR", "XXRPZEUR", "LINKEUR"]
INTERVALS = [1, 15, 60]  # 1m, 15m, 1h
BASE_DIR = nas_paths()["ohlc_2026"]
BASE_DIR.mkdir(parents=True, exist_ok=True)

sess = requests.Session()

def fetch_ohlc(pair: str, interval: int, since: int):
    for attempt in range(8):
        r = sess.get('https://api.kraken.com/0/public/OHLC', params={'pair': pair, 'interval': interval, 'since': since}, timeout=30)
        j = r.json()
        errs = j.get('error') or []
        if errs and any('Too many requests' in e for e in errs):
            time.sleep(1.5 + attempt)
            continue
        if errs:
            raise RuntimeError(f'{pair} {interval}m {errs}')
        key = [k for k in j['result'].keys() if k != 'last'][0]
        return j['result'][key], int(j['result'].get('last', since + 1))
    return [], since + 1

def collect_pair_interval(pair: str, interval: int, days_back: int = 365 * 5):
    out_dir = BASE_DIR / f'{pair}'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f'ohlc_{interval}m_5y.csv.gz'

    start_ts = int(time.time()) - days_back * 86400
    since = start_ts

    rows_written = 0
    with gzip.open(out_file, 'wt', newline='') as gz:
        w = csv.writer(gz)
        w.writerow(['ts', 'open', 'high', 'low', 'close', 'vwap', 'volume', 'count'])
        while since < int(time.time()):
            rows, nxt = fetch_ohlc(pair, interval, since)
            if not rows:
                break
            max_ts = since
            for row in rows:
                ts = int(row[0])
                if ts < start_ts:
                    continue
                w.writerow(row[:8])
                rows_written += 1
                if ts > max_ts:
                    max_ts = ts
            if nxt <= since:
                since = max_ts + 1
            else:
                since = nxt
            time.sleep(0.35)
    return rows_written, out_file


def main():
    log = BASE_DIR / 'collector.log'
    with open(log, 'a') as lf:
        lf.write(f"\n[{datetime.now(timezone.utc).isoformat()}] START 5y collection\n")
        for p in PAIRS:
            for i in INTERVALS:
                try:
                    n, f = collect_pair_interval(p, i)
                    lf.write(f"{p} {i}m -> {n} rows -> {f}\n")
                    lf.flush()
                except Exception as e:
                    lf.write(f"ERROR {p} {i}m: {e}\n")
                    lf.flush()
        lf.write(f"[{datetime.now(timezone.utc).isoformat()}] DONE\n")

if __name__ == '__main__':
    main()
