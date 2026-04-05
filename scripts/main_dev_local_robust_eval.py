#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Dict, List, Tuple

import pandas as pd

PAIRS = ["XXBTZEUR", "XETHZEUR", "SOLEUR", "ADAEUR", "DOTEUR", "XXRPZEUR", "LINKEUR"]
LOCAL_TS_DIR = Path(os.getenv("KRAKEN_TS_DIR", "/mnt/fritz_nas/Volume/kraken/2025/time_sales"))
OUT = Path("reports/main_dev_local_robust_eval.json")


@dataclass
class Profile:
    name: str
    fee: float
    slip: float
    score_gate: float
    cooldown_sec: int
    scalp_tp: float
    scalp_sl: float
    swing_tp: float
    swing_sl: float


MAIN = Profile(
    name="main",
    fee=0.0026,
    slip=0.0008,
    score_gate=20.0,
    cooldown_sec=3600,
    scalp_tp=1.2,
    scalp_sl=-0.8,
    swing_tp=6.0,
    swing_sl=-3.0,
)

DEV = Profile(
    name="dev",
    fee=0.0026,
    slip=0.0004,
    score_gate=22.0,
    cooldown_sec=4200,
    scalp_tp=1.1,
    scalp_sl=-0.7,
    swing_tp=5.8,
    swing_sl=-2.8,
)


def pair_file_candidates(pair: str) -> List[str]:
    # Robust symbol mapping from Kraken API names to local filenames
    mapping = {
        "XXBTZEUR": ["XBTEUR.csv", "XXBTZEUR.csv", "BTCEUR.csv"],
        "XETHZEUR": ["ETHEUR.csv", "XETHZEUR.csv"],
        "XXRPZEUR": ["XRPEUR.csv", "XXRPZEUR.csv"],
    }
    if pair in mapping:
        return mapping[pair]
    simple = pair.replace("Z", "")
    return [f"{pair}.csv", f"{simple}.csv"]


def load_ticks(pair: str, start_ts: int, end_ts: int) -> pd.DataFrame:
    fpath = None
    for name in pair_file_candidates(pair):
        p = LOCAL_TS_DIR / name
        if p.exists():
            fpath = p
            break
    if fpath is None:
        raise FileNotFoundError(f"Missing local file for {pair} under {LOCAL_TS_DIR}")

    # Chunked C-engine CSV parsing (fast + memory-safe), with strict window filter.
    frames: List[pd.DataFrame] = []
    for chunk in pd.read_csv(
        fpath,
        names=["ts", "price", "volume"],
        usecols=[0, 1, 2],
        header=None,
        dtype={"ts": "float64", "price": "float64", "volume": "float64"},
        engine="c",
        on_bad_lines="skip",
        chunksize=1_500_000,
    ):
        chunk = chunk.dropna()
        chunk = chunk[(chunk["price"] > 0)]
        chunk["ts"] = chunk["ts"].astype("int64")
        chunk = chunk[(chunk["ts"] >= start_ts) & (chunk["ts"] <= end_ts)]
        if not chunk.empty:
            frames.append(chunk)

    if not frames:
        raise RuntimeError(f"No ticks in requested range for {pair} ({fpath})")
    return pd.concat(frames, ignore_index=True)


