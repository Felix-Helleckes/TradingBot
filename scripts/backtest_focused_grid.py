#!/usr/bin/env python3
"""Focused grid backtest runner.
Runs parameter sweep and writes JSONL results + summary.
"""
import io
import json
import os
import re
import sys
import time
from datetime import datetime
from itertools import product

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

try:
    import toml

    from kraken_interface import KrakenAPI
    from trading_bot import Backtester
except Exception as e:
    print("IMPORT_ERROR", e)
    raise

OUT_JSONL = os.path.join(REPO, "reports", "backtest_focused_grid_results.jsonl")
SUMMARY = os.path.join(REPO, "reports", "backtest_focused_grid_summary.json")
LOG = os.path.join(REPO, "reports", "backtest_focused_grid_run.log")

# Parameter ranges
take_profit_vals = [4.0, 5.0, 6.0, 7.0]
sell_fee_buffer_vals = [0.45, 0.55, 0.75]
min_net_vals = [2.0, 3.0, 5.0]
min_reentry_vals = [3.0, 5.0]
atr_mult_vals = [2.0, 3.0]
allocation_vals = [10.0, 15.0]

# Safety: limit max runs if env set (useful for testing)
MAX_RUNS = int(os.environ.get("FOCUSED_GRID_MAX_RUNS", "0"))

combos = list(
    product(take_profit_vals, sell_fee_buffer_vals, min_net_vals, min_reentry_vals, atr_mult_vals, allocation_vals)
)
if MAX_RUNS and MAX_RUNS > 0:
    combos = combos[:MAX_RUNS]

print(f"Starting focused grid: {len(combos)} runs -> {OUT_JSONL}")

# clear output files
os.makedirs(os.path.join(REPO, "reports"), exist_ok=True)
if os.path.exists(OUT_JSONL):
    # rotate
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    os.rename(OUT_JSONL, OUT_JSONL + "." + ts)

# helper to run a single configuration
p_att = re.compile(r"Total Return:\s*([0-9.+-]+)%")
p_dd = re.compile(r"Max Drawdown:\s*([0-9.+-]+)%")
p_tr = re.compile(r"Total Trades:\s*(\d+)")
p_wr = re.compile(r"Win Rate:\s*([0-9.+-]+)%")

run_count = 0
for tp, fee_buf, min_net, min_reentry, atr_mult, alloc in combos:
    run_count += 1
    cfg_path = os.path.join(REPO, "config.toml")
    cfg = toml.load(cfg_path) if os.path.exists(cfg_path) else {}
    cfg.setdefault("risk_management", {})
    cfg["risk_management"]["take_profit_percent"] = float(tp)
    cfg["risk_management"]["sell_fee_buffer_percent"] = float(fee_buf)
    cfg["risk_management"]["min_net_sell_profit_pct"] = float(min_net)
    cfg["risk_management"]["min_reentry_profit_pct"] = float(min_reentry)
    cfg["risk_management"]["atr_multiplier"] = float(atr_mult)
    cfg.setdefault("bot_settings", {})
    cfg["bot_settings"]["allocation_per_trade_percent"] = float(alloc)
    # set a short backtest interval (overrideable)
    cfg.setdefault("backtesting", {})
    cfg["backtesting"]["interval"] = 60
    cfg["backtesting"]["start_date"] = cfg["backtesting"].get("start_date", "2024-01-01")
    cfg["backtesting"]["initial_balance"] = cfg["backtesting"].get("initial_balance", 1000.0)

    # run backtester capturing stdout
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        bt = Backtester(KrakenAPI("", ""), cfg)
        bt.run()
    except Exception as e:
        print("ERROR", str(e))
    finally:
        sys.stdout = old
    out = buf.getvalue()

    # parse metrics
    tr = None
    dd = None
    trades = None
    wr = None
    m = p_att.search(out)
    if m:
        try:
            tr = float(m.group(1))
        except Exception:
            tr = None
    m = p_dd.search(out)
    if m:
        try:
            dd = float(m.group(1))
        except Exception:
            dd = None
    m = p_tr.search(out)
    if m:
        try:
            trades = int(m.group(1))
        except Exception:
            trades = None
    m = p_wr.search(out)
    if m:
        try:
            wr = float(m.group(1))
        except Exception:
            wr = None

    res = {
        "timestamp": datetime.utcnow().isoformat(),
        "params": {
            "take_profit_percent": tp,
            "sell_fee_buffer_percent": fee_buf,
            "min_net_sell_profit_pct": min_net,
            "min_reentry_profit_pct": min_reentry,
            "atr_multiplier": atr_mult,
            "allocation_per_trade_percent": alloc,
        },
        "metrics": {
            "total_return_pct": tr,
            "max_drawdown_pct": dd,
            "total_trades": trades,
            "win_rate_pct": wr,
        },
        "raw": out,
    }

    with open(OUT_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(res) + "\n")

    print(
        f"Run {run_count}/{len(combos)}: TP={tp} fee_buf={fee_buf} min_net={min_net} reentry={min_reentry} atr={atr_mult} alloc={alloc} -> TR={tr} DD={dd} trades={trades}"
    )
    # small sleep to give cache/backoff a breath
    time.sleep(0.8)

# summary
all_results = []
with open(OUT_JSONL, "r", encoding="utf-8") as f:
    for ln in f:
        try:
            all_results.append(json.loads(ln))
        except Exception:
            continue

valid = [r for r in all_results if r.get("metrics", {}).get("total_return_pct") is not None]
valid.sort(key=lambda r: r["metrics"]["total_return_pct"], reverse=True)
summary = {
    "total_runs": len(all_results),
    "valid_runs": len(valid),
    "best": valid[0] if valid else None,
    "generated_at": datetime.utcnow().isoformat(),
}
with open(SUMMARY, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)

print("Focused grid complete. Summary written to", SUMMARY)
