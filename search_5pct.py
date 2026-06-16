#!/usr/bin/env python3
import subprocess, json, os, itertools, sys

base_cmd = ["python3", "scripts/backtest_v3_detailed.py", "--days", "30"]
# Define a few promising combos
combos = []
for alloc in [30, 40, 50]:
    for mr in [True]:
        for tb in [True]:
            for tp in [3.0, 4.0, 5.0]:
                for hs_en in [True]:
                    for hs_sl in [3.0, 5.0]:
                        combos.append((alloc, mr, tb, tp, hs_en, hs_sl))
# also test with pyramiding maybe later

print(f"Testing {len(combos)} combos")
best_ret = -float('inf')
best_data = None
for i, (alloc, mr, tb, tp, hs_en, hs_sl) in enumerate(combos):
    # backup original config
    subprocess.run(["cp", "/home/felix/tradingbot/config.toml", "/home/felix/tradingbot/config.toml.bak"], check=False)
    # modify config
    with open("/home/felix/tradingbot/config.toml", "r") as f:
        lines = f.readlines()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('allocation_per_trade_percent ='):
            new_lines.append(f'allocation_per_trade_percent = {alloc}\n')
        elif stripped.startswith('enable_mean_reversion_signals ='):
            new_lines.append(f'enable_mean_reversion_signals = {str(mr).lower()}\n')
        elif stripped.startswith('enable_trend_breakout_signals ='):
            new_lines.append(f'enable_trend_breakout_signals = {str(tb).lower()}\n')
        elif stripped.startswith('take_profit_percent ='):
            new_lines.append(f'take_profit_percent = {tp}\n')
        elif stripped.startswith('enable_hard_stop_loss ='):
            new_lines.append(f'enable_hard_stop_loss = {str(hs_en).lower()}\n')
        elif stripped.startswith('hard_stop_loss_percent ='):
            new_lines.append(f'hard_stop_loss_percent = {hs_sl}\n')
        else:
            new_lines.append(line)
    with open("/home/felix/tradingbot/config.toml", "w") as f:
        f.writelines(new_lines)
    # run backtest
    try:
        result = subprocess.run(base_cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        result = None
    # restore config
    subprocess.run(["mv", "/home/felix/tradingbot/config.toml.bak", "/home/felix/tradingbot/config.toml"], check=False)
    if result is None or result.returncode != 0:
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
        best_params = (alloc, mr, tb, tp, hs_en, hs_sl)
    if ret >= 5.0:
        print(f"Found >=5%: {json.dumps(data, indent=2)}")
        sys.exit(0)
    sys.stderr.write(f'[{i+1}/{len(combos)}] alloc={alloc} mr={mr} tb={tb} tp={tp} hs={hs_en}/{hs_sl} -> {ret:.2f}% best {best_ret:.2f}%\n')
sys.stderr.write(f'Finished. Best {best_ret:.2f}% with params {best_params}\n')
if best_data:
    sys.stderr.write(json.dumps(best_data, indent=2) + '\n')
sys.exit(0 if best_ret >= 5.0 else 1)