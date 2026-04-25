#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import requests

PAIRS = ["XXBTZEUR", "XETHZEUR", "SOLEUR", "ADAEUR", "DOTEUR", "XXRPZEUR", "LINKEUR"]
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import nas_paths as _nas_paths

_NAS = _nas_paths()
CACHE_DIR = _NAS["bot_cache"] / "mentor_cache_1h"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Position:
    side: int = 0
    qty: float = 0.0
    entry_price: float = 0.0
    entry_ts: int = 0
    tag: str = ""


@dataclass
class Variant:
    name: str
    score_gate: float = 0.0
    allow_long: bool = True
    allow_short: bool = True
    allow_mr: bool = True
    allow_trend: bool = True
    cooldown_sec: int = 3600
    risk_off_scale: float = 0.60
    alloc_pct: float = 0.18
    alloc_cap: float = 40.0


def fetch_ohlc_1h(pair: str, start_ts: int, end_ts: int) -> Dict[int, float]:
    out_path = CACHE_DIR / f"{pair}_{start_ts}_{end_ts}.json"
    if out_path.exists():
        return {int(k): float(v) for k, v in json.loads(out_path.read_text()).items()}

    out: Dict[int, float] = {}
    since = start_ts
    sess = requests.Session()

    while since < end_ts:
        for attempt in range(10):
            j = sess.get(
                "https://api.kraken.com/0/public/OHLC",
                params={"pair": pair, "interval": 60, "since": since},
                timeout=30,
            ).json()
            errs = j.get("error") or []
            if errs and any("Too many requests" in e for e in errs):
                time.sleep(1.2 + attempt * 1.2)
                continue
            if errs:
                raise RuntimeError(f"{pair}: {errs}")
            break
        else:
            raise RuntimeError(f"{pair}: rate-limit loop")

        key = [k for k in j["result"].keys() if k != "last"][0]
        rows = j["result"][key]
        if not rows:
            break

        last_ts = since
        for row in rows:
            ts = int(row[0])
            if start_ts <= ts <= end_ts:
                out[ts] = float(row[4])
            last_ts = max(last_ts, ts)

        nxt = int(j["result"].get("last", last_ts + 1))
        since = nxt if nxt > since else last_ts + 1
        time.sleep(0.35)

    out_path.write_text(json.dumps(out))
    return out


def calc_rsi(prices: List[float], period: int = 14):
    if len(prices) < period + 1:
        return None
    arr = np.array(prices)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 0.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def strategy_signal(prices: List[float], v: Variant):
    if len(prices) < 50:
        return "HOLD", 0.0
    rsi = calc_rsi(prices, 14)
    if rsi is None:
        return "HOLD", 0.0

    sma20 = float(np.mean(prices[-20:]))
    sma50 = float(np.mean(prices[-50:]))
    recent = np.array(prices[-20:])
    vol_pct = float(np.std(recent) / np.mean(recent) * 100) if np.mean(recent) > 0 else 0.0
    if vol_pct < 0.15:
        return "HOLD", 0.0

    rsi_score = 0.0
    if rsi < 30:
        rsi_score = (30 - rsi) / 30 * 50
    elif rsi > 70:
        rsi_score = -((rsi - 70) / 30 * 50)

    sma_score = max(-50.0, min(50.0, (((sma20 - sma50) / sma50) * 100) * 10))
    total = rsi_score + sma_score
    ratio = (sma20 - sma50) / sma50

    if v.allow_mr:
        if rsi < 33 and ratio > -0.003:
            return "BUY", total
        if rsi > 67 and ratio < 0.003:
            return "SELL", total
    if v.allow_trend:
        if ratio > 0.006 and 45 <= rsi <= 68:
            return "BUY", total + 8
        if ratio < -0.006 and 32 <= rsi <= 55:
            return "SELL", total - 8
    return "HOLD", total


