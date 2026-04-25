#!/usr/bin/env python3
"""
Daytrading backtest: RSI Mean-Reversion on 15m or 1h data.

Same logic as the swing bot (RSI extremes → reversion) but:
  - shorter hold times (2-4h on 15m, 12-24h on 1h)
  - tighter TP/SL (0.8-1.5%)
  - uses data/daytrading_15m/ OR data/mentor_cache_1h/ as fallback

Signal:
  BUY  when RSI(14) ≤ buy_rsi (default 25) — oversold
  EXIT when RSI(14) ≥ sell_rsi (default 65) OR TP hit OR SL hit OR time limit

Usage:
    # On existing 1h cache (60 days, no collection needed):
    python3 scripts/backtest_daytrade_rsi_mr.py --use-1h --sweep
    python3 scripts/backtest_daytrade_rsi_mr.py --use-1h --days 60 --initial 200

    # On 15m data (7.5 days available now, grows via cron):
    python3 scripts/backtest_daytrade_rsi_mr.py --days 8 --initial 200 --sweep
    python3 scripts/backtest_daytrade_rsi_mr.py --days 8 --tp 1.2 --sl 0.8 --max-hold-h 3

    # BB+RSI combo (higher precision):
    python3 scripts/backtest_daytrade_rsi_mr.py --use-1h --combo --sweep
"""
import argparse
import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

PAIRS = ["XETHZEUR", "SOLEUR", "ADAEUR", "XXRPZEUR", "LINKEUR"]
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import nas_paths as _nas_paths

_NAS = _nas_paths()
DATA_15M = _NAS["bot_cache"] / "daytrading_15m"
DATA_1H = _NAS["bot_cache"] / "mentor_cache_1h"

RSI_PERIOD = 14
BB_PERIOD = 20
BB_K = 2.0


# ── Indicators ────────────────────────────────────────────────────────────────


def calc_rsi(prices: List[float], period: int = RSI_PERIOD) -> Optional[float]:
    if len(prices) < period + 1:
        return None
    arr = np.array(prices[-period - 1 :])
    d = np.diff(arr)
    gains = np.where(d > 0, d, 0.0)
    losses = np.where(d < 0, -d, 0.0)
    ag = np.mean(gains)
    al = np.mean(losses)
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1 + ag / al)


