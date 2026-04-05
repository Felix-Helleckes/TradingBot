#!/usr/bin/env python3
import csv
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
import os

PAIRS = ["XXBTZEUR", "XETHZEUR", "SOLEUR", "ADAEUR", "DOTEUR", "XXRPZEUR", "LINKEUR"]
INTERVALS = [1, 15, 60]
BASE_DIR = Path(os.getenv('COLLECT_BASE_DIR', '/mnt/fritz_nas/Volume/kraken/2026/ohlc'))
STATE_DIR = BASE_DIR / '_state'
STATE_FILE = STATE_DIR / 'collector_state.json'
LOG_FILE = BASE_DIR / 'collector_runtime.log'
FALLBACK_LOG_FILE = Path('/tmp/kraken_research_collector.log')
# Keep runtime lock local to avoid CIFS stale-handle lock failures
LOCK_FILE = Path('/tmp/kraken_research_collector.lock')

DEFAULT_LOOKBACK_DAYS = 365 * 5
# Trading-first throttle profile: lower API pressure during live bot runtime
CYCLE_SLEEP_SEC = 900
REQUEST_SLEEP_SEC = 1.2

sess = requests.Session()


def log(msg: str):
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n"
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
    except OSError:
        with open(FALLBACK_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line)


def load_state():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2), encoding='utf-8')
    tmp.replace(STATE_FILE)


def acquire_lock():
    import os
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            if Path(f'/proc/{pid}').exists():
                raise SystemExit(f"collector already running (pid {pid})")
        except Exception:
            pass
    LOCK_FILE.write_text(str(os.getpid()))


def release_lock():
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def fetch_ohlc(pair: str, interval: int, since: int):
    for attempt in range(10):
        r = sess.get('https://api.kraken.com/0/public/OHLC', params={'pair': pair, 'interval': interval, 'since': since}, timeout=30)
        j = r.json()
        errs = j.get('error') or []
        if errs and any('Too many requests' in e for e in errs):
            time.sleep(1.5 + attempt)
            continue
        if errs:
            raise RuntimeError(f"{pair} {interval}m {errs}")
        key = [k for k in j['result'].keys() if k != 'last'][0]
        rows = j['result'][key]
        nxt = int(j['result'].get('last', since + 1))
        return rows, nxt
    return [], since + 1


def ensure_csv(pair: str, interval: int):
    out_dir = BASE_DIR / pair
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f'ohlc_{interval}m.csv'
    if not out_file.exists() or out_file.stat().st_size == 0:
        with open(out_file, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['ts', 'open', 'high', 'low', 'close', 'vwap', 'volume', 'count'])
    return out_file


def append_rows(out_file: Path, rows, min_ts: int, last_ts: int):
    written = 0
    new_last = last_ts
    with open(out_file, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        for row in rows:
            ts = int(row[0])
            if ts < min_ts or ts <= last_ts:
                continue
            w.writerow(row[:8])
            written += 1
            if ts > new_last:
                new_last = ts
    return written, new_last


def run_cycle(state):
    now_ts = int(time.time())
    min_ts_default = now_ts - DEFAULT_LOOKBACK_DAYS * 86400

    total_written = 0
    for pair in PAIRS:
        for interval in INTERVALS:
            key = f"{pair}:{interval}"
            item = state.get(key, {})
            last_ts = int(item.get('last_ts', min_ts_default - 1))
            min_ts = int(item.get('min_ts', min_ts_default))
            out_file = ensure_csv(pair, interval)

            since = last_ts + 1
            rows, nxt = fetch_ohlc(pair, interval, since)
            if not rows:
                state[key] = {'last_ts': last_ts, 'min_ts': min_ts, 'file': str(out_file)}
                continue

            written, new_last = append_rows(out_file, rows, min_ts=min_ts, last_ts=last_ts)
            total_written += written
            state[key] = {'last_ts': new_last, 'min_ts': min_ts, 'file': str(out_file)}

            log(f"{pair} {interval}m wrote={written} last_ts={new_last} next={nxt}")
            time.sleep(REQUEST_SLEEP_SEC)

    save_state(state)
    return total_written


def main():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    log('collector incremental START (single run)')
    try:
        wrote = run_cycle(state)
        log(f'cycle complete wrote={wrote}')
    except Exception as e:
        log(f'ERROR cycle: {e}')


if __name__ == '__main__':
    acquire_lock()
    try:
        main()
    finally:
        release_lock()
