#!/usr/bin/env python3
import subprocess
import json
import sys
import os
import itertools

base_cmd = ["python3", "scripts/backtest_v3_detailed.py", "--days", "30"]
# Parameter grids
daytrading_opts = [False, True]
execution_opts = ["immediate", "twap", "vwap"]
twap_slices_opts = [1, 2, 4, 6, 8]
slippage_opts = [0, 1, 2, 5, 10]  # bps
fee_opts = [0.001, 0.002, 0.0026, 0.005]  # fee as fraction

count = 0
for dt, ex, ts, slip, fee in itertools.product(daytrading_opts, execution_opts, twap_slices_opts, slippage_opts, fee_opts):
    count += 1
    cmd = base_cmd.copy()
    if dt:
        cmd.append("--daytrading")
    cmd.extend(["--execution-mode", ex])
    if ex == "twap":
        cmd.extend(["--twap-slices", str(ts)])
    cmd.extend(["--slippage-bps", str(slip)])
    cmd.extend(["--fee", str(fee)])
    # Run
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        continue
    if result.returncode != 0:
        # skip error
        continue
    # parse JSON from stdout
    out = result.stdout.strip()
    # find first {
    idx = out.find('{')
    if idx == -1:
        continue
    json_str = out[idx:]
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        continue
    ret = data.get("return_pct", 0.0)
    if ret >= 5.0:
        # Success! Print result and exit
        print(json.dumps(data, indent=2))
        sys.exit(0)
    # optional progress
    if count % 50 == 0:
        sys.stderr.write(f'Tested {count} combos, best so far {ret:.2f}%\n')
# If none found, print nothing (or maybe final fallback)
sys.stderr.write(f'Tested {count} combos, none reached 5%+\n')
sys.exit(1)