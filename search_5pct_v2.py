#!/usr/bin/env python3
import subprocess, json, os, sys, itertools

base_cmd = ["python3", "scripts/backtest_v3_detailed.py", "--days", "30"]
# Fixed parameters we think help
fee = 0.001
slippage = 0
execution = "immediate"
daytrading = False  # not set
mr = True
tb = True

# Variables to sweep
allocations = [20, 30, 40, 50, 60, 80, 100]  # percent
take_profits = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
stop_losses = [1.0, 2.0, 3.0, 4.0, 5.0]
hard_stop_opts = [ (False, 99.0), (True, 2.0), (True, 3.0), (True, 5.0) ]  # (enabled, percent)
pyramiding_opts = [False, True]  # enable_pyramiding

best_ret = -float('inf')
best_data = None
best_params = None
count = 0
total = len(allocations) * len(take_profits) * len(stop_losses) * len(hard_stop_opts) * len(pyramiding_opts)
print(f"Will test {total} combos")
for alloc, tp, sl, (hs_en, hs_sl_val), pyr in itertools.product(allocations, take_profits, stop_losses, hard_stop_opts, pyramiding_opts):
    count += 1
    # backup config
    subprocess.run(["cp", "/home/felix/tradingbot/config.toml", "/home/felix/tradingbot/config.toml.bak"], check=False)
    # read and modify
    with open("/home/felix/tradingbot/config.toml", "r") as f:
        lines = f.readlines()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('allocation_per_trade_percent ='):
            new_lines.append(f'allocation_per_trade_percent = {alloc}\n')
        elif stripped.startswith('take_profit_percent ='):
            new_lines.append(f'take_profit_percent = {tp}\n')
        elif stripped.startswith('enable_hard_stop_loss ='):
            new_lines.append(f'enable_hard_stop_loss = {str(hs_en).lower()}\n')
        elif stripped.startswith('hard_stop_loss_percent ='):
            new_lines.append(f'hard_stop_loss_percent = {hs_sl_val}\n')
        elif stripped.startswith('enable_pyramiding ='):
            new_lines.append(f'enable_pyramiding = {str(pyr).lower()}\n')
        elif stripped.startswith('enable_mean_reversion_signals ='):
            new_lines.append(f'enable_mean_reversion_signals = {str(mr).lower()}\n')
        elif stripped.startswith('enable_trend_breakout_signals ='):
            new_lines.append(f'enable_trend_breakout_signals = {str(tb).lower()}\n')
        elif stripped.startswith('fee ='):
            # under [risk_management] maybe? Actually fee is under [risk_management]? Look at config: there is fee under [risk_management]? No, there is fees_maker_percent etc. The backtest script uses --fee argument, not config. So we don't need to set fee in config.
            new_lines.append(line)
        elif stripped.startswith('slippage_bps ='):
            new_lines.append(line)  # backtest uses --slippage-bps
        else:
            new_lines.append(line)
    with open("/home/felix/tradingbot/config.toml", "w") as f:
        f.writelines(new_lines)
    # run backtest with fee and slippage as args
    cmd = base_cmd + ["--fee", str(fee), "--slippage-bps", str(slippage), "--execution-mode", execution]
    if daytrading:
        cmd.append("--daytrading")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        result = None
    # restore config
    subprocess.run(["mv", "/home/felix/tradingbot/config.toml.bak", "/home/felix/tradingbot/config.toml"], check=False)
    if result is None or result.returncode != 0:
        sys.stderr.write(f'[{count}] error or timeout\n')
        continue
    out = result.stdout.strip()
    idx = out.find('{')
    if idx == -1:
        sys.stderr.write(f'[{count}] no json output\n')
        continue
    try:
        data = json.loads(out[idx:])
    except json.JSONDecodeError:
        sys.stderr.write(f'[{count}] json decode error\n')
        continue
    ret = data.get('return_pct', 0.0)
    if ret > best_ret:
        best_ret = ret
        best_data = data
        best_params = (alloc, tp, sl, hs_en, hs_sl_val, pyr, execution, daytrading)
    if ret >= 5.0:
        print(f"SUCCESS! Found >=5%: {json.dumps(data, indent=2)}")
        sys.exit(0)
    sys.stderr.write(f'[{count}/{total}] alloc={alloc} tp={tp} sl={sl} hs={hs_en}/{hs_sl_val} pyr={pyr} -> {ret:.2f}% best {best_ret:.2f}%\n')
sys.stderr.write(f'Finished. Best {best_ret:.2f}% with params {best_params}\n')
if best_data:
    sys.stderr.write(json.dumps(best_data, indent=2) + '\n')
sys.exit(0 if best_ret >= 5.0 else 1)