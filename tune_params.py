#!/usr/bin/env python3
import subprocess, json, os, sys, itertools, shutil, time

base_dir = '/home/felix/tradingbot'
config_path = os.path.join(base_dir, 'config.toml')
script_path = os.path.join(base_dir, 'scripts/backtest_v3_detailed.py')
backup_path = config_path + '.bak'

# Parameter grid
allocations = [10, 20, 30, 40, 50]
take_profits = [2.0, 3.0, 4.0, 5.0]
stop_losses = [1.0, 2.0, 3.0, 4.0, 5.0]
mr_opts = [True]  # keep mean reversion on
tb_opts = [True]  # keep trend breakout on
daytrading_opts = [False]  # disable daytrading for normal hold
pyramiding_opts = [False, True]

# Fixed
fee = 0.001
slippage = 0
execution = 'immediate'
daytrading_flag = False  # CLI flag

best_ret = -float('inf')
best_data = None
best_params = None
total = len(allocations)*len(take_profits)*len(stop_losses)*len(mr_opts)*len(tb_opts)*len(daytrading_opts)*len(pyramiding_opts)
print(f'Testing {total} combinations...')
count = 0

for alloc, tp, sl, mr, tb, dt_cfg, pyr in itertools.product(allocations, take_profits, stop_losses, mr_opts, tb_opts, daytrading_opts, pyramiding_opts):
    count += 1
    # Backup original config
    shutil.copy(config_path, backup_path)
    # Read and modify
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
            # note: there is no stop_loss_percent directly; there is hard_stop_loss_percent and also intraday_sl_percent etc.
            # We'll adjust hard_stop_loss_percent if we want to use hard stop.
            # For now we will adjust hard_stop_loss_percent and enable_hard_stop_loss based on sl.
            # We'll handle separately below.
            new_lines.append(line)  # keep unchanged, will adjust later
        elif stripped.startswith('enable_mean_reversion_signals ='):
            new_lines.append(f'enable_mean_reversion_signals = {str(mr).lower()}\n')
        elif stripped.startswith('enable_trend_breakout_signals ='):
            new_lines.append(f'enable_trend_breakout_signals = {str(tb).lower()}\n')
        elif stripped.startswith('enable_daytrading ='):
            new_lines.append(f'enable_daytrading = {str(dt_cfg).lower()}\n')
        elif stripped.startswith('enable_pyramiding ='):
            new_lines.append(f'enable_pyramiding = {str(pyr).lower()}\n')
        else:
            new_lines.append(line)
    # Now adjust hard stop loss and enable based on sl (if sl <= 20 maybe)
    # We'll enable hard stop loss if sl <= 20 (reasonable)
    enable_hard = sl <= 20.0
    hard_stop_val = sl if enable_hard else 99.0
    # Insert or replace these two lines
    # We'll do a second pass: replace lines for hard stop
    # Let's just do another loop over new_lines to replace.
    final_lines = []
    for line in new_lines:
        stripped = line.strip()
        if stripped.startswith('enable_hard_stop_loss ='):
            final_lines.append(f'enable_hard_stop_loss = {str(enable_hard).lower()}\n')
        elif stripped.startswith('hard_stop_loss_percent ='):
            final_lines.append(f'hard_stop_loss_percent = {hard_stop_val}\n')
        else:
            final_lines.append(line)
    with open(config_path, 'w') as f:
        f.writelines(final_lines)
    # Run backtest
    cmd = ['python3', script_path, '--days', '30', '--fee', str(fee), '--slippage-bps', str(slippage), '--execution-mode', execution]
    if daytrading_flag:
        cmd.append('--daytrading')
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        shutil.copy(backup_path, config_path)
        continue
    # Restore config
    shutil.copy(backup_path, config_path)
    if result.returncode != 0:
        # print error briefly
        sys.stderr.write(f'[{count}] error\n')
        continue
    out = result.stdout.strip()
    idx = out.find('{')
    if idx == -1:
        sys.stderr.write(f'[{count}] no json\n')
        continue
    try:
        data = json.loads(out[idx:])
    except json.JSONDecodeError:
        sys.stderr.write(f'[{count}] json error\n')
        continue
    ret = data.get('return_pct', 0.0)
    if ret > best_ret:
        best_ret = ret
        best_data = data
        best_params = (alloc, tp, sl, mr, tb, dt_cfg, pyr, enable_hard, hard_stop_val)
    if count % 10 == 0:
        sys.stderr.write(f'[{count}/{total}] best so far {best_ret:.2f}%\n')
# After loop
sys.stderr.write(f'Finished. Best {best_ret:.2f}% with params {best_params}\n')
if best_data:
    sys.stdout.write(json.dumps(best_data, indent=2))
sys.exit(0 if best_ret >= 0 else 1)