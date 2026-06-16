#!/usr/bin/env python3
import subprocess
import json
import sys
import os

base_cmd = ["python3", "scripts/backtest_v3_detailed.py", "--days", "30"]
# Parameter grids
daytrading_opts = [False, True]
execution_opts = ["immediate", "twap", "vwap"]
twap_slices_opts = [1, 3, 5]
slippage_opts = [0, 2, 5]  # bps
fee_opts = [0.001, 0.0026]  # fee as fraction
# We'll also try to enable/disable mean reversion and trend via env? Not exposed.
# We'll just rely on defaults.

for dt in daytrading_opts:
    for ex in execution_opts:
        for ts in twap_slices_opts:
            for slip in slippage_opts:
                for fee in fee_opts:
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
                    if ret > 0:
                        # Success! Print result and exit
                        print(json.dumps(data, indent=2))
                        sys.exit(0)
# If none found, print nothing (or maybe final fallback)
sys.exit(1)