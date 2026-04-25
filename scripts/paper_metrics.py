#!/usr/bin/env python3
"""Run the bot in paper mode for a short period and summarize trade metrics.

This script will run `main.py --paper` for a specified number of seconds and
then parse `logs/trade_events.jsonl` (or `reports/trade_journal.csv`) to
produce simple metrics: trades, win-rate, avg PnL, avg slippage.

Usage: python scripts/paper_metrics.py --duration 3600
"""
import argparse
import subprocess
import time
import csv
import json
from pathlib import Path


def parse_jsonl(path):
    events = []
    if not path.exists():
        return events
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events


def summarize(events):
    buys = [e for e in events if e.get('side') == 'BUY']
    sells = [e for e in events if e.get('side') == 'SELL']
    trades = len(buys) + len(sells)
    profits = [e.get('profit_eur') for e in events if e.get('profit_eur') is not None]
    slippages = [e.get('slippage_pct') for e in events if e.get('slippage_pct') is not None]
    def avg(l):
        return sum(l) / len(l) if l else 0.0

    print(f"Events parsed: {len(events)}")
    print(f"Trades (BUY+SELL): {trades}")
    print(f"Avg profit EUR: {avg(profits):.4f}")
    print(f"Avg slippage %: {avg(slippages):.4f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--duration', type=int, default=300, help='seconds to run the paper bot')
    args = p.parse_args()

    # Start main.py in paper mode
    proc = subprocess.Popen(['python', 'main.py', '--paper'])
    try:
        time.sleep(args.duration)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    # try reading JSONL trade events first
    jsonl = Path('logs') / 'trade_events.jsonl'
    events = parse_jsonl(jsonl)
    if not events:
        # try CSV journal
        csvp = Path('reports') / 'trade_journal.csv'
        if csvp.exists():
            with csvp.open() as fh:
                reader = csv.DictReader(fh)
                for r in reader:
                    # expect 'profit_eur' and 'slippage_pct' columns optional
                    e = {}
                    try:
                        e['profit_eur'] = float(r.get('profit_eur') or 0.0)
                    except Exception:
                        e['profit_eur'] = None
                    try:
                        e['slippage_pct'] = float(r.get('slippage_pct') or 0.0)
                    except Exception:
                        e['slippage_pct'] = None
                    events.append(e)

    summarize(events)


if __name__ == '__main__':
    main()
