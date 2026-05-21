#!/usr/bin/env python3
"""Trading Skill Adapter
Lightweight adapter to expose analysis/backtest/position sizing utilities
from the local tradingbot codebase as a reusable skill interface.

Usage (CLI):
  python trading_skill_adapter.py analyze --pair XXBTZEUR --interval 60 --days 30
  python trading_skill_adapter.py backtest --days 90 --out out.json
  python trading_skill_adapter.py position_size --account 1000 --risk_pct 1 --stop_pct 2

Safety: Requires user acceptance marker file .trading_skill_accepted in project root.
This adapter performs analysis and backtests only. It does NOT execute live trades.
"""

import os
import sys
import json
import re
import io
import argparse
from datetime import datetime, timedelta

# confirm acceptance
ACCEPT_FILE = os.path.join(os.path.dirname(__file__), '.trading_skill_accepted')
if not os.path.exists(ACCEPT_FILE):
    print("ERROR: legal acceptance not found. Please confirm legal.md before using the trading skill.")
    sys.exit(1)

# project imports
sys.path.insert(0, os.path.dirname(__file__))
try:
    import toml
    from trading_bot import Backtester, TechnicalAnalysis
    from kraken_interface import KrakenAPI
except Exception as e:
    print("ERROR: failed importing project modules:", e)
    raise


def analyze_pair(pair, interval=60, days=30):
    api = KrakenAPI('','')
    end_ts = int(datetime.utcnow().timestamp())
    since = int((datetime.utcnow() - timedelta(days=days)).timestamp())
    od = api.get_ohlc_data(pair, interval=interval, since=since)
    if not od:
        return {'error': 'no_data', 'pair': pair}
    # od may be dict keyed by pair
    if isinstance(od, dict) and pair in od:
        series = od[pair]
    elif isinstance(od, dict):
        series = list(od.values())[0]
    else:
        series = od
    closes = [float(c[4]) for c in series]

    ta = TechnicalAnalysis()
    ta.seed_from_ohlc(pair, closes)

    # use last close for current metrics
    metrics = {}
    prices = list(ta._get_price_history(pair))
    try:
        rsi = ta.calculate_rsi(prices)
    except Exception:
        rsi = None
    try:
        macd, sig, hist = ta.calculate_macd(prices)
    except Exception:
        macd = sig = hist = None
    try:
        fast, slow, is_bull = ta.calculate_ema_crossover(prices)
    except Exception:
        fast = slow = is_bull = None
    try:
        atr = ta.calculate_atr(pair)
    except Exception:
        atr = None

    # latest signal
    last_close = closes[-1] if closes else None
    signal, score = ta.generate_signal_with_score({pair: {'c':[last_close]}}) if last_close is not None else ("HOLD", 0)

    metrics.update({
        'pair': pair,
        'interval': interval,
        'days': days,
        'last_close': last_close,
        'signal': signal,
        'score': score,
        'rsi': rsi,
        'macd': macd,
        'macd_signal': sig,
        'macd_histogram': hist,
        'ema_fast': fast,
        'ema_slow': slow,
        'ema_bullish': is_bull,
        'atr': atr,
        'candles': len(closes),
    })
    return metrics


def run_backtest_with_cfg(overrides=None, days=90, initial=1000.0):
    # load base config
    cfg = {}
    try:
        cfg = toml.load(os.path.join(os.path.dirname(__file__), 'config.toml'))
    except Exception:
        cfg = {}
    if overrides:
        # shallow merge risk_management/backtesting
        cfg = toml.loads(toml.dumps(cfg))
        if 'risk_management' not in cfg:
            cfg['risk_management'] = {}
        cfg['risk_management'].update(overrides.get('risk_management', {}))
        if 'bot_settings' not in cfg:
            cfg['bot_settings'] = {}
        cfg['bot_settings'].update(overrides.get('bot_settings', {}))
    # set backtesting window
    if 'backtesting' not in cfg:
        cfg['backtesting'] = {}
    cfg['backtesting']['start_date'] = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
    cfg['backtesting']['initial_balance'] = initial

    bt = Backtester(KrakenAPI('',''), cfg)
    # capture stdout
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        bt.run()
        out = sys.stdout.getvalue()
    finally:
        sys.stdout = old

    # parse metrics
    d = {}
    m = re.search(r"Total Return:\s*([0-9.+-]+)%", out)
    if m: d['total_return_pct'] = float(m.group(1))
    m = re.search(r"Sharpe Ratio:\s*([0-9.+-]+)", out)
    if m: d['sharpe'] = float(m.group(1))
    m = re.search(r"Sortino Ratio:\s*([0-9.+-]+)", out)
    if m: d['sortino'] = float(m.group(1))
    m = re.search(r"Max Drawdown:\s*([0-9.+-]+)%", out)
    if m: d['max_drawdown_pct'] = float(m.group(1))
    m = re.search(r"Total Trades:\s*([0-9]+)", out)
    if m: d['total_trades'] = int(m.group(1))
    # include raw
    d['raw'] = out
    return d


def position_size(account_eur, risk_percent, stop_distance_pct, trade_amount_eur=None, min_trade_eur=10.0):
    # risk_percent as percent of account (e.g. 1 for 1%)
    risk_eur = account_eur * (risk_percent / 100.0)
    if stop_distance_pct <= 0:
        return {'error': 'stop_distance_must_be_positive'}
    # size euros = risk_eur / (stop_distance_pct/100)
    size_eur = risk_eur / (stop_distance_pct / 100.0)
    if trade_amount_eur:
        # can't exceed trade_amount_eur
        size_eur = min(size_eur, trade_amount_eur)
    size_eur = max(size_eur, min_trade_eur)
    return {'account_eur': account_eur, 'risk_eur': risk_eur, 'stop_distance_pct': stop_distance_pct, 'size_eur': round(size_eur,2)}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd')

    a = sub.add_parser('analyze')
    a.add_argument('--pair', required=True)
    a.add_argument('--interval', type=int, default=60)
    a.add_argument('--days', type=int, default=30)
    a.add_argument('--out', type=str)

    b = sub.add_parser('backtest')
    b.add_argument('--days', type=int, default=90)
    b.add_argument('--initial', type=float, default=1000.0)
    b.add_argument('--out', type=str)

    p = sub.add_parser('position_size')
    p.add_argument('--account', type=float, required=True)
    p.add_argument('--risk_pct', type=float, required=True)
    p.add_argument('--stop_pct', type=float, required=True)
    p.add_argument('--trade_amt', type=float)

    args = ap.parse_args()
    if args.cmd == 'analyze':
        res = analyze_pair(args.pair, interval=args.interval, days=args.days)
        if args.out:
            with open(args.out,'w') as f: json.dump(res,f,indent=2)
        else:
            print(json.dumps(res,indent=2))
    elif args.cmd == 'backtest':
        res = run_backtest_with_cfg(days=args.days, initial=args.initial)
        if args.out:
            with open(args.out,'w') as f: json.dump(res,f,indent=2)
        else:
            print(json.dumps(res,indent=2))
    elif args.cmd == 'position_size':
        res = position_size(args.account, args.risk_pct, args.stop_pct, trade_amount_eur=args.trade_amt)
        print(json.dumps(res,indent=2))
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