def run_variant(series: Dict[str, Dict[int, float]], days: int, v: Variant):
    all_ts = sorted(set().union(*[set(vv.keys()) for vv in series.values()]))
    hist = {p: deque(maxlen=100) for p in PAIRS}
    signal = {p: "HOLD" for p in PAIRS}
    score = {p: 0.0 for p in PAIRS}
    price = {p: 0.0 for p in PAIRS}
    pos = {p: Position() for p in PAIRS}

    cash = 200.0
    last_trade_ts = 0
    losses_in_row = 0
    pause_until = 0
    closed = []
    peak = 200.0
    max_dd = 0.0

    fee_rate = 0.0026
    slippage_bps = 8.0

    def equity() -> float:
        eq = cash
        for p in PAIRS:
            px = price.get(p, 0.0)
            position = pos[p]
            if position.side == 1:
                eq += position.qty * px
            elif position.side == -1:
                eq += (position.entry_price - px) * position.qty
        return eq

    for ts in all_ts:
        for p in PAIRS:
            px = series[p].get(ts)
            if px is None:
                continue
            price[p] = px
            hist[p].append(px)
            s, sc = strategy_signal(list(hist[p]), v)
            signal[p] = s
            score[p] = sc

        benchmark_score = score.get("XXBTZEUR", 0.0)
        risk_on = benchmark_score >= -12.0

        for p in PAIRS:
            position = pos[p]
            px = price.get(p, 0.0)
            if position.side == 0 or px <= 0:
                continue
            held_hours = (ts - position.entry_ts) / 3600 if position.entry_ts else 0
            pnl_pct = (
                ((px - position.entry_price) / position.entry_price) * 100
                if position.side == 1
                else ((position.entry_price - px) / position.entry_price) * 100
            )

            tp = 1.2 if position.tag == "scalp" else 6.0
            sl = -0.8 if position.tag == "scalp" else -3.0
            max_hold_h = 6 if position.tag == "scalp" else 48

            if pnl_pct >= tp or pnl_pct <= sl or held_hours >= max_hold_h:
                slip = slippage_bps / 10000.0
                if position.side == 1:
                    exit_px = px * (1 - slip)
                    gross = position.qty * exit_px
                    fee = gross * fee_rate
                    pnl_eur = (exit_px - position.entry_price) * position.qty - fee
                    cash += gross - fee
                else:
                    exit_px = px * (1 + slip)
                    notional = position.qty * position.entry_price
                    pnl_eur = (position.entry_price - exit_px) * position.qty
                    fee = (position.qty * exit_px) * fee_rate
                    cash += notional + pnl_eur - fee

                closed.append({"pair": p, "side": position.side, "pnl_eur": pnl_eur})
                if pnl_eur < 0:
                    losses_in_row += 1
                    if losses_in_row >= 3:
                        pause_until = max(pause_until, ts + 180 * 60)
                else:
                    losses_in_row = 0
                pos[p] = Position()

        if ts - last_trade_ts < v.cooldown_sec:
            eq = equity()
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0)
            continue
        if ts < pause_until:
            eq = equity()
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0)
            continue

        cands = [
            (abs(score[p]), p)
            for p in PAIRS
            if signal[p] in ("BUY", "SELL") and pos[p].side == 0 and abs(score[p]) >= v.score_gate
        ]
        if not cands:
            eq = equity()
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0)
            continue

        _, bp = max(cands)
        s = signal[bp]
        sc = score[bp]
        px = price.get(bp, 0.0)
        if px <= 0:
            eq = equity()
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0)
            continue

        bench_hist = list(hist.get("XXBTZEUR", []))[-20:]
        bench_vol = 0.0
        if len(bench_hist) >= 20:
            mean = float(np.mean(bench_hist))
            bench_vol = float(np.std(bench_hist) / mean * 100) if mean > 0 else 0.0
        vol_scale = 1.0 if bench_vol <= 0 else min(1.25, max(0.35, 1.6 / bench_vol))

        allocation = min(v.alloc_cap, cash * v.alloc_pct) * (1.0 if risk_on else v.risk_off_scale) * vol_scale
        if allocation < 8.0:
            eq = equity()
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0)
            continue

        is_scalp = abs(sc) >= 28
        direction = None
        if s == "BUY" and (risk_on or is_scalp):
            direction = 1
        if s == "SELL" and ((not risk_on) or is_scalp):
            direction = -1

        if direction == 1 and not v.allow_long:
            direction = None
        if direction == -1 and not v.allow_short:
            direction = None
        if direction is None:
            eq = equity()
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0)
            continue

        slip = slippage_bps / 10000.0
        entry_px = px * (1 + slip) if direction == 1 else px * (1 - slip)
        qty = allocation / entry_px

        if direction == 1:
            total = allocation * (1 + fee_rate)
            if total > cash:
                eq = equity()
                peak = max(peak, eq)
                max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0)
                continue
            cash -= total
        else:
            if allocation > cash:
                eq = equity()
                peak = max(peak, eq)
                max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0)
                continue
            cash -= allocation

        pos[bp] = Position(
            side=direction, qty=qty, entry_price=entry_px, entry_ts=ts, tag=("scalp" if is_scalp else "swing")
        )
        last_trade_ts = ts

        eq = equity()
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0)

    for p in PAIRS:
        position = pos[p]
        px = price.get(p, 0.0)
        if position.side == 0 or px <= 0:
            continue
        slip = slippage_bps / 10000.0
        if position.side == 1:
            exit_px = px * (1 - slip)
            gross = position.qty * exit_px
            fee = gross * fee_rate
            cash += gross - fee
        else:
            exit_px = px * (1 + slip)
            notional = position.qty * position.entry_price
            pnl_eur = (position.entry_price - exit_px) * position.qty
            fee = (position.qty * exit_px) * fee_rate
            cash += notional + pnl_eur - fee

    wins = sum(1 for x in closed if x["pnl_eur"] > 0)
    result = {
        "variant": v.name,
        "period_days": days,
        "final_eur": round(cash, 2),
        "return_pct": round((cash - 200.0) / 200.0 * 100, 2),
        "closed_trades": len(closed),
        "winrate_pct": round((wins / len(closed) * 100), 2) if closed else 0.0,
        "max_drawdown_pct": round(max_dd, 2),
    }
    return result


def main():
    days = 365
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    series = {}
    for p in PAIRS:
        data = fetch_ohlc_1h(p, start_ts, end_ts)
        series[p] = data

    variants = [
        Variant(name="main_baseline"),
        Variant(name="trend_bias", allow_mr=False, score_gate=8, risk_off_scale=0.50),
        Variant(name="reversion_bias", allow_trend=False, score_gate=8, cooldown_sec=5400),
        Variant(
            name="grid_safe_long_only",
            allow_short=False,
            score_gate=10,
            cooldown_sec=7200,
            alloc_pct=0.10,
            alloc_cap=20.0,
            risk_off_scale=0.45,
        ),
    ]

    runs = [run_variant(series, days, v) for v in variants]
    base = next(r for r in runs if r["variant"] == "main_baseline")
    for r in runs:
        r["delta_return_vs_main_pct"] = round(r["return_pct"] - base["return_pct"], 2)
        r["delta_dd_vs_main_pct"] = round(r["max_drawdown_pct"] - base["max_drawdown_pct"], 2)

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "period": f"{start.date()}..{end.date()}",
        "notes": {
            "data_source": "Kraken OHLC paginated 1h",
            "fee_rate": 0.0026,
            "slippage_bps": 8.0,
        },
        "results": runs,
    }

    out_path = Path("reports/mentor_beta_review_1y.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
