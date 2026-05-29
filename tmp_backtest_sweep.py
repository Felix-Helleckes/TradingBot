#!/usr/bin/env python3
import json, os, datetime

BASE = '/mnt/fritz_nas/Volume/kraken/2026'
SYMBOLS = ['XXRPZEUR','XETHZEUR','ADAEUR','DOTEUR']
OUT = 'reports/sweep_90d.json'

# helper to read ohlc file (prefer 15m else 5m)
def load_ohlc(symbol):
    d = os.path.join(BASE, symbol)
    if not os.path.isdir(d):
        return None
    # prefer ohlc_15m.csv
    for fname in ['ohlc_15m.csv','ohlc_5m.csv','ohlc_60m.csv']:
        path = os.path.join(d, fname)
        if os.path.isfile(path):
            return path
    return None

# parse csv into list of candles (ts,open,high,low,close,volume)
def parse_csv(path):
    rows = []
    with open(path,'r') as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split(',')]
    for ln in lines[1:]:
        parts = ln.split(',')
        if len(parts) < 5:
            continue
        rec = dict(zip(header, parts))
        try:
            ts = int(rec.get('ts','0'))
            o = float(rec.get('open',''))
            h = float(rec.get('high',''))
            l = float(rec.get('low',''))
            c = float(rec.get('close',''))
        except:
            continue
        vol = None
        try:
            vol = float(rec.get('volume',''))
        except:
            vol = None
        rows.append({'ts':ts,'dt':datetime.datetime.utcfromtimestamp(ts),'open':o,'high':h,'low':l,'close':c,'volume':vol})
    return rows

# aggregate 5m->15m if needed
from collections import defaultdict

