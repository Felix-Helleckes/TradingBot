#!/usr/bin/env python3
import subprocess, json, os, sys, shutil, itertools

base_dir = '/home/felix/tradingbot'
config_path = os.path.join(base_dir, 'config.toml')
script_path = os.path.join(base_dir, 'scripts/backtest_v3_detailed.py')
backup_path = config_path + '.bak'

# Target return percent
TARGET = 2.0

# Parameter ranges
allocs = [30, 40, 50, 60, 80]  # higher allocation
tps = [1.0, 1.5, 2.0, 2.5, 3.0]  # lower TP for quicker exits
sls = [0.5, 1.0, 1.5]  # tight SL
# Keep MR and TB on
# Pyramiding: test true
# Hard stop: enable with SL as hard stop
# Daytrading: false (normal hold) but we can also test true with tighter intraday later

fee = 0.001
slippage = 0
execution = 'immediate'

for alloc, tp, sl in itertools.product(allocs, tps, sls):
    for pyr in [True]:  # only pyramiding true for now
        # backup config
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
                new_lines.append('enable_pyramiding = true\n')
            elif stripped.startswith('enable_hard_stop_loss ='):
                new_lines.append('enable_hard_stop_loss = true\n')
            elif stripped.startswith('hard_stop_loss_percent ='):
                new_lines.append(f'hard_stop_loss_percent = {sl}\n')
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
        if ret >= TARGET:
            print(f'TARGET REACHED! Return {ret:.2f}%')
            print(json.dumps(data, indent=2))
            sys.exit(0)
        # else continue
    # end pyr loop
# after loops
print(f'No combination reached {TARGET}% return. Exiting.')
sys.exit(1)