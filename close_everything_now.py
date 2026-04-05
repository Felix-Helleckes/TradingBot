#!/usr/bin/env python3
"""
Close every open position immediately (market where possible).
Strategy:
 - First close long positions (type != 'sell') with market SELL to free margin.
 - Then attempt to close short positions (type == 'sell') with market BUY.
 - If an AddOrder fails due to insufficient funds, try partial close by scaling volume down to min_auto_scale_notional (from config.toml) in steps.
 - Log results to logs/close_everything_results.json and logs/close_everything.log

WARNING: This will place live market/limit orders on Kraken. Use only when you want to liquidate.
"""
import time, json, os
from pathlib import Path
from dotenv import load_dotenv
from krakenex import API
import toml
from order_lock import acquire_order_lock

_HERE = Path(__file__).parent
load_dotenv(_HERE / '.env')
API_KEY=os.getenv('KRAKEN_API_KEY')
API_SECRET=os.getenv('KRAKEN_API_SECRET')
api=API(API_KEY, API_SECRET)

CFG_PATH = _HERE / 'config.toml'
LOG = _HERE / 'logs' / 'close_everything.log'
OUT = _HERE / 'logs' / 'close_everything_results.json'

def log(msg):
    ts=time.strftime('%Y-%m-%d %H:%M:%S')
    line=f"{ts} - {msg}\n"
    with open(LOG,'a') as f:
        f.write(line)
    print(line,end='')

# load config for min_auto_scale_notional
try:
    cfg = toml.load(CFG_PATH)
    risk = cfg.get('risk_management',{})
    min_auto = float(risk.get('min_auto_scale_notional', 0.2))
except Exception:
    min_auto = 0.2

# helper
def query_private(cmd, params=None):
    try:
        if cmd == 'AddOrder':
            with acquire_order_lock(timeout_seconds=5.0) as locked:
                if not locked:
                    return {'error': ['order lock busy']}
                if params:
                    return api.query_private(cmd, params)
                return api.query_private(cmd, {})
        if params:
            return api.query_private(cmd, params)
        return api.query_private(cmd, {})
    except Exception as e:
        return {'error':[str(e)]}

log('Starting full liquidation run (close_everything_now)')
# fetch open positions
resp = query_private('OpenPositions')
if resp.get('error'):
    log(f'OpenPositions error: {resp.get("error")}')
    raise SystemExit(1)
positions = resp.get('result',{})
if not positions:
    log('No open positions to close')
    open_positions = []
else:
    open_positions = []
    for pid,p in positions.items():
        p['pid']=pid
        open_positions.append(p)

# fetch tickers
pairs = set(p.get('pair') for p in open_positions)
pair_str = ','.join(pairs) if pairs else ''
if pair_str:
    r = api.query_public('Ticker', {'pair': pair_str})
    tickers = r.get('result',{}) if not r.get('error') else {}
else:
    tickers={}

# partition
longs=[]
shorts=[]
for p in open_positions:
    if p.get('type')=='sell':
        shorts.append(p)
    else:
        longs.append(p)

log(f'Found {len(longs)} long(s) and {len(shorts)} short(s) to close')
results=[]

# function to get current price
def get_price_for_pair(pair):
    for k in tickers.keys():
        if k.lower().endswith(pair.lower()):
            try:
                return float(tickers[k]['c'][0])
            except:
                return None
    # fallback: public ticker single
    try:
        r=api.query_public('Ticker',{'pair':pair})
        if not r.get('error'):
            return float(r.get('result',{}).get(next(iter(r.get('result'))))['c'][0])
    except Exception:
        return None
    return None

# close longs first (sell)
for p in longs:
    pid=p.get('pid')
    pair=p.get('pair')
    vol=float(p.get('vol',0))
    price = get_price_for_pair(pair)
    log(f'Attempting to close LONG {pid} {pair} vol={vol} (market SELL)')
    if vol<=0:
        log('Zero volume, skipping')
        continue
    params={'pair':pair,'type':'sell','ordertype':'market','volume':str(vol)}
    r=query_private('AddOrder', params)
    if r.get('error'):
        log(f'AddOrder error for LONG {pid}: {r.get("error")}')
        # try partial scale down progressively
        scaled=vol
        while scaled>=min_auto:
            scaled = round(scaled/2,8)
            if scaled < min_auto:
                break
            log(f'Trying partial SELL vol={scaled}')
            params['volume']=str(scaled)
            r=query_private('AddOrder', params)
            if not r.get('error'):
                log(f'Partial SELL succeeded for {pid} vol={scaled} -> {r.get("result")}')
                results.append({'pid':pid,'side':'long','action':'partial_sell','vol':scaled,'resp':r})
                break
            else:
                log(f'Partial SELL failed: {r.get("error")}')
        else:
            log(f'Could not close LONG {pid}')
            results.append({'pid':pid,'side':'long','action':'failed','error':r.get('error')})
    else:
        log(f'Closed LONG {pid} successfully -> {r.get("result")}')
        results.append({'pid':pid,'side':'long','action':'closed','resp':r})
    time.sleep(1.0)

# refresh tradebalance/tickers
try:
    tb = query_private('TradeBalance')
    if not tb.get('error'):
        tickers_update = api.query_public('Ticker', {'pair': pair_str}).get('result',{}) if pair_str else {}
        tickers.update(tickers_update)
except Exception:
    pass

# attempt to close shorts (buy)
for p in sorted(shorts, key=lambda x: float(x.get('cost',0))):
    pid=p.get('pid')
    pair=p.get('pair')
    vol=float(p.get('vol',0))
    price = get_price_for_pair(pair)
    log(f'Attempting to close SHORT {pid} {pair} vol={vol} (market BUY)')
    if vol<=0:
        log('Zero volume, skipping')
        continue
    params={'pair':pair,'type':'buy','ordertype':'market','volume':str(vol)}
    r=query_private('AddOrder', params)
    if r.get('error'):
        log(f'AddOrder error for SHORT {pid}: {r.get("error")}')
        # try partial close scaling down
        scaled=vol
        while scaled>=min_auto:
            scaled = round(scaled/2,8)
            if scaled < min_auto:
                break
            log(f'Trying partial BUY vol={scaled}')
            params['volume']=str(scaled)
            r=query_private('AddOrder', params)
            if not r.get('error'):
                log(f'Partial BUY succeeded for {pid} vol={scaled} -> {r.get("result")}')
                results.append({'pid':pid,'side':'short','action':'partial_buy','vol':scaled,'resp':r})
                break
            else:
                log(f'Partial BUY failed: {r.get("error")}')
        else:
            log(f'Could not close SHORT {pid}')
            results.append({'pid':pid,'side':'short','action':'failed','error':r.get('error')})
    else:
        log(f'Closed SHORT {pid} successfully -> {r.get("result")}')
        results.append({'pid':pid,'side':'short','action':'closed','resp':r})
    time.sleep(1.0)

# write results
with open(OUT+'.tmp','w') as f:
    json.dump(results,f,indent=2)
os.replace(OUT+'.tmp', OUT)
log('Finished liquidation run')
