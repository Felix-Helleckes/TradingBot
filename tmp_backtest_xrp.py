#!/usr/bin/env python3
import csv, json, datetime, sys

# simple toml parser for needed keys

def get_cfg_val(cfg_text, section, key, default=None):
    sec = '[' + section + ']'
    if sec not in cfg_text:
        return default
    s = cfg_text.split(sec,1)[1]
    for ln in s.splitlines():
        ln2 = ln.strip()
        if ln2.startswith('['):
            break
        if ln2.startswith(key):
            return ln2.split('=',1)[1].strip().strip('\"').strip()
    return default

# load config
with open('config.toml','r') as f:
    cfg_text = f.read()
allocation = float(get_cfg_val(cfg_text,'bot_settings','allocation_per_trade_percent','20.0'))
intraday_sl = float(get_cfg_val(cfg_text,'daytrading','intraday_sl_percent','1.5'))
intraday_tp = float(get_cfg_val(cfg_text,'daytrading','intraday_tp_percent','1.8'))
fee_taker = float(get_cfg_val(cfg_text,'risk_management','fees_taker_percent','0.26'))

csv_path = '/mnt/fritz_nas/Volume/kraken/2026/XXRPZEUR/ohlc_5m.csv'
try:
    with open(csv_path,'r') as f:
        lines = [ln.strip() for ln in f.readlines() if ln.strip()]
except Exception as e:
    print(json.dumps({'error':'ohlc_missing','msg':str(e)}))
    sys.exit(0)

header = [h.strip() for h in lines[0].split(',')]
rows = []
for ln in lines[1:]:
    parts = ln.split(',')
    if len(parts) < 5:
        continue
    rec = dict(zip(header, parts))
    try:
        ts = int(rec.get('ts','0'))
    except:
        continue
    try:
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

if not rows:
    print(json.dumps({'error':'no_rows'}))
    sys.exit(0)

# aggregate to 15m
from collections import defaultdict
buckets = defaultdict(list)
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
if not agg:
    print(json.dumps({'error':'no_agg'}))
    sys.exit(0)

series = agg[-14*24*4:] if len(agg) >= 14*24*4 else agg

# EMA
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

# backtest function

def run_backtest(params):
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

params_current = {'allocation_pct': allocation, 'sl_pct': intraday_sl, 'tp_pct': intraday_tp, 'fee_rate': fee_taker/100.0}
params_proposed = {'allocation_pct': 20.0, 'sl_pct': 2.5, 'tp_pct': 3.0, 'fee_rate': fee_taker/100.0}

res_current = run_backtest(params_current)
res_proposed = run_backtest(params_proposed)

out = {'series_bars': len(series), 'current_params': params_current, 'proposed_params': params_proposed, 'results': {'current': res_current, 'proposed': res_proposed}}
print(json.dumps(out))
