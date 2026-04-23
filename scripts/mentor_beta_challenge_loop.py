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
    regime_gate: float = -12.0
    scalp_trigger: float = 28.0


@dataclass
class Stress:
    name: str
    fee_rate: float
    slippage_bps: float


def fetch_ohlc_1h(pair: str, start_ts: int, end_ts: int) -> Dict[int, float]:
    p = CACHE_DIR / f"{pair}_{start_ts}_{end_ts}_60m.json"
    if p.exists():
        return {int(k): float(v) for k, v in json.loads(p.read_text()).items()}
    out: Dict[int, float] = {}
    sess = requests.Session()
    since = start_ts
    while since < end_ts:
        for attempt in range(10):
            j = sess.get(
                "https://api.kraken.com/0/public/OHLC",
                params={"pair": pair, "interval": 60, "since": since},
                timeout=30,
            ).json()
            errs = j.get("error") or []
            if errs and any("Too many requests" in e for e in errs):
                time.sleep(1.1 + attempt * 1.2)
                continue
            if errs:
                raise RuntimeError(f"{pair}: {errs}")
            break
        else:
            raise RuntimeError(f"{pair}: rate-limit")
        key = [k for k in j["result"].keys() if k != "last"][0]
        rows = j["result"][key]
        if not rows:
            break
        last = since
        for r in rows:
            ts = int(r[0])
            if start_ts <= ts <= end_ts:
                out[ts] = float(r[4])
            if ts > last:
                last = ts
        nxt = int(j["result"].get("last", last + 1))
        since = nxt if nxt > since else last + 1
        time.sleep(0.35)
    p.write_text(json.dumps(out))
    return out


def calc_rsi(prices: List[float], period: int = 14):
    if len(prices) < period + 1:
        return None
    arr = np.array(prices)
    d = np.diff(arr)
    g = np.where(d > 0, d, 0)
    l = np.where(d < 0, -d, 0)
    ag, al = np.mean(g[-period:]), np.mean(l[-period:])
    if al == 0:
        return 100.0 if ag > 0 else 0.0
    rs = ag / al
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


def regime_label(hist_btc: deque):
    xs = list(hist_btc)
    if len(xs) < 220:
        return "warmup"
    sma50 = float(np.mean(xs[-50:]))
    sma200 = float(np.mean(xs[-200:]))
    slope20 = (xs[-1] - xs[-20]) / xs[-20] * 100 if xs[-20] > 0 else 0.0
    if sma50 > sma200 * 1.01 and slope20 > 1.0:
        return "bull"
    if sma50 < sma200 * 0.99 and slope20 < -1.0:
        return "bear"
    return "chop"