def ticks_to_1h_bars(df_ticks: pd.DataFrame, start_ts: int, end_ts: int) -> Dict[int, dict]:
    df = df_ticks.copy()
    df["bucket"] = (df["ts"] // 3600) * 3600
    grouped = df.groupby("bucket", sort=True).agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("volume", "sum"),
        trades=("price", "count"),
    )

    full_idx = pd.Index(range((start_ts // 3600) * 3600, (end_ts // 3600) * 3600 + 3600, 3600), name="bucket")
    grouped = grouped.reindex(full_idx)
    grouped["close"] = grouped["close"].ffill()
    grouped["open"] = grouped["open"].fillna(grouped["close"])
    grouped["high"] = grouped["high"].fillna(grouped["close"])
    grouped["low"] = grouped["low"].fillna(grouped["close"])
    grouped["volume"] = grouped["volume"].fillna(0.0)
    grouped["trades"] = grouped["trades"].fillna(0).astype(int)
    grouped = grouped.dropna(subset=["close"])

    out: Dict[int, dict] = {}
    for idx, row in grouped.iterrows():
        out[int(idx)] = {
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "trades": int(row["trades"]),
        }
    return out


def rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(-period, 0):
        d = prices[i] - prices[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    ag = mean(gains)
    al = mean(losses)
    if al <= 1e-12:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


def features(prices: List[float], vols: List[float]) -> dict:
    if len(prices) < 80:
        return {"score": 0.0, "signal": "HOLD", "vol_pct": 0.0}
    p20 = prices[-20:]
    p50 = prices[-50:]
    p80 = prices[-80:]
    c = prices[-1]
    sma20 = mean(p20)
    sma50 = mean(p50)
    sma80 = mean(p80)
    rv = rsi(prices, 14)
    ret1 = (prices[-1] / prices[-2] - 1.0) * 100.0
    ret6 = (prices[-1] / prices[-7] - 1.0) * 100.0 if prices[-7] > 0 else 0.0
    vol_pct = (pstdev(p20) / sma20 * 100.0) if sma20 > 0 else 0.0
    vol_ratio = (vols[-1] / (mean(vols[-24:]) + 1e-9)) if len(vols) >= 24 else 1.0

    trend = ((sma20 - sma50) / sma50) * 100.0 * 8.0 + ((sma50 - sma80) / sma80) * 100.0 * 4.0
    meanrev = 0.0
    if rv < 32:
        meanrev = (32 - rv) * 0.9
    elif rv > 68:
        meanrev = -(rv - 68) * 0.9

    micro = ret1 * 1.3 + ret6 * 0.45
    liquidity_bonus = min(3.0, max(-3.0, (vol_ratio - 1.0) * 2.0))
    vol_penalty = max(0.0, vol_pct - 2.2) * 1.6

    score = trend + meanrev + micro + liquidity_bonus - vol_penalty

    sig = "HOLD"
    if score >= 8 and rv <= 72 and c >= sma20 * 0.985:
        sig = "BUY"
    elif score <= -8 and rv >= 28 and c <= sma20 * 1.015:
        sig = "SELL"

    return {"score": score, "signal": sig, "vol_pct": vol_pct}


def run_profile(series: Dict[str, Dict[int, dict]], timeline: List[int], profile: Profile, initial: float = 200.0) -> dict:
    cash = initial
    hist_price = {p: deque(maxlen=200) for p in PAIRS}
    hist_vol = {p: deque(maxlen=200) for p in PAIRS}
    signal = {p: "HOLD" for p in PAIRS}
    score = {p: 0.0 for p in PAIRS}
    last_px = {p: 0.0 for p in PAIRS}

    pos = {p: 0 for p in PAIRS}
    qty = {p: 0.0 for p in PAIRS}
    entry = {p: 0.0 for p in PAIRS}
    et = {p: 0 for p in PAIRS}
    tag = {p: "" for p in PAIRS}

    last_trade = 0
    loss_streak = 0
    pause_until = 0
    closed = []
    peak = initial
    max_dd = 0.0

    def equity() -> float:
        eq = cash
        for p in PAIRS:
            px = last_px[p]
            if px <= 0:
                continue
            if pos[p] == 1:
                eq += qty[p] * px
            elif pos[p] == -1:
                eq += (entry[p] - px) * qty[p]
        return eq

    for ts in timeline:
        for p in PAIRS:
            bar = series[p].get(ts)
            if bar is None:
                continue
            px = bar["close"]
            last_px[p] = px
            hist_price[p].append(px)
            hist_vol[p].append(bar["volume"])
            ff = features(list(hist_price[p]), list(hist_vol[p]))
            signal[p] = ff["signal"]
            score[p] = ff["score"]

        btc_hist = list(hist_price["XXBTZEUR"])
        btc_vol = list(hist_vol["XXBTZEUR"])
        market_regime = features(btc_hist, btc_vol)
        risk_on = market_regime["score"] >= -4

        # exits
        for p in PAIRS:
            if pos[p] == 0 or last_px[p] <= 0:
                continue
            px = last_px[p]
            pnl_pct = ((px - entry[p]) / entry[p]) * 100 if pos[p] == 1 else ((entry[p] - px) / entry[p]) * 100
            is_scalp = tag[p] == "scalp"
            tp = profile.scalp_tp if is_scalp else profile.swing_tp
            sl = profile.scalp_sl if is_scalp else profile.swing_sl
            max_h = 8 if is_scalp else 48
            held_h = (ts - et[p]) / 3600
            flip = (pos[p] == 1 and signal[p] == "SELL") or (pos[p] == -1 and signal[p] == "BUY")
            if pnl_pct >= tp or pnl_pct <= sl or held_h >= max_h or flip:
                if pos[p] == 1:
                    exit_px = px * (1 - profile.slip)
                    gross = qty[p] * exit_px
                    fee = gross * profile.fee
                    pnl = (exit_px - entry[p]) * qty[p] - fee
                    cash += gross - fee
                else:
                    exit_px = px * (1 + profile.slip)
                    notional = qty[p] * entry[p]
                    pnl = (entry[p] - exit_px) * qty[p]
                    fee = (qty[p] * exit_px) * profile.fee
                    cash += notional + pnl - fee
                closed.append(pnl)
                if pnl < 0:
                    loss_streak += 1
                    if loss_streak >= 3:
                        pause_until = ts + 3 * 3600
                else:
                    loss_streak = 0
                pos[p] = 0
                qty[p] = 0.0
                entry[p] = 0.0
                et[p] = 0
                tag[p] = ""

        if ts < pause_until or ts - last_trade < profile.cooldown_sec:
            eq = equity()
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100) if peak > 0 else max_dd
            continue

        cands = [(abs(score[p]), p) for p in PAIRS if pos[p] == 0 and signal[p] in ("BUY", "SELL") and abs(score[p]) >= profile.score_gate]
        if cands:
            _, bp = max(cands)
            px = last_px[bp]
            if px > 0:
                alloc = min(40.0, cash * 0.18)
                if not risk_on:
                    alloc *= 0.7
                if alloc >= 8.0:
                    direction = 1 if signal[bp] == "BUY" else -1
                    is_scalp = abs(score[bp]) >= 18.0
                    if direction == 1:
                        ep = px * (1 + profile.slip)
                        q = alloc / ep
                        total = alloc * (1 + profile.fee)
                        if total <= cash:
                            cash -= total
                            pos[bp] = 1
                            qty[bp] = q
                            entry[bp] = ep
                            et[bp] = ts
                            tag[bp] = "scalp" if is_scalp else "swing"
                            last_trade = ts
                    else:
                        ep = px * (1 - profile.slip)
                        q = alloc / ep
                        if alloc <= cash:
                            cash -= alloc
                            pos[bp] = -1
                            qty[bp] = q
                            entry[bp] = ep
                            et[bp] = ts
                            tag[bp] = "scalp" if is_scalp else "swing"
                            last_trade = ts

        eq = equity()
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100) if peak > 0 else max_dd

    # final liquidation
    for p in PAIRS:
        if pos[p] == 0 or last_px[p] <= 0:
            continue
        px = last_px[p]
        if pos[p] == 1:
            exit_px = px * (1 - profile.slip)
            cash += qty[p] * exit_px * (1 - profile.fee)
        else:
            exit_px = px * (1 + profile.slip)
            notional = qty[p] * entry[p]
            pnl = (entry[p] - exit_px) * qty[p]
            cash += notional + pnl - (qty[p] * exit_px * profile.fee)

    # hourly equity returns for sharpe proxy
    # computed from closed pnl only for speed + robustness here
    trade_count = len(closed)
    wins = sum(1 for x in closed if x > 0)
    avg_pnl = mean(closed) if closed else 0.0
    std_pnl = pstdev(closed) if len(closed) > 1 else 0.0
    sharpe_like = (avg_pnl / std_pnl * math.sqrt(trade_count)) if std_pnl > 1e-12 else 0.0

    ret = (cash - initial) / initial * 100.0
    return {
        "final_eur": round(cash, 2),
        "return_pct": round(ret, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "trades": trade_count,
        "winrate_pct": round((wins / trade_count) * 100.0, 2) if trade_count else 0.0,
        "sharpe_like": round(sharpe_like, 3),
    }


def evaluate_consistency(series: Dict[str, Dict[int, dict]], full_timeline: List[int], profile: Profile) -> List[dict]:
    # 4 quarter slices as out-of-sample consistency check
    if len(full_timeline) < 24:
        return []
    n = len(full_timeline)
    step = n // 4
    out = []
    for i in range(4):
        a = i * step
        b = n if i == 3 else (i + 1) * step
        seg_ts = full_timeline[a:b]
        if len(seg_ts) < 24:
            continue
        seg = run_profile(series, seg_ts, profile)
        seg["segment"] = i + 1
        out.append(seg)
    return out


def merge_recommendation(main: dict, dev: dict, main_seg: List[dict], dev_seg: List[dict]) -> dict:
    dev_better_segments = sum(1 for m, d in zip(main_seg, dev_seg) if d["return_pct"] > m["return_pct"] and d["max_drawdown_pct"] <= m["max_drawdown_pct"] + 0.8)
    checks = {
        "return_edge": dev["return_pct"] >= main["return_pct"] + 3.0,
        "risk_not_worse": dev["max_drawdown_pct"] <= main["max_drawdown_pct"] + 1.0,
        "quality_not_worse": dev["sharpe_like"] >= main["sharpe_like"] + 0.1,
        "trade_count_sane": dev["trades"] >= max(8, int(main["trades"] * 0.7)),
        "oos_consistency": dev_better_segments >= 3,
    }
    clearly_superior = all(checks.values())
    return {
        "clearly_superior": clearly_superior,
        "checks": checks,
        "dev_better_segments": dev_better_segments,
        "recommendation": "MERGE_DEV" if clearly_superior else "KEEP_MAIN_NO_MERGE_YET",
    }


def main() -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365)
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    series: Dict[str, Dict[int, dict]] = {}
    data_quality = {}
    for p in PAIRS:
        ticks = load_ticks(p, start_ts, end_ts)
        bars = ticks_to_1h_bars(ticks, start_ts, end_ts)
        series[p] = bars
        data_quality[p] = {
            "ticks": len(ticks),
            "bars": len(bars),
            "first_ts": min(bars) if bars else None,
            "last_ts": max(bars) if bars else None,
        }

    timeline = sorted(set().union(*[set(v.keys()) for v in series.values()]))
    main_res = run_profile(series, timeline, MAIN)
    dev_res = run_profile(series, timeline, DEV)
    main_seg = evaluate_consistency(series, timeline, MAIN)
    dev_seg = evaluate_consistency(series, timeline, DEV)
    rec = merge_recommendation(main_res, dev_res, main_seg, dev_seg)

    out = {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "data_source": str(LOCAL_TS_DIR),
        "pairs": PAIRS,
        "data_quality": data_quality,
        "main": main_res,
        "dev": dev_res,
        "segments_main": main_seg,
        "segments_dev": dev_seg,
        "decision": rec,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
