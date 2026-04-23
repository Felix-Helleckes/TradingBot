#!/usr/bin/env python3
"""Walk-forward validator for top N grid candidates.
Reads backtest_full_grid_expanded_results.jsonl, picks top N candidates by total_return_pct,
and runs Backtester for multiple start dates to check robustness.

Outputs results to reports/walkforward_top10_results.jsonl and summary JSON.
"""
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

try:
    import toml

    from kraken_interface import KrakenAPI
    from trading_bot import Backtester
except Exception as e:
    print("import error", e)
    raise

IN_JSONL = os.path.join(REPO, "reports", "backtest_full_grid_expanded_results.jsonl")
OUT_JSONL = os.path.join(REPO, "reports", "walkforward_top10_results.jsonl")
SUMMARY = os.path.join(REPO, "reports", "walkforward_top10_summary.json")

# windows: run backtests starting from these start dates (ISO)
now = datetime.utcnow()
windows = [now - timedelta(days=365), now - timedelta(days=270), now - timedelta(days=180)]
window_strs = [d.date().isoformat() for d in windows]

# choose top N
N = 10
candidates = []
if not os.path.exists(IN_JSONL):
    print("no input results jsonl at", IN_JSONL)
    raise SystemExit(1)
with open(IN_JSONL, "r", encoding="utf-8") as f:
    for ln in f:
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("total_return_pct") is None:
            continue
        candidates.append(o)

if not candidates:
    print("no candidate results")
    raise SystemExit(1)

candidates.sort(key=lambda x: x["total_return_pct"] if x.get("total_return_pct") is not None else -9999, reverse=True)
selected = candidates[:N]
print("Selected", len(selected), "candidates for walk-forward")

# run backtests
results = []
for idx, cand in enumerate(selected, start=1):
    print(
        f'[{idx}/{len(selected)}] candidate TR={cand.get("total_return_pct")} cfg: TP={cand.get("take_profit_percent")} net={cand.get("min_net")} re={cand.get("min_reentry")}'
    )
    for ws in window_strs:
        cfg = toml.load(os.path.join(REPO, "config.toml"))
        # apply candidate params
        cfg.setdefault("risk_management", {})
        if "min_net" in cand:
            cfg["risk_management"]["min_net_sell_profit_pct"] = float(cand.get("min_net"))
        if "min_reentry" in cand:
            cfg["risk_management"]["min_reentry_profit_pct"] = float(cand.get("min_reentry"))
        if "take_profit_percent" in cand:
            cfg["risk_management"]["take_profit_percent"] = float(cand.get("take_profit_percent"))
        if "sell_fee_buffer_percent" in cand:
            cfg["risk_management"]["sell_fee_buffer_percent"] = float(cand.get("sell_fee_buffer_percent"))
        if "atr_multiplier" in cand:
            cfg["risk_management"]["atr_multiplier"] = float(cand.get("atr_multiplier"))
        if "allocation_per_trade_percent" in cand:
            cfg.setdefault("bot_settings", {})
            cfg["bot_settings"]["allocation_per_trade_percent"] = float(cand.get("allocation_per_trade_percent"))
        # set backtesting start date
        cfg.setdefault("backtesting", {})
        cfg["backtesting"]["start_date"] = ws
        # run
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            bt = Backtester(KrakenAPI("", ""), cfg)
            bt.run()
        except Exception as e:
            print("ERROR running bt for candidate", e)
        finally:
            sys.stdout = old_stdout
        out = buf.getvalue()
        # parse metrics
        tr = None
        dd = None
        trades = None
        for line in out.splitlines():
            m = re.search(r"Total Return:\s*([0-9.+-]+)%", line)
            if m:
                tr = float(m.group(1))
            m2 = re.search(r"Max Drawdown:\s*([0-9.+-]+)%", line)
            if m2:
                dd = float(m2.group(1))
            m3 = re.search(r"Total Trades:\s*(\d+)", line)
            if m3:
                trades = int(m3.group(1))
        res = {
            "candidate_index": idx,
            "base_candidate": cand,
            "window_start": ws,
            "total_return_pct": tr,
            "max_drawdown_pct": dd,
            "trades": trades,
            "raw": out,
        }
        with open(OUT_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(res) + "\n")
        results.append(res)
        # small sleep
        time.sleep(1)

# summary
valid = [r for r in results if r.get("total_return_pct") is not None]
best = None
if valid:
    valid.sort(key=lambda x: x["total_return_pct"], reverse=True)
    best = valid[0]
summary = {"runs_done": len(results), "best": best}
with open(SUMMARY, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)
print("Walk-forward done. Summary:", SUMMARY)
