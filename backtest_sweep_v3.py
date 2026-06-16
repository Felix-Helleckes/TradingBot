#!/usr/bin/env python3
"""
Baseline + continuous improvement backtest sweep.
Runs baseline with current config, then sweeps params to beat it.
Writes progress to /tmp/backtest_sweep.log
"""
import subprocess, json, os, sys, shutil, itertools, time
from datetime import datetime

BASE = '/home/felix/tradingbot'
CONFIG = f'{BASE}/config.toml'
BACKUP = f'{CONFIG}.backtest_bak'
SCRIPT = f'{BASE}/scripts/backtest_v3_detailed.py'
LOGFILE = '/tmp/backtest_sweep.log'

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    with open(LOGFILE, 'a') as f:
        f.write(f'[{ts}] {msg}\n')

def run_backtest(cmd_args, label):
    """Run backtest with given CLI args, return return_pct or None"""
    cmd = ['python3', SCRIPT, '--days', '30'] + cmd_args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    idx = out.find('{')
    if idx == -1:
        return None
    try:
        data = json.loads(out[idx:])
    except:
        return None
    ret = data.get('return_pct', 0.0)
    trades = data.get('closed_trades', 0)
    wins = data.get('wins', 0)
    drawdown = data.get('max_drawdown_pct', 0)
    log(f"  {label}: return={ret:.2f}% trades={trades} winrate={wins/trades*100 if trades else 0:.0f}% dd={drawdown:.1f}%")
    return ret

# 1. BASELINE - current config with standard CLI args
log("=== BASELINE: current config.toml + --fee 0.001 --slippage-bps 0 --execution-mode immediate ===")
shutil.copy(CONFIG, BACKUP)
baseline = run_backtest(['--fee', '0.001', '--slippage-bps', '0', '--execution-mode', 'immediate'], 'BASELINE')
shutil.copy(BACKUP, CONFIG)
os.remove(BACKUP)

if baseline is None:
    log("ERROR: baseline failed")
    sys.exit(1)

log(f"BASELINE RETURN: {baseline:.2f}%")
log(f"TARGET: beat {baseline:.2f}%")

# 2. PARAMETER SWEEP
# Vary: allocation (40,60,80), tp (1.0,1.5,2.0,2.5,3.0), pyramiding (T/F), 
#       hard_stop (false), sl disabled, fee/slippage fixed
log("=== STARTING SWEEP ===")

best_ret = baseline
best_params = None
best_data = None

allocs = [40, 60, 80]
tps = [1.0, 1.5, 2.0, 2.5, 3.0]
pyr_opts = [False, True]
# Also try ATR variations
atr_mult_opts = [2.0, 3.0, 4.0]
atr_trail_opts = [1.0, 2.0, 3.0]
atr_tp_mult_opts = [1.0, 1.5, 2.0]
mr_opts = [True]
tb_opts = [True]

total = len(allocs) * len(tps) * len(pyr_opts) * len(atr_mult_opts) * len(atr_trail_opts) * len(atr_tp_mult_opts)
log(f"Total combinations: {total}")
count = 0

for alloc in allocs:
    for tp in tps:
        for pyr in pyr_opts:
            for atr_m in atr_mult_opts:
                for atr_tr in atr_trail_opts:
                    for atr_tp in atr_tp_mult_opts:
                        count += 1
                        # Modify config
                        shutil.copy(CONFIG, BACKUP)
                        with open(CONFIG, 'r') as f:
                            lines = f.readlines()
                        new_lines = []
                        for line in lines:
                            s = line.strip()
                            if s.startswith('allocation_per_trade_percent ='):
                                new_lines.append(f'allocation_per_trade_percent = {alloc}\n')
                            elif s.startswith('take_profit_percent ='):
                                new_lines.append(f'take_profit_percent = {tp}\n')
                            elif s.startswith('enable_pyramiding ='):
                                new_lines.append(f'enable_pyramiding = {str(pyr).lower()}\n')
                            elif s.startswith('atr_multiplier =') and not s.startswith('atr_trail'):
                                new_lines.append(f'    atr_multiplier = {atr_m}\n')
                            elif s.startswith('atr_trail_multiplier ='):
                                new_lines.append(f'    atr_trail_multiplier = {atr_tr}\n')
                            elif s.startswith('atr_tp_multiplier ='):
                                new_lines.append(f'    atr_tp_multiplier = {atr_tp}\n')
                            else:
                                new_lines.append(line)
                        with open(CONFIG, 'w') as f:
                            f.writelines(new_lines)
                        
                        ret = run_backtest(['--fee', '0.001', '--slippage-bps', '0', '--execution-mode', 'immediate'],
                                          f'[{count}/{total}] a={alloc} tp={tp} pyr={pyr} atr_m={atr_m} tr={atr_tr} tp_m={atr_tp}')
                        
                        shutil.copy(BACKUP, CONFIG)
                        os.remove(BACKUP)
                        
                        if ret is not None and ret > best_ret:
                            best_ret = ret
                            best_params = (alloc, tp, pyr, atr_m, atr_tr, atr_tp)
                            log(f"  *** NEW BEST: {ret:.2f}% ***")
                        
                        if count % 50 == 0:
                            log(f"Progress: {count}/{total}, best so far: {best_ret:.2f}%")

log(f"=== SWEEP COMPLETE ===")
log(f"BEST: {best_ret:.2f}% (baseline was {baseline:.2f}%)")
log(f"PARAMS: {best_params}")

# If we beat baseline, apply best config
if best_ret > baseline:
    alloc, tp, pyr, atr_m, atr_tr, atr_tp = best_params
    shutil.copy(CONFIG, BACKUP)
    with open(CONFIG, 'r') as f:
        lines = f.readlines()
    new_lines = []
    for line in lines:
        s = line.strip()
        if s.startswith('allocation_per_trade_percent ='):
            new_lines.append(f'allocation_per_trade_percent = {alloc}\n')
        elif s.startswith('take_profit_percent ='):
            new_lines.append(f'take_profit_percent = {tp}\n')
        elif s.startswith('enable_pyramiding ='):
            new_lines.append(f'enable_pyramiding = {str(pyr).lower()}\n')
        elif s.startswith('atr_multiplier =') and not s.startswith('atr_trail'):
            new_lines.append(f'    atr_multiplier = {atr_m}\n')
        elif s.startswith('atr_trail_multiplier ='):
            new_lines.append(f'    atr_trail_multiplier = {atr_tr}\n')
        elif s.startswith('atr_tp_multiplier ='):
            new_lines.append(f'    atr_tp_multiplier = {atr_tp}\n')
        else:
            new_lines.append(line)
    with open(CONFIG, 'w') as f:
        f.writelines(new_lines)
    log(f"Applied best config to config.toml")
    # Kill bot so wrapper restarts it with new config
    subprocess.run(['pkill', '-f', 'main.py'], capture_output=True)
    log("Sent restart signal to bot")
else:
    log("No improvement over baseline, keeping original config")

log("DONE")