def run_variant(series: Dict[str, Dict[int, float]], timeline: List[int], v: Variant, stress: Stress):
    hist = {p: deque(maxlen=300) for p in PAIRS}
    sig = {p: "HOLD" for p in PAIRS}
    score = {p: 0.0 for p in PAIRS}
    px = {p: 0.0 for p in PAIRS}
    pos = {p: {"side": 0, "qty": 0.0, "entry": 0.0, "ts": 0, "tag": "", "regime": ""} for p in PAIRS}
    cash = 200.0
    peak = 200.0
    max_dd = 0.0
    last_trade = 0
    losses = 0
    pause_until = 0
    closed = []

    def equity():
        e = cash
        for p in PAIRS:
            po = pos[p]
            if po["side"] == 1:
                e += po["qty"] * px[p]
            elif po["side"] == -1:
                e += (po["entry"] - px[p]) * po["qty"]
        return e

    for ts in timeline:
        for p in PAIRS:
            x = series[p].get(ts)
            if x is None:
                continue
            px[p] = x
            hist[p].append(x)
            s, sc = strategy_signal(list(hist[p]), v)
            sig[p], score[p] = s, sc

        risk_on = score.get("XXBTZEUR", 0.0) >= v.regime_gate
        regime_now = regime_label(hist["XXBTZEUR"])

        for p in PAIRS:
            po = pos[p]
            if po["side"] == 0 or px[p] <= 0:
                continue
            held_h = (ts - po["ts"]) / 3600 if po["ts"] else 0
            pnl_pct = (
                ((px[p] - po["entry"]) / po["entry"]) * 100
                if po["side"] == 1
                else ((po["entry"] - px[p]) / po["entry"]) * 100
            )
            tp = 1.2 if po["tag"] == "scalp" else 6.0
            sl = -0.8 if po["tag"] == "scalp" else -3.0
            max_h = 6 if po["tag"] == "scalp" else 48
            if pnl_pct >= tp or pnl_pct <= sl or held_h >= max_h:
                slip = stress.slippage_bps / 10000.0
                if po["side"] == 1:
                    ex = px[p] * (1 - slip)
                    gross = po["qty"] * ex
                    fee = gross * stress.fee_rate
                    pnl = (ex - po["entry"]) * po["qty"] - fee
                    cash += gross - fee
                else:
                    ex = px[p] * (1 + slip)
                    notional = po["qty"] * po["entry"]
                    pnl = (po["entry"] - ex) * po["qty"]
                    fee = (po["qty"] * ex) * stress.fee_rate
                    cash += notional + pnl - fee
                closed.append({"pnl": pnl, "regime": po["regime"]})
                if pnl < 0:
                    losses += 1
                    if losses >= 3:
                        pause_until = max(pause_until, ts + 180 * 60)
                else:
                    losses = 0
                pos[p] = {"side": 0, "qty": 0.0, "entry": 0.0, "ts": 0, "tag": "", "regime": ""}

        if ts - last_trade < v.cooldown_sec or ts < pause_until:
            eq = equity()
            peak = max(peak, eq)
            max_dd = max(max_dd, ((peak - eq) / peak * 100) if peak > 0 else 0)
            continue

        cands = [
            (abs(score[p]), p)
            for p in PAIRS
            if sig[p] in ("BUY", "SELL") and pos[p]["side"] == 0 and abs(score[p]) >= v.score_gate
        ]
        if cands:
            _, bp = max(cands)
            s = sig[bp]
            sc = score[bp]
            if px[bp] > 0:
                bench = list(hist["XXBTZEUR"])[-20:]
                bvol = 0.0
                if len(bench) >= 20:
                    m = float(np.mean(bench))
                    bvol = float(np.std(bench) / m * 100) if m > 0 else 0
                vol_scale = 1.0 if bvol <= 0 else min(1.25, max(0.35, 1.6 / bvol))
                alloc = min(v.alloc_cap, cash * v.alloc_pct) * (1.0 if risk_on else v.risk_off_scale) * vol_scale
                if alloc >= 8.0:
                    is_scalp = abs(sc) >= v.scalp_trigger
                    direction = None
                    if s == "BUY" and (risk_on or is_scalp):
                        direction = 1
                    if s == "SELL" and ((not risk_on) or is_scalp):
                        direction = -1
                    if direction == 1 and not v.allow_long:
                        direction = None
                    if direction == -1 and not v.allow_short:
                        direction = None
                    if direction is not None:
                        slip = stress.slippage_bps / 10000.0
                        en = px[bp] * (1 + slip) if direction == 1 else px[bp] * (1 - slip)
                        q = alloc / en
                        if direction == 1:
                            total = alloc * (1 + stress.fee_rate)
                            if total <= cash:
                                cash -= total
                                pos[bp] = {
                                    "side": 1,
                                    "qty": q,
                                    "entry": en,
                                    "ts": ts,
                                    "tag": "scalp" if is_scalp else "swing",
                                    "regime": regime_now,
                                }
                                last_trade = ts
                        else:
                            if alloc <= cash:
                                cash -= alloc
                                pos[bp] = {
                                    "side": -1,
                                    "qty": q,
                                    "entry": en,
                                    "ts": ts,
                                    "tag": "scalp" if is_scalp else "swing",
                                    "regime": regime_now,
                                }
                                last_trade = ts

        eq = equity()
        peak = max(peak, eq)
        max_dd = max(max_dd, ((peak - eq) / peak * 100) if peak > 0 else 0)

    for p in PAIRS:
        po = pos[p]
        if po["side"] == 0 or px[p] <= 0:
            continue
        slip = stress.slippage_bps / 10000.0
        if po["side"] == 1:
            ex = px[p] * (1 - slip)
            gross = po["qty"] * ex
            fee = gross * stress.fee_rate
            cash += gross - fee
        else:
            ex = px[p] * (1 + slip)
            notional = po["qty"] * po["entry"]
            pnl = (po["entry"] - ex) * po["qty"]
            fee = (po["qty"] * ex) * stress.fee_rate
            cash += notional + pnl - fee

    wins = sum(1 for t in closed if t["pnl"] > 0)
    reg = {k: [x["pnl"] for x in closed if x["regime"] == k] for k in ["bull", "bear", "chop"]}
    reg_stats = {
        k: {
            "trades": len(vs),
            "avg_pnl": round(float(np.mean(vs)), 4) if vs else 0.0,
            "winrate": round((sum(1 for z in vs if z > 0) / len(vs) * 100), 2) if vs else 0.0,
        }
        for k, vs in reg.items()
    }
    return {
        "final_eur": round(cash, 2),
        "return_pct": round((cash - 200.0) / 200.0 * 100, 2),
        "closed_trades": len(closed),
        "winrate_pct": round((wins / len(closed) * 100), 2) if closed else 0.0,
        "max_drawdown_pct": round(max_dd, 2),
        "regime_stats": reg_stats,
    }


