#!/usr/bin/env python3
"""Parameter sweep over backtest_v3_detailed — finds best RSI/TP/ATR config.

Monkey-patches global signal/risk constants to test multiple configurations.
Outputs sorted ranking and writes best params to stdout as TOML snippet.

Usage:
    python3 scripts/sweep_v3.py [--days 365] [--initial 500] [--quick]
    --quick  : smaller grid (27 combos) for fast iteration
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest_v3_detailed as bt

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=365)
ap.add_argument("--initial", type=float, default=500.0)
ap.add_argument("--fee", type=float, default=0.0026)
ap.add_argument("--slippage-bps", type=float, default=8.0)
ap.add_argument("--quick", action="store_true", help="Smaller 27-combo grid")
ap.add_argument("--out", type=str, default="reports/sweep_v3_results.json")
args = ap.parse_args()

Path("reports").mkdir(exist_ok=True)

# Parameter grids
if args.quick:
    oversold_vals = [28, 33, 38]  # RSI buy threshold
    overbought_vals = [62, 67, 72]  # RSI sell threshold
    base_tp_vals = [2.0, 3.0, 4.5]  # base TP%
    atr_mult_vals = [1.5, 2.0, 2.5]  # ATR TP multiplier
    min_score_vals = [8.0]  # min buy score
else:
    oversold_vals = [25, 28, 30, 33, 36]
    overbought_vals = [62, 64, 67, 70, 74]
    base_tp_vals = [1.5, 2.0, 2.5, 3.0, 4.0]
    atr_mult_vals = [1.5, 2.0, 2.5, 3.0]
    min_score_vals = [6.0, 8.0, 10.0]

combos = list(itertools.product(oversold_vals, overbought_vals, base_tp_vals, atr_mult_vals, min_score_vals))
print(f"Running {len(combos)} combinations × {args.days}d backtest (initial={args.initial}€)")
print("=" * 70)

results = []
t0 = time.time()
for i, (oversold, overbought, base_tp, atr_mult, min_score) in enumerate(combos):
    # patch globals
    bt._MR_OVERSOLD = oversold
    bt._MR_OVERBOUGHT = overbought
    bt._BASE_TP_PCT = base_tp
    bt._ATR_TP_MULT = atr_mult
    bt._MIN_BUY_SCORE = min_score

    try:
        r = bt.run_backtest(
            days=args.days,
            initial_eur=args.initial,
            fee_rate=args.fee,
            slippage_bps=args.slippage_bps,
        )
    except Exception as e:
        print(f"  [{i+1}/{len(combos)}] ERROR: {e}")
        continue

    ret_pct = r["return_pct"]
    sharpe = r["metrics"].get("sharpe") or 0.0
    wr = r["winrate_pct"]
    trades = r["closed_trades"]
    dd = r["max_drawdown_pct"]
    # composite score: return weighted by sharpe, penalise deep drawdowns
    composite = ret_pct * (1 + max(0.0, sharpe) * 0.5) - (dd * 0.3)

    row = {
        "oversold": oversold,
        "overbought": overbought,
        "base_tp": base_tp,
        "atr_mult": atr_mult,
        "min_score": min_score,
        "return_pct": ret_pct,
        "sharpe": sharpe,
        "winrate_pct": wr,
        "trades": trades,
        "max_dd_pct": dd,
        "composite": round(composite, 3),
    }
    results.append(row)

    elapsed = time.time() - t0
    eta = (elapsed / (i + 1)) * (len(combos) - i - 1)
    print(
        f"  [{i+1:3d}/{len(combos)}] os={oversold} ob={overbought} tp={base_tp} "
        f"atr={atr_mult} sc={min_score} → {ret_pct:+.2f}% WR={wr:.0f}% "
        f"DD={dd:.1f}% sharpe={sharpe:.3f} | ETA {int(eta)}s"
    )

results.sort(key=lambda x: x["composite"], reverse=True)

print("\n" + "=" * 70)
print("TOP 10 CONFIGURATIONS:")
print("=" * 70)
for rank, r in enumerate(results[:10], 1):
    print(
        f"#{rank:2d}  os={r['oversold']:2d} ob={r['overbought']:2d} "
        f"tp={r['base_tp']} atr={r['atr_mult']} sc={r['min_score']}  "
        f"→ {r['return_pct']:+.2f}% WR={r['winrate_pct']:.0f}% "
        f"DD={r['max_dd_pct']:.1f}% sharpe={r['sharpe']:.3f} composite={r['composite']}"
    )

if results:
    best = results[0]
    print("\n" + "=" * 70)
    print("BEST CONFIG — paste into config.toml [risk_management]:")
    print("=" * 70)
    print(f'mr_rsi_oversold_threshold   = {best["oversold"]}')
    print(f'mr_rsi_overbought_threshold = {best["overbought"]}')
    print(f'take_profit_percent         = {best["base_tp"]}')
    print(f'atr_tp_multiplier           = {best["atr_mult"]}')
    print(f'min_buy_score               = {best["min_score"]}')

out_path = Path(args.out)
out_path.parent.mkdir(exist_ok=True)
out_path.write_text(json.dumps(results, indent=2))
print(f"\nFull results → {out_path}")
