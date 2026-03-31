#!/usr/bin/env python3
"""
Daytrading backtest: 15-minute EMA crossover + RSI + Volume confirmation.
Uses data/daytrading_15m/ collected by scripts/collect_15m_daytrading.py

Signal logic:
  BUY  when: EMA9 crosses above EMA21 AND RSI(14) > 45 AND RSI not overbought (<70)
  SELL when: EMA9 crosses below EMA21 OR RSI(14) > 72 (take profit on overbought)

Exit logic (per position):
  - TP: +1.8% (ATR-adjustable)
  - SL: -1.2%
  - Force-close: 8h max hold

Usage:
    # First collect data:
    python3 scripts/collect_15m_daytrading.py --days 90

    # Then backtest:
    python3 scripts/backtest_daytrading_15m.py --days 90 --initial 200
    python3 scripts/backtest_daytrading_15m.py --days 90 --initial 200 --tp 2.0 --sl 1.0

    # Parameter sweep:
    python3 scripts/backtest_daytrading_15m.py --sweep
"""
import argparse
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

PAIRS = ["XETHZEUR", "SOLEUR", "ADAEUR", "XXRPZEUR", "LINKEUR"]
DATA_DIR = Path("data/daytrading_15m")
INTERVAL_MIN = 15
CANDLE_SEC = INTERVAL_MIN * 60

# Default signal params
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
ATR_PERIOD = 14


# ── Indicators ────────────────────────────────────────────────────────────────

