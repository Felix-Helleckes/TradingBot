#!/usr/bin/env python3
import json
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

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
class Profile:
    name: str
    slip: float
    score_gate: int
    cooldown_sec: int
    fee: float = 0.0026


PROD = Profile("prod", slip=0.0008, score_gate=20, cooldown_sec=3600)
DEV = Profile("dev", slip=0.0002, score_gate=24, cooldown_sec=4200)


def fetch_ohlc_1h(pair: str, start_ts: int, end_ts: int) -> Dict[int, float]:
    # Prefer exact match, then fall back to any cached file for this pair (60m suffix)
    cache_path = CACHE_DIR / f"{pair}_{start_ts}_{end_ts}_1h.json"
    if not cache_path.exists():
        candidates = sorted(CACHE_DIR.glob(f"{pair}_*_60m.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            cache_path = candidates[0]
    if cache_path.exists():
        return {int(k): float(v) for k, v in json.loads(cache_path.read_text()).items()}

    out: Dict[int, float] = {}
    since = start_ts
    sess = requests.Session()
    loops = 0
    while since < end_ts and loops < 400:
        loops += 1
        for attempt in range(8):
            r = sess.get(
                "https://api.kraken.com/0/public/OHLC",
                params={"pair": pair, "interval": 60, "since": since},
                timeout=30,
            )
            j = r.json()
            errs = j.get("error") or []
            if errs and any("Too many requests" in e for e in errs):
                time.sleep(1.5 + attempt * 1.0)
                continue
            if errs:
                raise RuntimeError(f"{pair}: {errs}")
            break
        else:
            raise RuntimeError(f"{pair}: repeated rate-limit")

        res = j["result"]
        key = [k for k in res.keys() if k != "last"][0]
        rows = res[key]
        if not rows:
            break
        last_ts = since
        for row in rows:
            ts = int(row[0])
            if start_ts <= ts <= end_ts:
                out[ts] = float(row[4])
            last_ts = max(last_ts, ts)
        nxt = int(res.get("last", last_ts + 1))
        since = nxt if nxt > since else last_ts + 1
        time.sleep(0.35)

    cache_path.write_text(json.dumps(out))
    return out


def rsi(prices: List[float], period: int = 14):
    if len(prices) < period + 1:
        return None
    arr = np.array(prices)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100 if avg_gain > 0 else 0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def signal(prices: List[float]) -> Tuple[str, float]:
    if len(prices) < 50:
        return "HOLD", 0.0
    rv = rsi(prices)
    s20 = float(np.mean(prices[-20:]))
    s50 = float(np.mean(prices[-50:]))
    recent = np.array(prices[-20:])
    vol = float(np.std(recent) / np.mean(recent) * 100) if np.mean(recent) > 0 else 0.0
    if rv is None or vol < 0.15:
        return "HOLD", 0.0

    rscore = 0.0
    if rv < 30:
        rscore = (30 - rv) / 30 * 50
    elif rv > 70:
        rscore = -((rv - 70) / 30 * 50)

    sma_score = max(-50.0, min(50.0, (((s20 - s50) / s50) * 100) * 10))
    total = rscore + sma_score
    ratio = (s20 - s50) / s50

    if rv < 33 and ratio > -0.003:
        return "BUY", total
    if rv > 67 and ratio < 0.003:
        return "SELL", total
    if ratio > 0.006 and 45 <= rv <= 68:
        return "BUY", total + 8
    if ratio < -0.006 and 32 <= rv <= 55:
        return "SELL", total - 8
    return "HOLD", total


def mtf_regime_score(prices: List[float]):
    if len(prices) < 80:
        return None
    r5 = rsi(prices[-25:]) or 50.0
    r15 = rsi(prices[-35:]) or 50.0
    r60 = rsi(prices[-80:]) or 50.0
    s10 = float(np.mean(prices[-10:]))
    s30 = float(np.mean(prices[-30:]))
    s70 = float(np.mean(prices[-70:]))

    trend = (((s10 - s30) / s30) * 100) * 0.9 + (((s30 - s70) / s70) * 100) * 1.2
    momentum = ((r5 - 50) * 0.4) + ((r15 - 50) * 0.35) + ((r60 - 50) * 0.25)
    recent = prices[-24:]
    mean = sum(recent) / len(recent)
    vol = 0.0
    if mean > 0:
        var = sum((p - mean) ** 2 for p in recent) / len(recent)
        vol = ((var**0.5) / mean) * 100
    vol_penalty = max(0.0, vol - 2.2) * 1.5
    return trend + momentum - vol_penalty


def run_profile(series: Dict[str, Dict[int, float]], timeline: List[int], profile: Profile):
    cash = 200.0
    hist = {p: deque(maxlen=180) for p in PAIRS}
    sigs = {p: "HOLD" for p in PAIRS}
    scores = {p: 0.0 for p in PAIRS}
    price = {p: 0.0 for p in PAIRS}

    pos = {p: 0 for p in PAIRS}  # 1 long, -1 short
    qty = {p: 0.0 for p in PAIRS}
    entry = {p: 0.0 for p in PAIRS}
    et = {p: 0 for p in PAIRS}
    tag = {p: "" for p in PAIRS}

    trades = 0
    last_trade = 0
    loss_streak = 0
    pause_until = 0
    peak = 200.0
    max_dd = 0.0

    def equity():
        e = cash
        for p in PAIRS:
            pr = price.get(p, 0.0)
            if pos[p] == 1:
                e += qty[p] * pr
            elif pos[p] == -1:
                e += (entry[p] - pr) * qty[p]
        return e

    for ts in timeline:
        for p in PAIRS:
            pr = series[p].get(ts)
            if pr is None:
                continue
            price[p] = pr
            hist[p].append(pr)
            s, sc = signal(list(hist[p]))
            sigs[p] = s
            scores[p] = sc

        reg = mtf_regime_score(list(hist["XXBTZEUR"]))
        risk_on = True if reg is None else reg >= -2.0

        # exits
        for p in PAIRS:
            if pos[p] == 0 or price[p] <= 0:
                continue
            pr = price[p]
            pnl_pct = ((pr - entry[p]) / entry[p]) * 100 if pos[p] == 1 else ((entry[p] - pr) / entry[p]) * 100
            tp = 1.1 if tag[p] == "scalp" else 5.5
            sl = -0.7 if tag[p] == "scalp" else -2.8
            max_h = 6 if tag[p] == "scalp" else 36
            held_h = (ts - et[p]) / 3600
            flip = (pos[p] == 1 and (not risk_on) and sigs[p] == "SELL") or (
                pos[p] == -1 and risk_on and sigs[p] == "BUY"
            )

            if pnl_pct >= tp or pnl_pct <= sl or held_h >= max_h or flip:
                if pos[p] == 1:
                    exit_px = pr * (1 - profile.slip)
                    gross = qty[p] * exit_px
                    fee = gross * profile.fee
                    pnl_eur = (exit_px - entry[p]) * qty[p] - fee
                    cash += gross - fee
                else:
                    exit_px = pr * (1 + profile.slip)
                    notional = qty[p] * entry[p]
                    pnl_eur = (entry[p] - exit_px) * qty[p]
                    fee = (qty[p] * exit_px) * profile.fee
                    cash += notional + pnl_eur - fee

                loss_streak = loss_streak + 1 if pnl_eur < 0 else 0
                if loss_streak >= 3:
                    pause_until = ts + 180 * 60

                pos[p] = 0
                qty[p] = 0.0
                entry[p] = 0.0
                et[p] = 0
                tag[p] = ""
                trades += 1

        if ts - last_trade < profile.cooldown_sec or ts < pause_until:
            eq = equity()
            peak = max(peak, eq)
            if peak > 0:
                max_dd = max(max_dd, (peak - eq) / peak * 100)
            continue

        candidates = [
            (abs(scores[p]), p)
            for p in PAIRS
            if sigs[p] in ("BUY", "SELL") and pos[p] == 0 and abs(scores[p]) >= profile.score_gate
        ]
        if candidates:
            _, bp = max(candidates)
            s = sigs[bp]
            sc = scores[bp]
            pr = price[bp]
            if pr > 0:
                alloc = min(40.0, cash * 0.18)
                if reg is not None:
                    alloc *= min(1.35, max(0.45, abs(reg) / 35))
                if alloc >= 8.0:
                    scalp = abs(sc) >= 30
                    direction = None
                    if s == "BUY" and (risk_on or scalp):
                        direction = 1
                    if s == "SELL" and ((not risk_on) or scalp):
                        direction = -1

                    if direction == 1:
                        entry_px = pr * (1 + profile.slip)
                        q = alloc / entry_px
                        total = alloc * (1 + profile.fee)
                        if total <= cash:
                            cash -= total
                            pos[bp] = 1
                            qty[bp] = q
                            entry[bp] = entry_px
                            et[bp] = ts
                            tag[bp] = "scalp" if scalp else "swing"
                            trades += 1
                            last_trade = ts
                    elif direction == -1 and alloc <= cash:
                        entry_px = pr * (1 - profile.slip)
                        q = alloc / entry_px
                        cash -= alloc
                        pos[bp] = -1
                        qty[bp] = q
                        entry[bp] = entry_px
                        et[bp] = ts
                        tag[bp] = "scalp" if scalp else "swing"
                        trades += 1
                        last_trade = ts

        eq = equity()
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100)

    # liquidate open positions at end
    for p in PAIRS:
        if pos[p] == 0 or price[p] <= 0:
            continue
        pr = price[p]
        if pos[p] == 1:
            exit_px = pr * (1 - profile.slip)
            cash += qty[p] * exit_px * (1 - profile.fee)
        else:
            exit_px = pr * (1 + profile.slip)
            notional = qty[p] * entry[p]
            pnl = (entry[p] - exit_px) * qty[p]
            cash += notional + pnl - (qty[p] * exit_px * profile.fee)

    ret = (cash - 200.0) / 200.0 * 100
    return {
        "final_eur": round(cash, 2),
        "return_pct": round(ret, 2),
        "trades": trades,
        "max_drawdown_pct": round(max_dd, 2),
    }


def main():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365)
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    print("Fetching detailed 1h yearly data...")
    series = {}
    for p in PAIRS:
        data = fetch_ohlc_1h(p, start_ts, end_ts)
        series[p] = data
        print(f"{p}: {len(data)} points")

    timeline = sorted(set().union(*[set(v.keys()) for v in series.values()]))

    prod = run_profile(series, timeline, PROD)
    dev = run_profile(series, timeline, DEV)

    output = {
        "period": f"{start.date()}..{end.date()}",
        "initial_eur": 200.0,
        "prod": prod,
        "dev": dev,
    }

    out_path = Path("reports/prod_dev_yearly_detailed.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