def to_15m(rows, src_minutes):
    if src_minutes == 15:
        return rows
    buckets = defaultdict(list)
    period = src_minutes*60
    for r in rows:
        k = (r['ts']//900)*900
        buckets[k].append(r)
    agg = []
    for k in sorted(buckets.keys()):
        group = buckets[k]
        opens = [g['open'] for g in group]
        highs = [g['high'] for g in group]
        lows = [g['low'] for g in group]
        closes = [g['close'] for g in group]
        vols = [g['volume'] for g in group if g['volume'] is not None]
        agg.append({'ts':k,'dt':datetime.datetime.utcfromtimestamp(k),'open':opens[0],'high':max(highs),'low':min(lows),'close':closes[-1],'volume':sum(vols) if vols else None})
    return agg

# EMA and backtest logic (same as tmp_backtest_xrp)
def ema(series_vals, period):
    k = 2.0/(period+1)
    out = []
    s = None
    for v in series_vals:
        if s is None:
            s = v
        else:
            s = v*k + s*(1-k)
        out.append(s)
    return out

def run_backtest_on_series(series, params):
    fast_p = 9
    slow_p = 21
    closes = [c['close'] for c in series]
    if len(closes) < slow_p+1:
        return {'error':'not_enough_bars','bars':len(closes)}
    ema_fast = ema(closes, fast_p)
    ema_slow = ema(closes, slow_p)
    in_pos = False
    entry_price = None
    entry_idx = None
    qty = 0.0
    cash = 200.0
    closed = []
    fee_rate = params['fee_rate']
    alloc_frac = params['allocation_pct']/100.0
    sl_pct = params['sl_pct']
    tp_pct = params['tp_pct']
    max_hold = 48
    for i in range(1,len(series)):
        if not in_pos and ema_fast[i] is not None and ema_slow[i] is not None and ema_fast[i]>ema_slow[i] and ema_fast[i-1]<=ema_slow[i-1]:
            entry_price = series[i]['open']*(1+0.0008)
            allocation = cash * alloc_frac
            if allocation < 1.0:
                continue
            qty = (allocation) / entry_price
            cash -= allocation
            in_pos = True
            entry_idx = i
            continue
        if in_pos:
            px_high = series[i]['high']
            px_low = series[i]['low']
            tp_price = entry_price*(1+tp_pct/100.0)
            sl_price = entry_price*(1-sl_pct/100.0)
            exit_price = None
            reason = None
            if px_high>=tp_price and px_low>sl_price:
                exit_price = min(px_high,tp_price); reason='TP'
            elif px_low<=sl_price and px_high<tp_price:
                exit_price = max(px_low,sl_price); reason='SL'
            elif px_high>=tp_price and px_low<=sl_price:
                openp = series[i]['open']
                if abs(tp_price-openp) < abs(openp-sl_price):
                    exit_price = min(px_high,tp_price); reason='TP_first'
                else:
                    exit_price = max(px_low,sl_price); reason='SL_first'
            elif i-entry_idx >= max_hold:
                exit_price = series[i]['close']; reason='TIME'
            if exit_price is not None:
                exit_price = exit_price*(1-0.0008)
                gross = (exit_price - entry_price)*qty
                fee = fee_rate*(entry_price*qty + exit_price*qty)
                net = gross - fee
                cash += exit_price*qty - fee
                closed.append({'entry_idx':entry_idx,'exit_idx':i,'entry_price':entry_price,'exit_price':exit_price,'qty':qty,'pnl':net,'reason':reason})
                in_pos=False; entry_price=None; entry_idx=None; qty=0.0
    if in_pos:
        last = series[-1]['close']
        exit_price = last*(1-0.0008)
        gross = (exit_price - entry_price)*qty
        fee = fee_rate*(entry_price*qty + exit_price*qty)
        net = gross - fee
        cash += exit_price*qty - fee
        closed.append({'entry_idx':entry_idx,'exit_idx':len(series)-1,'entry_price':entry_price,'exit_price':exit_price,'qty':qty,'pnl':net,'reason':'EOD'})
    net_pnl = cash - 200.0
    wins = [c for c in closed if c['pnl']>=0]
    losses = [c for c in closed if c['pnl']<0]
    eq_hist = [200.0]
    cur_cash = 200.0
    for c in closed:
        cur_cash += c['pnl']
        eq_hist.append(cur_cash)
    cur_peak = eq_hist[0]
    max_dd = 0.0
    for e in eq_hist:
        cur_peak = max(cur_peak,e)
        dd = (cur_peak - e)/cur_peak*100 if cur_peak>0 else 0.0
        max_dd = max(max_dd,dd)
    return {'closed_trades':len(closed),'wins':len(wins),'losses':len(losses),'winrate_pct': round(len(wins)/len(closed)*100,2) if closed else 0.0,'net_pnl_eur': round(net_pnl,4),'return_pct': round(net_pnl/200.0*100,2),'max_drawdown_pct': round(max_dd,2)}

# default params
current_cfg = {'allocation_pct': 20.0, 'sl_pct': 1.5, 'tp_pct': 1.8, 'fee_rate': 0.0026}
proposed_cfg = {'allocation_pct': 20.0, 'sl_pct': 2.5, 'tp_pct': 3.0, 'fee_rate': 0.0026}

results = {}
for s in SYMBOLS:
    path = load_ohlc(s)
    if not path:
        results[s] = {'error':'no_data'}
        continue
    rows = parse_csv(path)
    if not rows:
        results[s] = {'error':'parse_failed'}
        continue
    # detect source minutes from filename
    src_min = 5
    if '15m' in path:
        src_min = 15
    elif '60m' in path:
        src_min = 60
    series = to_15m(rows, src_min)
    # select last ~90 days if available (90*24*4 15m bars)
    target_bars = 90*24*4
    use = series[-target_bars:] if len(series) >= target_bars else series
    res_cur = run_backtest_on_series(use, current_cfg)
    res_prop = run_backtest_on_series(use, proposed_cfg)
    results[s] = {'bars_used': len(use), 'current': res_cur, 'proposed': res_prop}

os.makedirs('reports', exist_ok=True)
with open(OUT,'w') as f:
    json.dump({'generated': datetime.datetime.utcnow().isoformat() + 'Z', 'symbols': SYMBOLS, 'results': results}, f, indent=2)
print('wrote', OUT)
