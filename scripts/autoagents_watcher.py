#!/usr/bin/env python3
"""
Autoagents watcher:
- Polls Kraken TradeBalance every 60s
- If account equity drops below initial_balance * (1 - TRIGGER_PCT) it starts an optimizer agent (optimize.py)
- Limits concurrent optimizer agents to MAX_CONCURRENT
- Respects AUTOAPPLY flag in config: optimizer already auto-applies if enabled
- Logs to reports/autoagents.log
"""
import time, os, subprocess, json
from pathlib import Path
from dotenv import load_dotenv
from krakenex import API
import toml

REPO = Path('/home/felix/tradingbot')
LOG = REPO / 'reports' / 'autoagents.log'
CFG = REPO / 'config.toml'
OPT_SCRIPT = REPO / 'optimize.py'
POLL_SECS = int(os.environ.get('AUTOAGENTS_POLL_SECS', '60'))
TRIGGER_PCT = float(os.environ.get('AUTOAGENTS_TRIGGER_PCT', '0.01'))  # 1% drawdown
MAX_CONCURRENT = int(os.environ.get('AUTOAGENTS_MAX_CONCURRENT', '2'))

load_dotenv(REPO / '.env')
api = API()
api.key = os.getenv('KRAKEN_API_KEY')
api.secret = os.getenv('KRAKEN_API_SECRET')

def log(msg):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f"{ts} - {msg}\n"
    with open(LOG, 'a') as f:
        f.write(line)
    print(line, end='')

# read initial balance from config
try:
    cfg = toml.load(CFG)
    initial = float(cfg.get('bot_settings', {}).get('trade_amounts', {}).get('initial_balance', 100.0))
except Exception:
    initial = 100.0

log(f'Autoagents watcher started (trigger {TRIGGER_PCT*100:.2f}%, max concurrent {MAX_CONCURRENT}) - initial_balance={initial}')

while True:
    try:
        r = api.query_private('TradeBalance')
        if r.get('error'):
            log(f'TradeBalance error: {r.get("error")}')
            time.sleep(POLL_SECS)
            continue
        tb = r.get('result', {})
        eb = float(tb.get('eb') or tb.get('e') or 0)
        pct_drop = (initial - eb) / initial
        log(f'Equity eb={eb:.4f} pct_drop={(pct_drop*100):.3f}%')
        if pct_drop >= TRIGGER_PCT:
            # check number of running optimizer agents
            out = subprocess.run(['pgrep','-f','optimize.py'], capture_output=True, text=True)
            pids = [ln for ln in out.stdout.splitlines() if ln.strip()]
            if len(pids) < MAX_CONCURRENT:
                # start optimizer
                log('Trigger condition met -> starting optimizer agent')
                subprocess.Popen(['/usr/bin/env','nohup','/home/felix/tradingbot/venv/bin/python',str(OPT_SCRIPT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                log('Optimizer started')
            else:
                log(f'Optimizer instances {len(pids)} >= max {MAX_CONCURRENT}, skipping start')
    except Exception as e:
        log('Watcher exception: '+str(e))
    time.sleep(POLL_SECS)
