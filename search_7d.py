#!/usr/bin/env python3
import subprocess, json, os, sys, itertools, time

base_dir = '/home/felix/tradingbot'
script = os.path.join(base_dir, 'scripts/backtest_v3_detailed.py')
config_path = os.path.join(base_dir, 'config.toml')
backup_path = config_path + '.bak'

# Base command
base_cmd = ['python3', script, '--days', '7']

# Parameters to vary
allocations = [10, 20, 30, 40, 50]  # percent
take_profits = [1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
stop_losses = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
fee_options = [0.001, 0.002]  # 0.1% and 0.2%
slippage_options = [0, 1, 2]  # bps
execution_options = ['immediate']  # could add twap later
mr_options = [True, False]
tb_options = [True, False]

# We'll also test daytrading flag
daytrading_options = [False, True]

# Limit combos: we'll iterate but break early if found
count = 0
for alloc, tp, sl, fee, slip, exec_mode, mr, tb, daytrade in itertools.product(
        allocations, take_profits, stop_losses, fee_options, slippage_options,
        execution_options, mr_options, tb_options, daytrading_options):
    count += 1
    # Backup original config
    subprocess.run(['cp', config_path, backup_path], check=False)
    # Modify config
    with open(config_path, 'r') as f:
        lines = f.readlines()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('allocation_per_trade_percent ='):
            new_lines.append(f'allocation_per_trade_percent = {alloc}\n')
        elif stripped.startswith('take_profit_percent ='):
            new_lines.append(f'take_profit_percent = {tp}\n')
        elif stripped.startswith('stop_loss_percent ='):
            # Actually config uses hard_stop_loss_percent? but we have stop_loss_percent? Not present. We'll skip.
            new_lines.append(line)
        elif stripped.startswith('enable_mean_reversion_signals ='):
            new_lines.append(f'enable_mean_reversion_signals = {str(mr).lower()}\n')
        elif stripped.startswith('enable_trend_breakout_signals ='):
            new_lines.append(f'enable_trend_breakout_signals = {str(tb).lower()}\n')
        else:
            new_lines.append(line)
    with open(config_path, 'w') as f:
        f.writelines(new_lines)
    # Build command
    cmd = base_cmd + ['--fee', str(fee), '--slippage-bps', str(slip), '--execution-mode', exec_mode]
    if mr:
        # already set via config
        pass
    if tb:
        pass
    if daytrade:
        cmd.append('--daytrading')
    # Run
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        result = None
    # Restore config
    subprocess.run(['mv', backup_path, config_path], check=False)
    if result is None or result.returncode != 0:
        sys.stderr.write(f'[{count}] error/timeout\\n')
        continue
    out = result.stdout.strip()
    idx = out.find('{')
    if idx == -1:
        sys.stderr.write(f'[{count}] no json\\n')
        continue
    try:
        data = json.loads(out[idx:])
    except json.JSONDecodeError:
        sys.stderr.write(f'[{count}] json error\\n')
        continue
    ret = data.get('return_pct', 0.0)
    if ret > 0:
        print(json.dumps(data, indent=2))
        sys.exit(0)
    if count % 20 == 0:
        sys.stderr.write(f'Tested {count} combos, best so far?\\n')
# If loop ends without positive
sys.stderr.write('No positive found in grid.\\n')
sys.exit(1)