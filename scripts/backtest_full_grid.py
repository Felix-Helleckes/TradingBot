#!/usr/bin/env python3
"""Full grid backtest runner.
Writes incremental results to reports/backtest_full_grid_results.jsonl and
final summary to reports/backtest_full_grid_summary.json.

Usage: run inside repo venv: /home/felix/tradingbot/venv/bin/python scripts/backtest_full_grid.py
"""
import sys, os, time, json, itertools, io, re
from datetime import datetime
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, REPO_ROOT)

try:
    import toml
    from trading_bot import Backtester
    from kraken_interface import KrakenAPI
    import utils
    append_jsonl = getattr(utils, 'append_jsonl_locked', None)
except Exception as e:
    print('Import error', e)
    raise

OUT_DIR = os.path.join(REPO_ROOT, 'reports')
os.makedirs(OUT_DIR, exist_ok=True)
RESULTS_JSONL = os.path.join(OUT_DIR, 'backtest_full_grid_results.jsonl')
SUMMARY_JSON = os.path.join(OUT_DIR, 'backtest_full_grid_summary.json')
LOG = os.path.join(OUT_DIR, 'backtest_full_grid_run.log')

# Parameter grid (conservative size)
min_net_list = [0.0, 2.0, 3.0, 5.0]
min_reentry_list = [0.0, 3.0, 5.0]
tp_list = [4.0, 6.0]
fee_buf_list = [0.45, 0.55]
atr_list = [2.0, 3.0]
alloc_list = [10.0, 15.0]

grid = list(itertools.product(min_net_list, min_reentry_list, tp_list, fee_buf_list, atr_list, alloc_list))
TOTAL = len(grid)
print(f"Starting full grid backtest: {TOTAL} runs — results -> {RESULTS_JSONL}")
start_ts = time.time()

# load base config
cfg_path = os.path.join(REPO_ROOT, 'config.toml')
base_cfg = toml.load(cfg_path)

# helper to run a single backtest and parse outputs
re_tr = re.compile(r"Total Return:\s*([0-9.+-]+)%")
re_dd = re.compile(r"Max Drawdown:\s*([0-9.+-]+)%")
re_trades = re.compile(r"Total Trades:\s*(\d+)")

def run_backtest_for_cfg(cfg):
    # capture stdout from Backtester.run
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        bt = Backtester(KrakenAPI('',''), cfg)
        bt.run()
    except Exception as e:
        print('ERROR running backtester:', e)
    finally:
        sys.stdout = old_stdout
    out = buf.getvalue()
    tr = None
    dd = None
    trades = None
    for line in out.splitlines():
        m = re_tr.search(line)
        if m:
            tr = float(m.group(1))
        m = re_dd.search(line)
        if m:
            dd = float(m.group(1))
        m = re_trades.search(line)
        if m:
            trades = int(m.group(1))
    return tr, dd, trades, out

# iterate grid
i = 0
for (min_net, min_reentry, tp, fee_buf, atr, alloc) in grid:
    i += 1
    cfg = json.loads(json.dumps(base_cfg))  # deep copy via json
    # set parameters
    cfg.setdefault('risk_management', {})
    cfg['risk_management']['min_net_sell_profit_pct'] = float(min_net)
    cfg['risk_management']['min_reentry_profit_pct'] = float(min_reentry)
    cfg['risk_management']['take_profit_percent'] = float(tp)
    cfg['risk_management']['sell_fee_buffer_percent'] = float(fee_buf)
    cfg['risk_management']['atr_multiplier'] = float(atr)
    cfg.setdefault('bot_settings', {})
    cfg['bot_settings']['allocation_per_trade_percent'] = float(alloc)

    print(f"[{i}/{TOTAL}] min_net={min_net} reentry={min_reentry} TP={tp} fee_buf={fee_buf} ATR={atr} alloc={alloc} — running...")
    tr, dd, trades, raw = run_backtest_for_cfg(cfg)
    result = {
        'ts': datetime.utcnow().isoformat(),
        'min_net': min_net,
        'min_reentry': min_reentry,
        'take_profit_percent': tp,
        'sell_fee_buffer_percent': fee_buf,
        'atr_multiplier': atr,
        'allocation_per_trade_percent': alloc,
        'total_return_pct': tr,
        'max_drawdown_pct': dd,
        'trades': trades,
        'raw': raw
    }
    # append to JSONL (use utils helper if available)
    try:
        if append_jsonl:
            ok = append_jsonl(RESULTS_JSONL, result)
            if not ok:
                # fallback
                with open(RESULTS_JSONL, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(result) + '\n')
        else:
            with open(RESULTS_JSONL, 'a', encoding='utf-8') as f:
                f.write(json.dumps(result) + '\n')
    except Exception as e:
        print('ERROR writing result:', e)

    # quick flush summary every 10 runs
    if i % 10 == 0:
        try:
            # read results and compute top by return
            res = []
            with open(RESULTS_JSONL, 'r', encoding='utf-8') as f:
                for ln in f:
                    try:
                        res.append(json.loads(ln))
                    except Exception:
                        continue
            # sort
            res_sorted = [r for r in res if r.get('total_return_pct') is not None]
            res_sorted.sort(key=lambda x: x['total_return_pct'], reverse=True)
            summary = {'runs_done': len(res), 'best': res_sorted[0] if res_sorted else None}
            with open(SUMMARY_JSON, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2)
        except Exception:
            pass

end_ts = time.time()
# final summary
all_res = []
with open(RESULTS_JSONL, 'r', encoding='utf-8') as f:
    for ln in f:
        try:
            all_res.append(json.loads(ln))
        except Exception:
            continue

best = None
valid = [r for r in all_res if r.get('total_return_pct') is not None]
if valid:
    valid.sort(key=lambda x: x['total_return_pct'], reverse=True)
    best = valid[0]

final = {
    'started_at': datetime.utcfromtimestamp(start_ts).isoformat(),
    'finished_at': datetime.utcnow().isoformat(),
    'duration_sec': round(end_ts - start_ts, 2),
    'total_runs': TOTAL,
    'results_count': len(all_res),
    'best': best
}
with open(SUMMARY_JSON, 'w', encoding='utf-8') as f:
    json.dump(final, f, indent=2)

print('Done. Summary written to', SUMMARY_JSON)
print(json.dumps(final, indent=2))
