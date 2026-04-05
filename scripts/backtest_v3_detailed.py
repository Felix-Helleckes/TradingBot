#!/usr/bin/env python3
"""Detailed V3 backtest estimator (long+short capable simulation layer).

Notes:
- Uses Kraken OHLC 1h data.
- Includes fees + configurable slippage.
- Simulates regime switching, multi-edge entries, risk guards, long/short engine.
- This is a research simulator, not live-order execution code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import requests

# Load live config so the backtest uses the same signal/risk params as the bot
try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # pip install tomli
    except ImportError:
        try:
            import toml as tomllib  # pip install toml (in requirements.txt)
            tomllib._binary_mode = False  # toml reads text, not bytes
        except ImportError:
            tomllib = None

_CFG_PATH = Path(__file__).resolve().parent.parent / "config.toml"
_CFG: dict = {}
if tomllib is not None and _CFG_PATH.exists():
    _binary = not getattr(tomllib, "_binary_mode", True) is False
    _open_mode = "rb" if _binary else "r"
    with open(_CFG_PATH, _open_mode) as _f:
        _CFG = tomllib.load(_f)
_RM = _CFG.get("risk_management", {})

# Signal engine params (read from config, same as analysis.py)
_ENABLE_MR     = bool(_RM.get("enable_mean_reversion_signals", True))
_ENABLE_TREND  = bool(_RM.get("enable_trend_breakout_signals", True))
_MR_OVERSOLD   = float(_RM.get("mr_rsi_oversold_threshold", 33.0))
_MR_OVERBOUGHT = float(_RM.get("mr_rsi_overbought_threshold", 67.0))

# Risk/position params from config
_MIN_BUY_SCORE      = float(_RM.get("min_buy_score", 8.0))
_TRADE_COOLDOWN_SEC = int(_RM.get("trade_cooldown_seconds", 3600))
_RISK_OFF_MULT      = float(_RM.get("risk_off_allocation_multiplier", 0.50))
_REGIME_MIN_SCORE   = float(_RM.get("regime_min_score", -10.0))
# ATR dynamic TP: floor = _ATR_TP_MULT × ATR% of close prices (prevents early exits)
_ENABLE_ATR_TP  = bool(_RM.get("enable_atr_dynamic_tp", True))
_ATR_TP_MULT    = float(_RM.get("atr_tp_multiplier", 2.0))
_BASE_TP_PCT    = float(_RM.get("take_profit_percent", 3.0))
_MAX_TP_PCT     = float(_RM.get("max_take_profit_percent", 7.0))
_ATR_PERIOD     = int(_RM.get("atr_period", 14))

PAIRS = ["XETHZEUR", "SOLEUR", "ADAEUR", "XXRPZEUR", "LINKEUR"]
CACHE_DIR = Path("data/ohlc_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
MENTOR_CACHE_DIR = Path("data/mentor_cache_1h")

# NAS path: unified kraken/ structure, year-based
_NAS_DEFAULT = (
    "/mnt/fritz_nas/Volume/kraken/2026/ohlc"
)
LOCAL_TS_DIR = Path(os.getenv("KRAKEN_TS_DIR", _NAS_DEFAULT))
USE_LOCAL_TS = os.getenv("USE_LOCAL_TS", "1") == "1"


@dataclass
class Position:
    side: int = 0  # 1 long, -1 short
    qty: float = 0.0
    entry_price: float = 0.0
    entry_ts: int = 0
    tag: str = ""


def _pair_file_candidates(pair: str) -> List[str]:
    clean = pair.replace("Z", "")
    return [f"{pair}.csv", f"{clean}.csv", f"{clean.replace('XXBT', 'XBT')}.csv"]


def load_local_timesales_ohlc(pair: str, since_ts: int, end_ts: int, interval: int = 60) -> Dict[int, float]:
    if not LOCAL_TS_DIR.exists():
        return {}
    
    # Try subfolder structure: pair/ohlc_{interval}m.csv
    fpath = LOCAL_TS_DIR / pair / f"ohlc_{interval}m.csv"
    if not fpath.exists():
        # Fallback to candidates in root
        for name in _pair_file_candidates(pair):
            p = LOCAL_TS_DIR / name
            if p.exists():
                fpath = p
                break
    
    if fpath is None or not fpath.exists():
        return {}

    bucket = max(1, int(interval)) * 60
    out: Dict[int, float] = {}
    seen_window = False
    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.strip().split(",")
            if len(parts) < 2:
                continue
            try:
                ts = int(float(parts[0]))
                px = float(parts[1])
            except Exception:
                continue
            if ts < since_ts:
                continue
            if ts > end_ts:
                if seen_window:
                    break
                continue
            seen_window = True
            bts = (ts // bucket) * bucket
            out[bts] = px  # last price in bucket
    return out


def fetch_ohlc(pair: str, since_ts: int, end_ts: int, interval: int = 60) -> Dict[int, float]:
    # 1. Exact-match cache in ohlc_cache/
    cache_path = CACHE_DIR / f"{pair}_{since_ts}_{end_ts}_{interval}m.json"
    if cache_path.exists():
        return {int(k): float(v) for k, v in json.loads(cache_path.read_text()).items()}

    # 2. mentor_cache_1h — use if coverage ≥ threshold (env var override for sweeps)
    _COVERAGE_THRESHOLD = float(os.getenv("BT_COVERAGE_THRESHOLD", "0.70"))
    if MENTOR_CACHE_DIR.exists():
        candidates = sorted(MENTOR_CACHE_DIR.glob(f"{pair}_*_60m.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            # Merge all available cache files for this pair for maximum coverage
            merged: Dict[int, float] = {}
            for cp in candidates:
                try:
                    raw = {int(k): float(v) for k, v in json.loads(cp.read_text()).items()}
                    merged.update(raw)
                except Exception:
                    continue
            filtered = {k: v for k, v in merged.items() if since_ts <= k <= end_ts}
            expected_candles = (end_ts - since_ts) / (interval * 60)
            coverage = len(filtered) / max(1, expected_candles)
            if coverage >= _COVERAGE_THRESHOLD:
                return filtered
            # Coverage too low — fall through to API to fill gaps

    if USE_LOCAL_TS:
        local = load_local_timesales_ohlc(pair, since_ts, end_ts, interval)
        if local:
            cache_path.write_text(json.dumps(local))
            return local

    out: Dict[int, float] = {}
    since = since_ts
    sess = requests.Session()
    loops = 0

    while since < end_ts and loops < 500:
        loops += 1
        for attempt in range(8):
            r = sess.get(
                "https://api.kraken.com/0/public/OHLC",
                params={"pair": pair, "interval": interval, "since": since},
                timeout=30,
            )
            j = r.json()
            errs = j.get("error") or []
            if errs and any("Too many requests" in e for e in errs):
                time.sleep(1.5 + attempt * 1.0)
                continue
            if errs:
                raise RuntimeError(f"Kraken error for {pair}: {errs}")
            break
        else:
            raise RuntimeError(f"Kraken rate-limit retries exhausted for {pair}")

        res = j.get("result", {})
        key = [k for k in res.keys() if k != "last"]
        if not key:
            break
        rows = res[key[0]]
        if not rows:
            break

        last_ts = since
        for row in rows:
            ts = int(row[0])
            if since_ts <= ts <= end_ts:
                out[ts] = float(row[4])
            last_ts = max(last_ts, ts)

        nxt = int(res.get("last", last_ts + 1))
        since = nxt if nxt > since else (last_ts + 1)
        time.sleep(0.35)

    cache_path.write_text(json.dumps(out))
    return out


def calc_rsi(prices: List[float], period: int = 14) -> float | None:
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


def strategy_signal(prices: List[float]) -> Tuple[str, float]:
    """Dual-mode signal engine mirroring analysis.py (MR + trend/breakout).
    Reads mode flags from config.toml at module load time."""
    if len(prices) < 50:
        return "HOLD", 0.0

    prices_arr = np.array(prices)
    current_price = prices_arr[-1]

    sma20 = float(np.mean(prices_arr[-20:]))
    std20 = float(np.std(prices_arr[-20:]))
    sma50 = float(np.mean(prices_arr[-50:]))
    upper_bb = sma20 + 2.0 * std20
    lower_bb = sma20 - 2.0 * std20
    sma_ratio = (sma20 - sma50) / sma50 if sma50 > 0 else 0.0

    rsi_full = calc_rsi(list(prices_arr), 14)
    rsi_confirm = calc_rsi(list(prices_arr[-20:]), 14) if len(prices_arr) >= 20 else None

    signal = "HOLD"
    score = 0.0

    # --- Mean-reversion path (reversion_bias) ---
    if _ENABLE_MR and rsi_full is not None:
        rsi_s = 0.0
        if rsi_full < 30:
            rsi_s = (30 - rsi_full) / 30 * 50
        elif rsi_full > 70:
            rsi_s = -((rsi_full - 70) / 30 * 50)
        sma_s = max(-50.0, min(50.0, sma_ratio * 100 * 10))
        mr_score = rsi_s + sma_s
        if rsi_full <= _MR_OVERSOLD and sma_ratio > -0.003:
            signal = "BUY"
            score = mr_score
        elif rsi_full >= _MR_OVERBOUGHT and sma_ratio < 0.003:
            signal = "SELL"
            score = mr_score

    # --- Trend/breakout path (Bollinger Band momentum) ---
    if _ENABLE_TREND:
        if current_price > upper_bb:
            if current_price > sma50 and (rsi_confirm is None or rsi_confirm >= 55):
                trend_score = min(50.0, 25.0 + (((current_price - upper_bb) / upper_bb) * 100 * 50.0))
                if trend_score > score:
                    signal = "BUY"
                    score = trend_score
            elif current_price > sma50 and score == 0.0:
                score = 8.0
        elif current_price < lower_bb:
            if current_price < sma50 and (rsi_confirm is None or rsi_confirm <= 45):
                trend_score = max(-50.0, -25.0 - (((lower_bb - current_price) / lower_bb) * 100 * 50.0))
                if trend_score < score:
                    signal = "SELL"
                    score = trend_score
            elif current_price < sma50 and score == 0.0:
                score = -8.0

    return signal, max(-50.0, min(50.0, score))


def compute_slip_for_pair(hist_prices: List[float], slippage_bps: float, model: str = 'fixed') -> float:
    """Compute slip fraction (e.g. 0.0008 for 8 bps) for a pair given recent history and model."""
    base = max(0.0, float(slippage_bps)) / 10000.0
    if model == 'fixed' or not hist_prices or len(hist_prices) < 5:
        return base
    try:
        arr = np.array(hist_prices)
        rets = np.diff(np.log(arr))
        vol = float(np.std(rets)) if rets.size > 0 else 0.0
        # scale multiplier conservatively: small factor of realized vol
        multiplier = 1.0 + min(5.0, vol * 10.0)
        slip = base * multiplier
        # cap slip to sensible limit
        return min(slip, 0.2)
    except Exception:
        return base


def simulate_twap_entry(pair: str, direction: int, allocation: float, idx: int, all_ts: List[int], series: Dict[str, Dict[int, float]], hist: Dict[str, deque], slices: int, slippage_bps: float, slippage_model: str, fee_rate: float):
    """Simulate a TWAP-style entry across the next `slices` timestamps. Returns (entry_price, total_qty, total_fee).

    If insufficient future timestamps exist, uses available ones and repeats last price.
    """
    S = max(1, int(slices))
    total_qty = 0.0
    total_fee = 0.0
    executed_notional = 0.0
    slice_notional = allocation / S
    prices = []
    n_ts = len(all_ts)
    for k in range(S):
        j = idx + k
        if j < n_ts:
            t = all_ts[j]
            p = series.get(pair, {}).get(t)
            if p is None:
                # fallback to last known price in hist or series
                p = list(hist.get(pair, []))[-1] if hist.get(pair) else None
            if p is None:
                # cannot execute, return immediate placeholder
                p = list(series.get(pair, {}).values())[-1] if series.get(pair) else 0.0
        else:
            # repeat last available
            p = list(hist.get(pair, []))[-1] if hist.get(pair) else (list(series.get(pair, {}).values())[-1] if series.get(pair) else 0.0)
        prices.append(p)
    # simulate slices
    qtys = []
    for p in prices:
        if p <= 0:
            qtys.append(0.0)
            continue
        slip = compute_slip_for_pair(list(hist.get(pair, [])), slippage_bps, slippage_model)
        exec_px = p * (1.0 + slip) if direction == 1 else p * (1.0 - slip)
        q = slice_notional / exec_px if exec_px > 0 else 0.0
        fee = exec_px * q * fee_rate
        qtys.append(q)
        total_qty += q
        total_fee += fee
        executed_notional += exec_px * q
    entry_price = (executed_notional / total_qty) if total_qty > 0 else (prices[0] if prices else 0.0)
    return entry_price, total_qty, total_fee


def _atr_dynamic_tp(prices: List[float]) -> float:
    """Compute ATR-based dynamic TP% from a list of close prices.
    ATR ≈ mean(|close[i] - close[i-1]|) over _ATR_PERIOD candles.
    Returns max(base_tp, mult × ATR%) capped at max_tp.
    """
    if not _ENABLE_ATR_TP or len(prices) < _ATR_PERIOD + 1:
        return _BASE_TP_PCT
    recent = prices[-(_ATR_PERIOD + 1):]
    trs = [abs(recent[i] - recent[i - 1]) for i in range(1, len(recent))]
    atr = sum(trs) / len(trs)
    last_price = recent[-1]
    if last_price <= 0:
        return _BASE_TP_PCT
    atr_pct = (atr / last_price) * 100.0
    return min(_MAX_TP_PCT, max(_BASE_TP_PCT, _ATR_TP_MULT * atr_pct))


def run_backtest(days: int, initial_eur: float, fee_rate: float, slippage_bps: float, execution_mode: str = 'immediate', twap_slices: int = 3, slippage_model: str = 'fixed', daytrading: bool = False) -> dict:
    # daytrading flag from CLI overrides config; otherwise use config value
    _DT = _CFG.get("daytrading", {})
    dt_enabled = daytrading  # CLI flag is the one source of truth for backtest
    dt_max_hold_h   = float(_DT.get("max_hold_hours", 12))
    dt_sl           = -abs(float(_DT.get("intraday_sl_percent", 1.5)))
    dt_tp_base      = float(_DT.get("intraday_tp_percent", 1.8))
    dt_cooldown_sec = int(_DT.get("intraday_cooldown_seconds", 1800))
    dt_max_losses   = int(_DT.get("max_consecutive_losses", 2))
    dt_pause_min    = float(_DT.get("loss_streak_pause_minutes", 30))

    # In daytrading mode, ATR dynamic TP still applies but with lower base
    if dt_enabled:
        dt_tp_func = lambda prices: min(_MAX_TP_PCT, max(dt_tp_base, _ATR_TP_MULT * (
            sum(abs(prices[i] - prices[i-1]) for i in range(-_ATR_PERIOD, 0)) / _ATR_PERIOD / prices[-1] * 100
            if len(prices) >= _ATR_PERIOD + 1 else dt_tp_base
        ))) if _ENABLE_ATR_TP else dt_tp_base

    effective_cooldown = dt_cooldown_sec if dt_enabled else _TRADE_COOLDOWN_SEC
    effective_max_losses = dt_max_losses if dt_enabled else 3
    effective_pause_sec = dt_pause_min * 60 if dt_enabled else 180 * 60

    end_ts = int(datetime.now(timezone.utc).timestamp())
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    series = {p: fetch_ohlc(p, since, end_ts, 60) for p in PAIRS}
    all_ts = sorted(set().union(*[set(v.keys()) for v in series.values()]))

    hist = {p: deque(maxlen=80) for p in PAIRS}
    signal = {p: "HOLD" for p in PAIRS}
    score = {p: 0.0 for p in PAIRS}
    price = {p: 0.0 for p in PAIRS}
    pos = {p: Position() for p in PAIRS}

    cash = initial_eur
    last_trade_ts = 0
    consecutive_losses = 0
    pause_until = 0
    closed = []
    peak_eq = initial_eur
    min_eq = initial_eur
    max_dd = 0.0
    bars_total = 0
    bars_above_initial = 0
    bars_below_initial = 0

    equity_history: List[Tuple[int, float]] = []

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

    for idx, ts in enumerate(all_ts):
        for p in PAIRS:
            px = series[p].get(ts)
            if px is None:
                continue
            price[p] = px
            hist[p].append(px)
            s, sc = strategy_signal(list(hist[p]))
            signal[p] = s
            score[p] = sc

        benchmark_score = score.get("XETHZEUR", 0.0)
        risk_on = benchmark_score >= _REGIME_MIN_SCORE

        eq_now = equity()
        # record equity history for metrics
        try:
            equity_history.append((ts, eq_now))
        except Exception:
            pass
        bars_total += 1
        if eq_now >= initial_eur:
            bars_above_initial += 1
        else:
            bars_below_initial += 1
        peak_eq = max(peak_eq, eq_now)
        min_eq = min(min_eq, eq_now)
        if peak_eq > 0:
            max_dd = max(max_dd, ((peak_eq - eq_now) / peak_eq) * 100.0)

        # Portfolio-level kill-switch: if drawdown is deep, cool off for 24h.
        if max_dd >= 18.0:
            pause_until = max(pause_until, ts + 24 * 3600)

        # exits first
        for p in PAIRS:
            position = pos[p]
            px = price.get(p, 0.0)
            if position.side == 0 or px <= 0:
                continue
            held_hours = (ts - position.entry_ts) / 3600 if position.entry_ts else 0
            if position.side == 1:
                pnl_pct = ((px - position.entry_price) / position.entry_price) * 100
            else:
                pnl_pct = ((position.entry_price - px) / position.entry_price) * 100

            tp = 1.2 if position.tag == "scalp" else (dt_tp_func(list(hist[p])) if dt_enabled else _atr_dynamic_tp(list(hist[p])))
            sl = -0.8 if position.tag == "scalp" else (dt_sl if dt_enabled else -3.0)
            max_hold_h = 6 if position.tag == "scalp" else (dt_max_hold_h if dt_enabled else 48)

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

                closed.append({"pair": p, "side": position.side, "pnl_eur": pnl_eur, "tag": position.tag})
                if pnl_eur < 0:
                    consecutive_losses += 1
                    if consecutive_losses >= effective_max_losses:
                        pause_until = max(pause_until, ts + effective_pause_sec)
                else:
                    consecutive_losses = 0
                pos[p] = Position()

        if ts - last_trade_ts < effective_cooldown:
            continue
        if ts < pause_until:
            continue

        cands = [(abs(score[p]), p) for p in PAIRS if signal[p] in ("BUY", "SELL") and pos[p].side == 0 and abs(score[p]) >= _MIN_BUY_SCORE]
        if not cands:
            continue
        _, bp = max(cands)
        s = signal[bp]
        sc = score[bp]
        px = price.get(bp, 0.0)
        if px <= 0:
            continue

        # volatility targeting proxy on benchmark history
        bench_hist = list(hist.get("XETHZEUR", []))[-20:]
        bench_vol = 0.0
        if len(bench_hist) >= 20:
            mean = float(np.mean(bench_hist))
            bench_vol = float(np.std(bench_hist) / mean * 100) if mean > 0 else 0.0
        vol_scale = 1.0 if bench_vol <= 0 else min(1.25, max(0.35, 1.6 / bench_vol))

        allocation = min(40.0, cash * 0.18) * (1.0 if risk_on else _RISK_OFF_MULT) * vol_scale
        if allocation < 8.0:
            continue

        # Fee/slippage drag gate: skip weak edges likely to be consumed by costs.
        # Round-trip drag ~= 2*fee + 2*slip (in % terms) — use base slippage for gate
        rt_cost_pct = (2 * fee_rate + 2 * (slippage_bps / 10000.0)) * 100.0
        edge_est_pct = abs(sc) * 0.11  # calibrated proxy: score->expected move
        if edge_est_pct < (rt_cost_pct * 1.25):
            continue

        # direction switch logic
        is_scalp = abs(sc) >= 28
        direction = None
        if s == "BUY" and (risk_on or is_scalp):
            direction = 1
        if s == "SELL" and ((not risk_on) or is_scalp):
            direction = -1
        if direction is None:
            continue

        # determine entry execution depending on execution_mode
        if execution_mode == 'immediate':
            slip = compute_slip_for_pair(list(hist.get(bp, [])), slippage_bps, slippage_model)
            entry_px = px * (1 + slip) if direction == 1 else px * (1 - slip)
            qty = allocation / entry_px if entry_px > 0 else 0.0
            total_fee = entry_px * qty * fee_rate
        elif execution_mode in ('twap', 'vwap'):
            # simulate TWAP/VWAP over next n slabs
            entry_px, qty, total_fee = simulate_twap_entry(bp, direction, allocation, idx, all_ts, series, hist, twap_slices, slippage_bps, slippage_model, fee_rate)
        else:
            # fallback to immediate
            slip = compute_slip_for_pair(list(hist.get(bp, [])), slippage_bps, slippage_model)
            entry_px = px * (1 + slip) if direction == 1 else px * (1 - slip)
            qty = allocation / entry_px if entry_px > 0 else 0.0
            total_fee = entry_px * qty * fee_rate

        if qty <= 0:
            continue

        if direction == 1:
            cash_required = allocation + total_fee
            if cash_required > cash:
                continue
            cash -= cash_required
        else:
            # reserve short notional from cash (conservative margin model)
            if allocation > cash:
                continue
            cash -= allocation

        pos[bp] = Position(side=direction, qty=qty, entry_price=entry_px, entry_ts=ts, tag=("scalp" if is_scalp else "swing"))
        last_trade_ts = ts

    # liquidate at end
    for p in PAIRS:
        position = pos[p]
        px = price.get(p, 0.0)
        if position.side == 0 or px <= 0:
            continue
        slip = compute_slip_for_pair(list(hist.get(p, [])), slippage_bps, slippage_model)
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
        closed.append({"pair": p, "side": position.side, "pnl_eur": pnl_eur, "tag": position.tag})

    wins = sum(1 for x in closed if x["pnl_eur"] > 0)
    losses = sum(1 for x in closed if x["pnl_eur"] <= 0)
    pnl_sum = sum(x["pnl_eur"] for x in closed)

    by_pair = defaultdict(float)
    for x in closed:
        by_pair[x["pair"]] += x["pnl_eur"]

    above_pct = (bars_above_initial / bars_total * 100.0) if bars_total else 0.0
    below_pct = (bars_below_initial / bars_total * 100.0) if bars_total else 0.0

    # compute additional metrics: sharpe, calmar, longest drawdown duration and recovery
    eq_series = [v for (_ts, v) in equity_history]
    returns = []
    for i in range(1, len(eq_series)):
        prev = eq_series[i-1]
        cur = eq_series[i]
        if prev > 0:
            returns.append((cur / prev) - 1.0)
    period_hours = 1.0
    annual_factor = (24.0 * 365.0) / period_hours
    mean_ret = float(np.mean(returns)) if returns else 0.0
    std_ret = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    sharpe = (mean_ret / std_ret) * (annual_factor ** 0.5) if std_ret > 0 else None
    ann_return = ((cash / initial_eur) ** (annual_factor / max(1.0, len(eq_series)))) - 1.0 if len(eq_series) > 0 else 0.0
    calmar = None
    if max_dd > 0:
        calmar = ann_return / (max_dd / 100.0) if max_dd > 0 else None

    # drawdown duration & recovery: compute longest drawdown time from peak to trough and time to recover to that peak
    longest_dd_seconds = 0
    recovery_seconds = None
    if equity_history:
        peak_ts, peak_val = equity_history[0]
        trough_ts, trough_val = peak_ts, peak_val
        for ts, val in equity_history:
            if val > peak_val:
                # recovered to new peak
                peak_ts, peak_val = ts, val
                trough_ts, trough_val = ts, val
            if val < trough_val:
                trough_ts, trough_val = ts, val
            if peak_val > 0:
                dd = (peak_val - val) / peak_val
                # if this is the largest dd so far, record duration
                if dd * 100.0 >= max_dd:
                    # duration from peak to this trough
                    longest_dd_seconds = max(longest_dd_seconds, ts - peak_ts)
        # try to find recovery time: first time after trough when equity >= previous peak
        for i in range(len(equity_history)):
            ts, val = equity_history[i]
            if val < peak_val:
                # find next time val >= peak_val
                for j in range(i+1, len(equity_history)):
                    ts2, val2 = equity_history[j]
                    if val2 >= peak_val:
                        recovery_seconds = ts2 - ts
                        break
                if recovery_seconds:
                    break
        longest_dd_hours = longest_dd_seconds / 3600.0
    else:
        longest_dd_hours = 0.0

    result = {
        "period_days": days,
        "initial_eur": round(initial_eur, 2),
        "final_eur": round(cash, 2),
        "return_pct": round((cash - initial_eur) / initial_eur * 100, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "peak_equity_eur": round(peak_eq, 2),
        "min_equity_eur": round(min_eq, 2),
        "time_above_initial_pct": round(above_pct, 2),
        "time_below_initial_pct": round(below_pct, 2),
        "data_points": {k: len(v) for k, v in series.items()},
        "closed_trades": len(closed),
        "wins": wins,
        "losses": losses,
        "winrate_pct": round((wins / len(closed) * 100), 2) if closed else 0.0,
        "net_pnl_eur": round(pnl_sum, 2),
        "by_pair_pnl": {k: round(v, 2) for k, v in sorted(by_pair.items())},
        "assumptions": {
            "fee_rate": fee_rate,
            "slippage_bps": slippage_bps,
            "mode": "daytrading" if dt_enabled else "research-estimator-long-short-scalp",
        },
        "metrics": {
            "sharpe": round(sharpe, 3) if sharpe is not None else None,
            "calmar": round(calmar, 3) if calmar is not None else None,
            "annual_return_pct": round(ann_return * 100.0, 2),
            "longest_drawdown_hours": round(longest_dd_hours, 2),
            "drawdown_recovery_seconds": int(recovery_seconds) if recovery_seconds else None,
        },
    }
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--initial", type=float, default=200.0)
    ap.add_argument("--fee", type=float, default=0.0026)
    ap.add_argument("--slippage-bps", type=float, default=8.0)
    ap.add_argument("--execution-mode", choices=["immediate","twap","vwap"], default="immediate")
    ap.add_argument("--twap-slices", type=int, default=3)
    ap.add_argument("--slippage-model", choices=["fixed","volatility"], default="fixed")
    ap.add_argument("--daytrading", action="store_true", help="Enable daytrading mode (short holds, tight SL/TP, faster cooldowns)")
    ap.add_argument("--out", type=str, default="reports/v3_backtest_detailed.json")
    args = ap.parse_args()

    result = run_backtest(args.days, args.initial, args.fee, args.slippage_bps, execution_mode=args.execution_mode, twap_slices=args.twap_slices, slippage_model=args.slippage_model, daytrading=args.daytrading)
    print(json.dumps(result, indent=2))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