def calc_ema(prices: List[float], period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    k = 2.0 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema


def calc_rsi(prices: List[float], period: int = RSI_PERIOD) -> Optional[float]:
    if len(prices) < period + 1:
        return None
    arr = np.array(prices)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def calc_atr_pct(prices: List[float], period: int = ATR_PERIOD) -> float:
    """ATR as % of last close price."""
    if len(prices) < period + 1:
        return 1.5  # default fallback
    trs = [abs(prices[i] - prices[i - 1]) for i in range(-period, 0)]
    atr = sum(trs) / len(trs)
    last = prices[-1]
    return (atr / last * 100) if last > 0 else 1.5


# ── Signal ────────────────────────────────────────────────────────────────────

@dataclass
class DaytradeSignal:
    action: str = "HOLD"   # BUY / SELL / HOLD
    score: float = 0.0
    ema_fast: Optional[float] = None
    ema_slow: Optional[float] = None
    rsi: Optional[float] = None


def generate_signal(prices: List[float], prev_prices: List[float]) -> DaytradeSignal:
    """EMA9/21 crossover + RSI confirmation on 15m closes."""
    if len(prices) < EMA_SLOW + 2:
        return DaytradeSignal()

    ema_fast_now  = calc_ema(prices, EMA_FAST)
    ema_slow_now  = calc_ema(prices, EMA_SLOW)
    ema_fast_prev = calc_ema(prices[:-1], EMA_FAST)
    ema_slow_prev = calc_ema(prices[:-1], EMA_SLOW)
    rsi = calc_rsi(prices, RSI_PERIOD)

    if None in (ema_fast_now, ema_slow_now, ema_fast_prev, ema_slow_prev, rsi):
        return DaytradeSignal(ema_fast=ema_fast_now, ema_slow=ema_slow_now, rsi=rsi)

    bullish_cross = (ema_fast_prev <= ema_slow_prev) and (ema_fast_now > ema_slow_now)
    bearish_cross = (ema_fast_prev >= ema_slow_prev) and (ema_fast_now < ema_slow_now)

    # Score: gap between EMAs as % of price (momentum strength)
    gap_pct = abs(ema_fast_now - ema_slow_now) / ema_slow_now * 100 if ema_slow_now else 0
    score = min(30.0, gap_pct * 10)

    action = "HOLD"
    if bullish_cross and 40 < rsi < 72:
        action = "BUY"
    elif bearish_cross and rsi > 50:
        action = "SELL"
    elif rsi > 75:  # overbought exit trigger even without crossover
        action = "SELL"
    elif rsi < 25:  # oversold — don't short, flag as potential reversal coming
        action = "HOLD"

    signed_score = score if action == "BUY" else (-score if action == "SELL" else 0.0)
    return DaytradeSignal(action=action, score=signed_score,
                          ema_fast=ema_fast_now, ema_slow=ema_slow_now, rsi=rsi)


# ── Data loader ───────────────────────────────────────────────────────────────

def load_15m_data(pair: str, since_ts: int, end_ts: int) -> Dict[int, float]:
    candidates = sorted(DATA_DIR.glob(f"{pair}_*_15m.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return {}
    raw = {int(k): float(v) for k, v in json.loads(candidates[0].read_text()).items()}
    return {k: v for k, v in raw.items() if since_ts <= k <= end_ts}


# ── Backtest ──────────────────────────────────────────────────────────────────

@dataclass
class Position:
    side: int = 0        # 1 long, -1 short (shorts disabled for now)
    qty: float = 0.0
    entry_price: float = 0.0
    entry_ts: int = 0


def run_backtest(
    days: int,
    initial_eur: float,
    fee_rate: float,
    slippage_bps: float,
    tp_pct: float = 1.8,
    sl_pct: float = 1.2,
    max_hold_h: float = 8.0,
    use_atr_tp: bool = True,
    atr_tp_mult: float = 1.2,
    cooldown_min: int = 30,
    max_positions: int = 2,
) -> dict:
    end_ts = int(datetime.now(timezone.utc).timestamp())
    since_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

    # Load data
    series: Dict[str, Dict[int, float]] = {}
    for p in PAIRS:
        d = load_15m_data(p, since_ts, end_ts)
        if d:
            series[p] = d

    available_pairs = list(series.keys())
    if not available_pairs:
        return {"error": "No 15m data found. Run: python3 scripts/collect_15m_daytrading.py --days 90"}

    all_ts = sorted(set().union(*[set(v.keys()) for v in series.values()]))

    hist: Dict[str, deque] = {p: deque(maxlen=EMA_SLOW + 20) for p in available_pairs}
    pos: Dict[str, Position] = {p: Position() for p in available_pairs}

    cash = initial_eur
    slip = slippage_bps / 10000.0
    last_trade_ts = 0
    cooldown_sec = cooldown_min * 60
    closed: List[dict] = []
    consecutive_losses = 0
    pause_until = 0
    peak_eq = initial_eur
    max_dd = 0.0

    def equity() -> float:
        eq = cash
        for p in available_pairs:
            if pos[p].side == 1:
                px = series[p].get(all_ts[-1], pos[p].entry_price)
                eq += pos[p].qty * px
        return eq

    prev_prices: Dict[str, List[float]] = {p: [] for p in available_pairs}

    for ts in all_ts:
        if ts < pause_until:
            continue

        price: Dict[str, float] = {}
        for p in available_pairs:
            px = series[p].get(ts)
            if px is not None:
                hist[p].append(px)
                price[p] = px

        # Update peak/drawdown
        eq_now = cash + sum(
            pos[p].qty * price.get(p, pos[p].entry_price)
            for p in available_pairs if pos[p].side == 1
        )
        peak_eq = max(peak_eq, eq_now)
        if peak_eq > 0:
            max_dd = max(max_dd, (peak_eq - eq_now) / peak_eq * 100)

        # --- EXIT PASS ---
        for p in available_pairs:
            position = pos[p]
            px = price.get(p, 0.0)
            if position.side == 0 or px <= 0:
                continue

            held_h = (ts - position.entry_ts) / 3600.0
            pnl_pct = (px - position.entry_price) / position.entry_price * 100

            # Dynamic TP
            h = list(hist[p])
            atr_pct_val = calc_atr_pct(h) if use_atr_tp else tp_pct
            effective_tp = min(5.0, max(tp_pct, atr_tp_mult * atr_pct_val))

            hit_tp = pnl_pct >= effective_tp
            hit_sl = pnl_pct <= -sl_pct
            hit_time = held_h >= max_hold_h

            if hit_tp or hit_sl or hit_time:
                exit_px = px * (1 - slip)
                pnl_eur = (exit_px - position.entry_price) * position.qty
                fee = exit_px * position.qty * fee_rate
                pnl_eur -= fee
                cash += position.qty * exit_px - fee

                reason = "TP" if hit_tp else ("SL" if hit_sl else "TIME")
                closed.append({"pair": p, "pnl_eur": pnl_eur, "reason": reason,
                               "held_h": round(held_h, 1)})

                if pnl_eur < 0:
                    consecutive_losses += 1
                    if consecutive_losses >= 3:
                        pause_until = max(pause_until, ts + 2 * 3600)
                else:
                    consecutive_losses = 0
                pos[p] = Position()

        # --- ENTRY PASS ---
        if ts - last_trade_ts < cooldown_sec:
            continue

        open_count = sum(1 for p in available_pairs if pos[p].side != 0)
        if open_count >= max_positions:
            continue

        # Score all candidates
        candidates = []
        for p in available_pairs:
            if pos[p].side != 0:
                continue
            px = price.get(p, 0.0)
            if px <= 0:
                continue
            h = list(hist[p])
            sig = generate_signal(h, prev_prices[p])
            if sig.action == "BUY" and sig.score > 0:
                candidates.append((sig.score, p, px, sig))

        if not candidates:
            for p in available_pairs:
                if list(hist[p]):
                    prev_prices[p] = list(hist[p])
            continue

        candidates.sort(reverse=True)
        _, bp, px, sig = candidates[0]

        allocation = min(cash * 0.4, cash / max(1, max_positions - open_count))
        if allocation < 8.0:
            for p in available_pairs:
                if list(hist[p]):
                    prev_prices[p] = list(hist[p])
            continue

        entry_px = px * (1 + slip)
        fee_in = entry_px * (allocation / entry_px) * fee_rate
        qty = (allocation - fee_in) / entry_px
        cash -= allocation

        pos[bp] = Position(side=1, qty=qty, entry_price=entry_px, entry_ts=ts)
        last_trade_ts = ts

        for p in available_pairs:
            if list(hist[p]):
                prev_prices[p] = list(hist[p])

    # Final equity: close any open positions at last known price
    final_cash = cash
    for p in available_pairs:
        if pos[p].side == 1:
            last_px = series[p].get(all_ts[-1], pos[p].entry_price) if all_ts else pos[p].entry_price
            exit_px = last_px * (1 - slip)
            pnl = (exit_px - pos[p].entry_price) * pos[p].qty - (exit_px * pos[p].qty * fee_rate)
            final_cash += pos[p].qty * exit_px - (exit_px * pos[p].qty * fee_rate)
            closed.append({"pair": p, "pnl_eur": pnl, "reason": "EOD_CLOSE", "held_h": 0})

    wins = [t for t in closed if t["pnl_eur"] >= 0]
    losses = [t for t in closed if t["pnl_eur"] < 0]
    by_pair = {}
    for t in closed:
        by_pair.setdefault(t["pair"], 0.0)
        by_pair[t["pair"]] += t["pnl_eur"]
    by_reason = {}
    for t in closed:
        by_reason.setdefault(t["reason"], 0)
        by_reason[t["reason"]] += 1

    return {
        "period_days": days,
        "initial_eur": initial_eur,
        "final_eur": round(final_cash, 2),
        "return_pct": round((final_cash - initial_eur) / initial_eur * 100, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
        "net_pnl_eur": round(sum(t["pnl_eur"] for t in closed), 2),
        "by_pair_pnl": {k: round(v, 2) for k, v in sorted(by_pair.items())},
        "exit_reasons": by_reason,
        "params": {
            "tp_pct": tp_pct, "sl_pct": sl_pct, "max_hold_h": max_hold_h,
            "atr_tp_mult": atr_tp_mult, "cooldown_min": cooldown_min,
            "ema_fast": EMA_FAST, "ema_slow": EMA_SLOW, "rsi_period": RSI_PERIOD,
            "available_pairs": available_pairs,
        },
        "assumptions": {"fee_rate": fee_rate, "slippage_bps": slippage_bps, "mode": "daytrading-15m-ema"},
    }


def sweep(days: int, initial: float, fee: float, slip: float) -> None:
    configs = [
        dict(tp_pct=1.5, sl_pct=0.8, max_hold_h=4,  cooldown_min=20),
        dict(tp_pct=1.8, sl_pct=1.0, max_hold_h=6,  cooldown_min=30),
        dict(tp_pct=2.0, sl_pct=1.2, max_hold_h=8,  cooldown_min=30),
        dict(tp_pct=2.5, sl_pct=1.5, max_hold_h=12, cooldown_min=45),
        dict(tp_pct=3.0, sl_pct=1.5, max_hold_h=16, cooldown_min=60),
    ]
    print(f"{'Config':<40} {'Return':>8} {'WR':>5} {'Trades':>7} {'MaxDD':>7}")
    print("-" * 75)
    for c in configs:
        r = run_backtest(days, initial, fee, slip, **c)
        if "error" in r:
            print(r["error"])
            return
        label = f"TP{c['tp_pct']}%/SL{c['sl_pct']}%/{c['max_hold_h']}h/{c['cooldown_min']}min"
        print(f"{label:<40} {r['return_pct']:>+7.2f}% {r['winrate_pct']:>4.0f}% "
              f"{r['closed_trades']:>6}  {r['max_drawdown_pct']:>6.1f}%")


def main():
    ap = argparse.ArgumentParser(description="15m EMA crossover daytrading backtest")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--initial", type=float, default=200.0)
    ap.add_argument("--fee", type=float, default=0.0026)
    ap.add_argument("--slippage-bps", type=float, default=8.0)
    ap.add_argument("--tp", type=float, default=1.8, help="Take-profit %")
    ap.add_argument("--sl", type=float, default=1.2, help="Stop-loss %")
    ap.add_argument("--max-hold-h", type=float, default=8.0, help="Max hold hours")
    ap.add_argument("--cooldown", type=int, default=30, help="Entry cooldown minutes")
    ap.add_argument("--sweep", action="store_true", help="Run parameter sweep")
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    if args.sweep:
        sweep(args.days, args.initial, args.fee, args.slippage_bps)
        return

    result = run_backtest(
        args.days, args.initial, args.fee, args.slippage_bps,
        tp_pct=args.tp, sl_pct=args.sl,
        max_hold_h=args.max_hold_h, cooldown_min=args.cooldown,
    )
    print(json.dumps(result, indent=2))

    if args.out:
        from pathlib import Path
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
