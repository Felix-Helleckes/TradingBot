#!/usr/bin/env python3
import subprocess, json, os, sys, itertools, shutil

base_dir = '/home/felix/tradingbot'
config_path = os.path.join(base_dir, 'config.toml')
script_path = os.path.join(base_dir, 'scripts/backtest_v3_detailed.py')
backup_path = config_path + '.bak'

# Fixed
fee = 0.001
slippage = 0
execution = 'immediate'
daytrading_flag = False

# Ranges around previously good: alloc 20, tp 3, sl 1
allocs = [10, 15, 20, 25, 30]
tps = [2.0, 2.5, 3.0, 3.5, 4.0]
sls = [0.5, 1.0, 1.5, 2.0, 2.5]
# mr and tb keep true
# pyramiding false/true
# hard stop: we will test enable hard stop = true with sl as hard stop percent, else false
# Also test daytrading false (normal) and maybe daytrading true with tighter intraday settings? We'll keep daytrading false for now.

best_ret = -float('inf')
best_data = None
best_params = None

for alloc, tp, sl in itertools.product(allocs, tps, sls):
    for pyr in [False, True]:
        for ht in [False, True]:
            # ht = use hard stop (if true, enable hard stop loss = true, hard_stop_loss_percent = sl)
            # else disable hard stop
            shutil.copy(config_path, backup_path)
            with open(config_path, 'r') as f:
                lines = f.readlines()
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith('allocation_per_trade_percent ='):
                    new_lines.append(f'allocation_per_trade_percent = {alloc}\n')
                elif stripped.startswith('take_profit_percent ='):
                    new_lines.append(f'take_profit_percent = {tp}\n')
                elif stripped.startswith('enable_mean_reversion_signals ='):
                    new_lines.append('enable_mean_reversion_signals = true\n')
                elif stripped.startswith('enable_trend_breakout_signals ='):
                    new_lines.append('enable_trend_breakout_signals = true\n')
                elif stripped.startswith('enable_daytrading ='):
                    new_lines.append('enable_daytrading = false\n')
                elif stripped.startswith('enable_pyramiding ='):
                    new_lines.append(f'enable_pyramiding = {str(pyr).lower()}\n')
                elif stripped.startswith('enable_hard_stop_loss ='):
                    new_lines.append(f'enable_hard_stop_loss = {str(ht).lower()}\n')
                elif stripped.startswith('hard_stop_loss_percent ='):
                    if ht:
                        new_lines.append(f'hard_stop_loss_percent = {sl}\n')
                    else:
                        new_lines.append('hard_stop_loss_percent = 99.0\n')  # effectively disabled
                else:
                    new_lines.append(line)
            with open(config_path, 'w') as f:
                f.writelines(new_lines)
            cmd = ['python3', script_path, '--days', '30', '--fee', str(fee), '--slippage-bps', str(slippage), '--execution-mode', execution]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                shutil.copy(backup_path, config_path)
                continue
            shutil.copy(backup_path, config_path)
            if result.returncode != 0:
                continue
            out = result.stdout.strip()
            idx = out.find('{')
            if idx == -1:
                continue
            try:
                data = json.loads(out[idx:])
            except json.JSONDecodeError:
                continue
            ret = data.get('return_pct', 0.0)
            if ret > best_ret:
                best_ret = ret
                best_data = data
                best_params = (alloc, tp, sl, pyr, ht)
            # print progress
            sys.stderr.write(f'alloc={alloc} tp={tp} sl={sl} pyr={pyr} ht={ht} -> {ret:.2f}% best {best_ret:.2f}%\n')

sys.stderr.write(f'Finished. Best {best_ret:.2f}% with params {best_params}\n')
if best_data:
    sys.stdout.write(json.dumps(best_data, indent=2))
sys.exit(0)