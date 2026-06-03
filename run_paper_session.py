#!/usr/bin/env python3
"""Launch a paper-mode TradingBot in a child process for a fixed duration, then terminate and summarize trade events.

Usage: python run_paper_session.py --duration 300
"""
import argparse
import subprocess
import time
import os
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--duration', type=int, default=300)
args = p.parse_args()
DURATION = args.duration

BASE = Path(__file__).parent
LOGS = BASE / 'logs'
LOGS.mkdir(exist_ok=True)
CHILD_LOG = LOGS / 'paper_session_child.log'
SUMMARY_LOG = LOGS / 'paper_session_summary.txt'

# Child code: import TradingBot, instantiate with paper config, start trading loop
child_code = r"""
import sys, toml, os
sys.path.insert(0, '/home/felix/tradingbot')
from kraken_interface import KrakenAPI
from trading_bot import TradingBot
cfg = toml.load('/home/felix/tradingbot/config.paper.toml')
kraken = KrakenAPI(api_key='', api_secret='', paper_mode=True)
bot = TradingBot(kraken, cfg)
print('PAPER_CHILD_STARTED')
# start_trading is blocking; this child will be terminated by parent after duration
bot.start_trading()
"""

with open(CHILD_LOG, 'ab') as outfh:
    proc = subprocess.Popen([os.getenv('VIRTUAL_ENV', '') + '/bin/python' if os.getenv('VIRTUAL_ENV') else 'python', '-u', '-c', child_code], stdout=outfh, stderr=outfh)
    try:
        start_ts = time.time()
        # wait with polling so we can be responsive to early termination
        while True:
            if proc.poll() is not None:
                break
            if time.time() - start_ts >= DURATION:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    proc.kill()
                break
            time.sleep(1)
    finally:
        # ensure process is dead
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

# Summarize trade events if any
events = []
jsonl = LOGS / 'trade_events.jsonl'
if jsonl.exists():
    try:
        for ln in jsonl.read_text(encoding='utf-8').splitlines():
            import json
            try:
                events.append(json.loads(ln))
            except Exception:
                continue
    except Exception:
        pass

with open(SUMMARY_LOG, 'w') as fh:
    fh.write(f'Ran paper session for {DURATION} seconds.\n')
    fh.write(f'Child log: {CHILD_LOG}\n')
    fh.write(f'Trade events parsed: {len(events)}\n')
    if events:
        profits = [e.get('profit_eur') for e in events if e.get('profit_eur') is not None]
        slippages = [e.get('slippage_pct') for e in events if e.get('slippage_pct') is not None]
        fh.write(f'Trades: {len(events)}\n')
        fh.write(f'Avg profit EUR: {sum(profits)/len(profits) if profits else 0.0}\n')
        fh.write(f'Avg slippage %: {sum(slippages)/len(slippages) if slippages else 0.0}\n')

print('PAPER_SESSION_DONE')
print('Child log:', CHILD_LOG)
print('Summary log:', SUMMARY_LOG)
