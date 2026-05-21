#!/usr/bin/env python3
"""Auto-apply monitor: watches paper_canary backtest results and promotes config to live when
promotion criteria met. Runs as a background process.

Behavior:
- Monitors /home/felix/tradingbot/reports/paper_canary/backtest_results.jsonl for new entries.
- When the average return over the last N runs >= promotion_threshold_return_pct AND max_drawdown_pct <= max_live_drawdown_stop_percent,
  AND kraken_api keys in config.toml are non-empty, then:
  - Back up config.toml
  - Update allocation_per_trade_percent in bot_settings and risk_management to allocation_per_trade_percent
  - Restart main.py process (graceful SIGTERM) so bot picks up new config
  - Log the action

Safety: only runs if .trading_skill_accepted exists. No other external changes.
"""

import time, os, json, signal, sys, shutil, re
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(__file__)) if os.path.basename(__file__) != 'auto_apply_monitor.py' else os.path.dirname(__file__)
BASE = '/home/felix/tradingbot'
PAPER_JSONL = os.path.join(BASE, 'reports', 'paper_canary', 'backtest_results.jsonl')
CONFIG = os.path.join(BASE, 'config.toml')
ACCEPT = os.path.join(BASE, '.trading_skill_accepted')
LOG = os.path.join(BASE, 'reports', 'auto_apply_monitor.log')

# Parameters: read from env or defaults
ALLOCATION = float(os.environ.get('AUTO_APPLY_ALLOCATION', '3.0'))
PROMOTE_THRESH = float(os.environ.get('AUTO_APPLY_PROMOTE_THRESH', '0.75'))
MAX_DD = float(os.environ.get('AUTO_APPLY_MAX_DD', '3.0'))
CHECK_INTERVAL = int(os.environ.get('AUTO_APPLY_CHECK_INTERVAL', '300'))  # seconds
REQUIRED_RUNS = int(os.environ.get('AUTO_APPLY_REQUIRED_RUNS', '3'))

def log(msg):
    ts = datetime.utcnow().isoformat()+'Z'
    s = f"[{ts}] {msg}\n"
    with open(LOG,'a') as f:
        f.write(s)
    print(s, end='')

if not os.path.exists(ACCEPT):
    print("Auto-apply monitor: legal acceptance missing; exiting")
    sys.exit(1)

log(f"Auto-apply monitor started: allocation={ALLOCATION}%, promote_thresh={PROMOTE_THRESH}%, max_dd={MAX_DD}%")

def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out=[]
    try:
        with open(path,'r') as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out

def config_has_keys():
    try:
        with open(CONFIG,'r') as f:
            txt = f.read()
        m_key = re.search(r"key\s*=\s*\"(.*)\"", txt)
        m_secret = re.search(r"secret\s*=\s*\"(.*)\"", txt)
        key = m_key.group(1).strip() if m_key else ''
        secret = m_secret.group(1).strip() if m_secret else ''
        return bool(key) and bool(secret)
    except Exception:
        return False

def promote_config():
    # backup
    bak = CONFIG + '.bak.' + datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    shutil.copy2(CONFIG, bak)
    log(f'Backed up config to {bak}')
    # update file: set allocation_per_trade_percent in bot_settings and risk_management
    with open(CONFIG,'r') as f:
        txt = f.read()
    txt_new = re.sub(r"(\[bot_settings\][^\[]*?allocation_per_trade_percent\s*=\s*)([0-9.]+)", lambda m: m.group(1)+str(ALLOCATION), txt, flags=re.S)
    txt_new = re.sub(r"(\[risk_management\][^\[]*?allocation_per_trade_percent\s*=\s*)([0-9.]+)", lambda m: m.group(1)+str(ALLOCATION), txt_new, flags=re.S)
    with open(CONFIG,'w') as f:
        f.write(txt_new)
    log(f'Updated allocation_per_trade_percent to {ALLOCATION}% in {CONFIG}')
    # restart main.py by sending SIGTERM to the process; assume a restart supervisor will respawn or we will relaunch
    # find main.py pid
    pids=[]
    try:
        import subprocess
        out = subprocess.check_output(['pgrep','-f','/home/felix/tradingbot/main.py']).decode().strip().split() if shutil.which('pgrep') else []
        pids = [int(x) for x in out if x]
    except Exception:
        pids=[]
    if pids:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
                log(f'Sent SIGTERM to main.py pid {pid}')
            except Exception as e:
                log(f'Failed to SIGTERM pid {pid}: {e}')
    else:
        log('No main.py process found to restart; promotion completed but manual start may be required')

# Monitoring loop
while True:
    try:
        runs = read_jsonl(PAPER_JSONL)
        if len(runs) >= REQUIRED_RUNS:
            recent = runs[-REQUIRED_RUNS:]
            # compute average return_pct and max drawdown among recent
            rets = [r.get('return_pct') for r in recent if isinstance(r.get('return_pct'), (int,float))]
            mdds = [r.get('max_drawdown_pct') for r in recent if isinstance(r.get('max_drawdown_pct'), (int,float))]
            if rets and mdds:
                avg_ret = sum(rets)/len(rets)
                max_dd_recent = max(mdds)
                log(f'Checked recent runs: avg_return={avg_ret}%, max_dd={max_dd_recent}%')
                if avg_ret >= PROMOTE_THRESH and max_dd_recent <= MAX_DD:
                    log('Promotion criteria met')
                    if config_has_keys():
                        log('API keys found in config; proceeding to promote and restart')
                        promote_config()
                        log('Promotion action complete; exiting monitor')
                        break
                    else:
                        log('API keys missing in config; cannot promote to live. Waiting for keys to be added.')
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        log(f'Error in loop: {e}')
        time.sleep(CHECK_INTERVAL)

log('Auto-apply monitor exiting')