def calc_atr_pct(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 1.5
    trs = [abs(prices[i] - prices[i - 1]) for i in range(-period, 0)]
    atr = sum(trs) / len(trs)
    last = prices[-1]
    return (atr / last * 100) if last > 0 else 1.5


def near_bb_lower(prices: List[float], period: int = BB_PERIOD, k: float = BB_K, tolerance_pct: float = 1.5) -> bool:
    """True when price is at or below the lower Bollinger Band (within tolerance)."""
    if len(prices) < period:
        return False
    w = np.array(prices[-period:])
    mid = np.mean(w)
    std = np.std(w)
    lower = mid - k * std
    close = prices[-1]
    return close <= lower * (1 + tolerance_pct / 100)


# ── Data loaders ──────────────────────────────────────────────────────────────


def _load_json_files(pattern: str, base: Path) -> Dict[int, float]:
    result: Dict[int, float] = {}
    for f in sorted(base.glob(pattern)):
        result.update({int(k): float(v) for k, v in json.loads(f.read_text()).items()})
    return result


def load_data(pair: str, since_ts: int, end_ts: int, use_1h: bool) -> Dict[int, float]:
    if use_1h:
        raw = _load_json_files(f"{pair}_*_60m.json", DATA_1H)
    else:
        candidates = sorted(DATA_15M.glob(f"{pair}_*_15m.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return {}
        raw = {int(k): float(v) for k, v in json.loads(candidates[0].read_text()).items()}
    return {k: v for k, v in raw.items() if since_ts <= k <= end_ts}


# ── Backtest ──────────────────────────────────────────────────────────────────


@dataclass
class Position:
    qty: float = 0.0
    entry_price: float = 0.0
    entry_ts: int = 0


def run_backtest(
    days: int,
    initial_eur: float,
    fee_rate: float,
    slippage_bps: float,
    buy_rsi: float = 25.0,
    sell_rsi: float = 65.0,
    tp_pct: float = 1.2,
    sl_pct: float = 0.8,
    max_hold_h: float = 3.0,
    use_atr_tp: bool = True,
    atr_tp_mult: float = 0.8,
    cooldown_min: int = 30,
    max_positions: int = 2,
    use_1h: bool = False,
    combo_mode: bool = False,  # require BB lower touch on entry
) -> dict:
    end_ts = int(datetime.now(timezone.utc).timestamp())
    since_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

    candle_sec = 3600 if use_1h else 900  # 15m = 900s
    mode = ("rsi-mr-1h-combo" if combo_mode else "rsi-mr-1h") if use_1h else "rsi-mr-15m"

    # ── Load data ──
    series: Dict[str, Dict[int, float]] = {}
    for p in PAIRS:
        d = load_data(p, since_ts, end_ts, use_1h)
        if d:
            series[p] = d

    available = list(series.keys())
    if not available:
        err = (
            "No 1h cache found. Expected data/mentor_cache_1h/*_60m.json"
            if use_1h
            else "No 15m data. Run: python3 scripts/collect_15m_daytrading.py --days 14"
        )
        return {"error": err}

    all_ts = sorted(set().union(*[set(v.keys()) for v in series.values()]))
    warmup = RSI_PERIOD + 2

    hist: Dict[str, deque] = {p: deque(maxlen=max(BB_PERIOD, RSI_PERIOD) + 10) for p in available}
    pos: Dict[str, Position] = {p: Position() for p in available}

    cash = initial_eur
    slip = slippage_bps / 10_000.0
    cooldown = cooldown_min * 60
    last_buy = 0
    closed: List[dict] = []
    consec_losses = 0
    pause_until = 0
    peak_eq = initial_eur
    max_dd = 0.0

    for i, ts in enumerate(all_ts):
        if ts < pause_until:
            for p in available:
                px = series[p].get(ts)
                if px is not None:
                    hist[p].append(px)
            continue

        price: Dict[str, float] = {}
        for p in available:
            px = series[p].get(ts)
            if px is not None:
                hist[p].append(px)
                price[p] = px

        # Drawdown tracking
        eq_now = cash + sum(pos[p].qty * price.get(p, pos[p].entry_price) for p in available if pos[p].qty > 0)
        peak_eq = max(peak_eq, eq_now)
        if peak_eq > 0:
            max_dd = max(max_dd, (peak_eq - eq_now) / peak_eq * 100)

        # ── EXIT PASS ──
        for p in available:
            position = pos[p]
            if position.qty == 0:
                continue
            px = price.get(p, 0.0)
            if px <= 0:
                continue

            h = list(hist[p])
            held_h = (ts - position.entry_ts) / 3600.0
            pnl_pct = (px - position.entry_price) / position.entry_price * 100

            # Dynamic TP
            atr_val = calc_atr_pct(h) if use_atr_tp else tp_pct
            effective_tp = min(4.0, max(tp_pct, atr_tp_mult * atr_val))

            rsi = calc_rsi(h)

            hit_tp = pnl_pct >= effective_tp
            hit_sl = pnl_pct <= -sl_pct
            hit_time = held_h >= max_hold_h
            hit_rsi = rsi is not None and rsi >= sell_rsi

            if hit_tp or hit_sl or hit_time or hit_rsi:
                exit_px = px * (1 - slip)
                fee = exit_px * position.qty * fee_rate
                pnl_eur = (exit_px - position.entry_price) * position.qty - fee
                cash += position.qty * exit_px - fee

                reason = "TP" if hit_tp else "RSI_EXIT" if hit_rsi else "SL" if hit_sl else "TIME"
                closed.append(
                    {
                        "pair": p,
                        "pnl_eur": pnl_eur,
                        "reason": reason,
                        "held_h": round(held_h, 1),
                        "exit_rsi": round(rsi or 0, 1),
                    }
                )

                if pnl_eur < 0:
                    consec_losses += 1
                    if consec_losses >= 3:
                        # 2-candle cooldown after 3 consecutive losses
                        pause_until = max(pause_until, ts + 2 * candle_sec * 3)
                else:
                    consec_losses = 0

                pos[p] = Position()

        # ── ENTRY PASS ──
        if i < warmup or ts - last_buy < cooldown:
            continue

        open_count = sum(1 for p in available if pos[p].qty > 0)
        if open_count >= max_positions:
            continue

        # Score all candidates by RSI depth (lower = more oversold = higher priority)
        candidates: List[Tuple[float, str, float]] = []
        for p in available:
            if pos[p].qty > 0:
                continue
            px = price.get(p, 0.0)
            if px <= 0:
                continue
            h = list(hist[p])
            rsi = calc_rsi(h)
            if rsi is None or rsi > buy_rsi:
                continue
            if combo_mode and not near_bb_lower(h):
                continue
            candidates.append((rsi, p, px))  # lower RSI = better

        if not candidates:
            continue

        # Pick most oversold
        candidates.sort()
        _, bp, px = candidates[0]

        allocation = min(cash * 0.4, cash / max(1, max_positions - open_count))
        if allocation < 5.0:
            continue

        entry_px = px * (1 + slip)
        fee_in = entry_px * (allocation / entry_px) * fee_rate
        qty = (allocation - fee_in) / entry_px
        cash -= allocation

        pos[bp] = Position(qty=qty, entry_price=entry_px, entry_ts=ts)
        last_buy = ts

    # Close any open positions at last price
    for p in available:
        if pos[p].qty > 0:
            last_px = series[p].get(all_ts[-1], pos[p].entry_price)
            exit_px = last_px * (1 - slip)
            fee = exit_px * pos[p].qty * fee_rate
            pnl_eur = (exit_px - pos[p].entry_price) * pos[p].qty - fee
            cash += pos[p].qty * exit_px - fee
            closed.append({"pair": p, "pnl_eur": pnl_eur, "reason": "EOD", "held_h": 0, "exit_rsi": 0})

    wins = [t for t in closed if t["pnl_eur"] >= 0]
    losses = [t for t in closed if t["pnl_eur"] < 0]

    by_pair = {}
    by_reason = {}
    for t in closed:
        by_pair.setdefault(t["pair"], 0.0)
        by_pair[t["pair"]] += t["pnl_eur"]
        by_reason.setdefault(t["reason"], 0)
        by_reason[t["reason"]] += 1

    return {
        "mode": mode,
        "period_days": days,
        "initial_eur": initial_eur,
        "final_eur": round(cash, 2),
        "return_pct": round((cash - initial_eur) / initial_eur * 100, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
        "net_pnl_eur": round(sum(t["pnl_eur"] for t in closed), 2),
        "by_pair_pnl": {k: round(v, 2) for k, v in sorted(by_pair.items())},
        "exit_reasons": by_reason,
        "params": {
            "buy_rsi": buy_rsi,
            "sell_rsi": sell_rsi,
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "max_hold_h": max_hold_h,
            "cooldown_min": cooldown_min,
            "atr_tp_mult": atr_tp_mult,
            "combo_mode": combo_mode,
            "available_pairs": available,
        },
        "assumptions": {"fee_rate": fee_rate, "slippage_bps": slippage_bps},
    }


# ── Sweep ─────────────────────────────────────────────────────────────────────


def sweep(days: int, initial: float, fee: float, slip: float, use_1h: bool, combo: bool) -> None:
    if use_1h:
        configs = [
            dict(buy_rsi=25, sell_rsi=60, tp_pct=1.5, sl_pct=1.0, max_hold_h=12, cooldown_min=120),
            dict(buy_rsi=25, sell_rsi=65, tp_pct=2.0, sl_pct=1.0, max_hold_h=18, cooldown_min=180),
            dict(buy_rsi=20, sell_rsi=60, tp_pct=2.5, sl_pct=1.2, max_hold_h=24, cooldown_min=240),
            dict(buy_rsi=20, sell_rsi=65, tp_pct=3.0, sl_pct=1.5, max_hold_h=36, cooldown_min=360),
            dict(buy_rsi=30, sell_rsi=60, tp_pct=1.2, sl_pct=0.8, max_hold_h=8, cooldown_min=60),
        ]
    else:
        configs = [
            dict(buy_rsi=30, sell_rsi=60, tp_pct=0.8, sl_pct=0.6, max_hold_h=2, cooldown_min=15),
            dict(buy_rsi=25, sell_rsi=60, tp_pct=1.0, sl_pct=0.7, max_hold_h=3, cooldown_min=20),
            dict(buy_rsi=25, sell_rsi=65, tp_pct=1.2, sl_pct=0.8, max_hold_h=4, cooldown_min=30),
            dict(buy_rsi=20, sell_rsi=65, tp_pct=1.5, sl_pct=1.0, max_hold_h=6, cooldown_min=45),
            dict(buy_rsi=20, sell_rsi=70, tp_pct=2.0, sl_pct=1.2, max_hold_h=8, cooldown_min=60),
        ]

    mode_str = ("1h" if use_1h else "15m") + (" + BB combo" if combo else "")
    print(f"\n{'─'*80}")
    print(f"  RSI Mean-Reversion sweep — {mode_str} mode, {days}d, €{initial:.0f}")
    print(f"{'─'*80}")
    print(f"{'Config':<48} {'Return':>8} {'WR':>5} {'Trades':>7} {'MaxDD':>7} {'Reasons'}")
    print("─" * 80)
    for c in configs:
        r = run_backtest(days, initial, fee, slip, use_1h=use_1h, combo_mode=combo, **c)
        if "error" in r:
            print(r["error"])
            return
        reasons = " ".join(f"{k}:{v}" for k, v in r["exit_reasons"].items())
        label = f"RSI≤{c['buy_rsi']}→{c['sell_rsi']} TP{c['tp_pct']}% SL{c['sl_pct']}% {c['max_hold_h']}h"
        print(
            f"{label:<48} {r['return_pct']:>+7.2f}% {r['winrate_pct']:>4.0f}% "
            f"{r['closed_trades']:>6}  {r['max_drawdown_pct']:>6.1f}%  {reasons}"
        )


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description="RSI Mean-Reversion daytrading backtest (15m or 1h)")
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--initial", type=float, default=200.0)
    ap.add_argument("--fee", type=float, default=0.0026)
    ap.add_argument("--slippage-bps", type=float, default=8.0)
    ap.add_argument("--buy-rsi", type=float, default=25.0, help="RSI entry threshold (buy when below)")
    ap.add_argument("--sell-rsi", type=float, default=65.0, help="RSI exit threshold")
    ap.add_argument("--tp", type=float, default=1.2, help="Take-profit %%")
    ap.add_argument("--sl", type=float, default=0.8, help="Stop-loss %%")
    ap.add_argument("--max-hold-h", type=float, default=3.0, help="Max hold hours")
    ap.add_argument("--cooldown", type=int, default=30, help="Entry cooldown minutes")
    ap.add_argument("--use-1h", action="store_true", help="Use 1h cache data (60d available)")
    ap.add_argument("--combo", action="store_true", help="Require BB lower touch on entry")
    ap.add_argument("--sweep", action="store_true", help="Parameter sweep")
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    # Widen defaults in 1h mode
    if args.use_1h:
        if args.days == 8:
            args.days = 60
        if args.max_hold_h == 3.0:
            args.max_hold_h = 18.0
        if args.tp == 1.2:
            args.tp = 2.0
        if args.sl == 0.8:
            args.sl = 1.0
        if args.cooldown == 30:
            args.cooldown = 180

    if args.sweep:
        sweep(args.days, args.initial, args.fee, args.slippage_bps, use_1h=args.use_1h, combo=args.combo)
        return

    result = run_backtest(
        args.days,
        args.initial,
        args.fee,
        args.slippage_bps,
        buy_rsi=args.buy_rsi,
        sell_rsi=args.sell_rsi,
        tp_pct=args.tp,
        sl_pct=args.sl,
        max_hold_h=args.max_hold_h,
        cooldown_min=args.cooldown,
        use_1h=args.use_1h,
        combo_mode=args.combo,
    )

    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