def main():
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=365)
    start_ts, end_ts = int(start.timestamp()), int(end.timestamp())

    series = {p: fetch_ohlc_1h(p, start_ts, end_ts) for p in PAIRS}
    timeline = sorted(set().union(*[set(v.keys()) for v in series.values()]))
    per_pair_counts = {p: len(series[p]) for p in PAIRS}
    span_h = round((max(timeline) - min(timeline)) / 3600, 2) if timeline else 0

    variants = [
        Variant(name="main_baseline"),
        Variant(name="trend_bias", allow_mr=False, score_gate=8, risk_off_scale=0.50, regime_gate=-10),
        Variant(name="reversion_bias", allow_trend=False, score_gate=8, cooldown_sec=5400, scalp_trigger=30),
        Variant(
            name="grid_safe_long_only",
            allow_short=False,
            score_gate=10,
            cooldown_sec=7200,
            alloc_pct=0.10,
            alloc_cap=20.0,
            risk_off_scale=0.45,
        ),
        Variant(
            name="adaptive_hybrid",
            score_gate=10,
            cooldown_sec=4800,
            risk_off_scale=0.55,
            alloc_pct=0.16,
            alloc_cap=32.0,
            regime_gate=-8,
            scalp_trigger=31,
        ),
    ]
    stresses = [
        Stress("base", 0.0026, 8.0),
        Stress("high_slip", 0.0026, 12.0),
        Stress("high_fee", 0.00325, 8.0),
        Stress("combo", 0.00325, 12.0),
    ]

    all_results = {}
    for st in stresses:
        all_results[st.name] = {}
        for v in variants:
            all_results[st.name][v.name] = run_variant(series, timeline, v, st)

    # overfit detection with time split
    cut = timeline[int(len(timeline) * 0.7)]
    t_train = [t for t in timeline if t <= cut]
    t_test = [t for t in timeline if t > cut]
    split_eval = {}
    for v in variants:
        tr = run_variant(series, t_train, v, stresses[0])
        te = run_variant(series, t_test, v, stresses[0])
        split_eval[v.name] = {
            "train_return_pct": tr["return_pct"],
            "test_return_pct": te["return_pct"],
            "degrade_pct": round(te["return_pct"] - tr["return_pct"], 2),
        }

    base_name = "main_baseline"
    robust = []
    for v in variants:
        if v.name == base_name:
            continue
        deltas = []
        dd_ok = True
        for st in stresses:
            r = all_results[st.name][v.name]
            b = all_results[st.name][base_name]
            deltas.append(r["return_pct"] - b["return_pct"])
            if r["max_drawdown_pct"] > b["max_drawdown_pct"] + 1.0:
                dd_ok = False
        med = float(np.median(deltas))
        if med > 0 and dd_ok:
            robust.append(
                {
                    "variant": v.name,
                    "median_delta_return_vs_main_pct": round(med, 2),
                    "deltas": [round(x, 2) for x in deltas],
                }
            )

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "period": f"{start.date()}..{end.date()}",
        "candles_per_pair": per_pair_counts,
        "data_span_hours": span_h,
        "stress_profiles": [{"name": s.name, "fee_rate": s.fee_rate, "slippage_bps": s.slippage_bps} for s in stresses],
        "results": all_results,
        "overfit_split_base": split_eval,
        "robust_improvements_over_main": robust,
    }
    Path("reports").mkdir(exist_ok=True)
    out_path = Path("reports/mentor_beta_challenge_loop_1y.json")
    out_path.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
