#!/usr/bin/env python3
import subprocess, json, os, sys, shutil

base_dir = '/home/felix/tradingbot'
config_path = os.path.join(base_dir, 'config.toml')
script_path = os.path.join(base_dir, 'scripts/backtest_v3_detailed.py')
backup_path = config_path + '.bak'

# Fixed
fee = 0.001
slippage = 0
execution = 'immediate'
daytrading_flag = True  # we will also set enable_daytrading = true in config

# Daytrading params
max_hold_hours = 4
intraday_sl = 1.5
intraday_tp = 2.0
intraday_cooldown = 300

shutil.copy(config_path, backup_path)
with open(config_path, 'r') as f:
    lines = f.readlines()
new_lines = []
for line in lines:
    stripped = line.strip()
    if stripped.startswith('allocation_per_trade_percent ='):
        new_lines.append('allocation_per_trade_percent = 20\n')
    elif stripped.startswith('take_profit_percent ='):
        # In daytrading mode, take_profit_percent may be ignored? We'll set anyway.
        new_lines.append('take_profit_percent = 2.0\n')
    elif stripped.startswith('stop_loss_percent ='):
        # there is no stop_loss_percent; we will adjust hard stop later
        new_lines.append(line)
    elif stripped.startswith('enable_mean_reversion_signals ='):
        new_lines.append('enable_mean_reversion_signals = true\n')
    elif stripped.startswith('enable_trend_breakout_signals ='):
        new_lines.append('enable_trend_breakout_signals = true\n')
    elif stripped.startswith('enable_daytrading ='):
        new_lines.append('enable_daytrading = true\n')
    elif stripped.startswith('max_hold_hours ='):
        new_lines.append(f'max_hold_hours = {max_hold_hours}\n')
    elif stripped.startswith('intraday_sl_percent ='):
        new_lines.append(f'intraday_sl_percent = {intraday_sl}\n')
    elif stripped.startswith('intraday_tp_percent ='):
        new_lines.append(f'intraday_tp_percent = {intraday_tp}\n')
    elif stripped.startswith('intraday_cooldown_seconds ='):
        new_lines.append(f'intraday_cooldown_seconds = {intraday_cooldown}\n')
    elif stripped.startswith('enable_pyramiding ='):
        new_lines.append('enable_pyramiding = false\n')
    elif stripped.startswith('enable_hard_stop_loss ='):
        new_lines.append('enable_hard_stop_loss = false\n')
    elif stripped.startswith('hard_stop_loss_percent ='):
        new_lines.append('hard_stop_loss_percent = 99.0\n')
    else:
        new_lines.append(line)
with open(config_path, 'w') as f:
    f.writelines(new_lines)

cmd = ['python3', script_path, '--days', '30', '--fee', str(fee), '--slippage-bps', str(slippage), '--execution-mode', execution]
if daytrading_flag:
    cmd.append('--daytrading')
print('Running:', ' '.join(cmd))
try:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
except subprocess.TimeoutExpired:
    print('timeout')
    shutil.copy(backup_path, config_path)
    sys.exit(1)
shutil.copy(backup_path, config_path)
if result.returncode != 0:
    print('error:', result.stderr)
    sys.exit(1)
out = result.stdout.strip()
print('Output:')
print(out)
idx = out.find('{')
if idx != -1:
    try:
        data = json.loads(out[idx:])
        print('Parsed:')
        print(json.dumps(data, indent=2))
    except Exception as e:
        print('JSON error:', e)
else:
    print('No JSON found')