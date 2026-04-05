#!/usr/bin/env python3
"""
Post aggressive limit-close orders for profitable short positions, prioritized by efficiency (pnl / margin).
Only posts limit BUYs to close shorts (type='sell').
Config: LIMIT_OFFSET_PCT (how much ABOVE current price to place buy), MAX_POST (max orders to post per run)
"""
import os, json, time
from pathlib import Path
from dotenv import load_dotenv
from krakenex import API
from order_lock import acquire_order_lock

_HERE = Path(__file__).parent
load_dotenv(_HERE / '.env')
API_KEY=os.getenv('KRAKEN_API_KEY')
API_SECRET=os.getenv('KRAKEN_API_SECRET')
api=API(API_KEY, API_SECRET)

LIMIT_OFFSET_PCT = 0.001  # 0.1% above current price for aggressive buy-to-close
MAX_POST = 12
MIN_COST_EUR = 1.0  # skip very tiny positions

LOG= _HERE / 'logs' / 'post_limit_closes.log'

def log(msg):
    ts=time.strftime('%Y-%m-%d %H:%M:%S')
    line=f"{ts} - {msg}\n"
    print(line,end='')
    with open(LOG,'a') as f:
        f.write(line)


def safe_query_private(cmd, params=None):
    try:
        if params is None:
            r = api.query_private(cmd, {})
        else:
            r = api.query_private(cmd, params)
        return r
    except Exception as e:
        return {'error':[str(e)]}


def main():
    log('Starting post_limit_closes run')
    op = safe_query_private('OpenPositions')
    if op.get('error'):
        log(f'OpenPositions error: {op.get("error")}')
        return 1
    positions = op.get('result',{})
    if not positions:
        log('No open positions')
        return 0

    pairs=set(p.get('pair') for p in positions.values())
    pair_str = ','.join(pairs) if pairs else ''
    tick = {}
    if pair_str:
        pub = api.query_public('Ticker', {'pair': pair_str})
        if pub.get('error'):
            log(f'Ticker error: {pub.get("error")}')
        else:
            tick = pub.get('result',{})

    candidates=[]
    for pid,p in positions.items():
        try:
            if p.get('type') != 'sell':
                continue
            vol=float(p.get('vol',0))
            cost=float(p.get('cost',0))
            margin=float(p.get('margin',0))
            if cost < MIN_COST_EUR:
                continue
            entry = cost/vol if vol>0 else None
            cur=None
            for k in tick.keys():
                if k.lower().endswith(p.get('pair').lower()):
                    try:
                        cur=float(tick[k]['c'][0])
                    except:
                        cur=None
                    break
            if entry is None or cur is None:
                continue
            pnl = (entry - cur) * vol
            pnl_pct = (pnl / cost)*100.0 if cost>0 else 0.0
            if pnl <= 0:
                continue
            eff = pnl / (margin if margin>0 else cost)
            candidates.append({'pid':pid,'pair':p.get('pair'),'vol':vol,'entry':entry,'cur':cur,'pnl':pnl,'pnl_pct':pnl_pct,'margin':margin,'cost':cost,'eff':eff})
        except Exception as e:
            log(f'Exception computing candidate {pid}: {e}')
            continue

    if not candidates:
        log('No profitable short positions found')
        return 0

    candidates.sort(key=lambda x: x['eff'], reverse=True)
    posted = []
    count=0
    for c in candidates:
        if count>=MAX_POST:
            break
        pair=c['pair']
        vol=c['vol']
        cur=c['cur']
        limit_price = max(0.0001, cur * (1.0 + LIMIT_OFFSET_PCT))
        # Kraken requires specific decimal precision per pair (EUR pairs typically up to 5 decimals)
        # Round price to 5 decimals to avoid 'Invalid price' errors
        try:
            limit_price = float(round(limit_price, 5))
        except Exception:
            limit_price = max(0.0001, limit_price)
        # place limit buy to close short
        params={'pair': pair, 'type':'buy', 'ordertype':'limit', 'price': f"{limit_price:.5f}", 'volume': str(vol)}
        log(f"Posting limit close for {c['pid']} {pair} vol={vol:.8f} cur={cur} limit={limit_price:.6f} pnl={c['pnl']:.2f} eff={c['eff']:.4f}")
        with acquire_order_lock(timeout_seconds=5.0) as locked:
            if not locked:
                log('Order lock busy; skipping this candidate to avoid race conditions')
                continue
            resp = safe_query_private('AddOrder', params)
        if resp.get('error'):
            log(f"AddOrder error: {resp.get('error')}")
        else:
            log(f"Posted limit order, result: {json.dumps(resp.get('result',{}))}")
            posted.append({'cand':c,'resp':resp.get('result',{})})
            count+=1
        time.sleep(0.8)

    out_path= _HERE / 'logs' / 'post_limit_closes_results.json'
    with open(out_path+'.tmp','w') as f:
        json.dump(posted,f,indent=2)
    os.replace(out_path+'.tmp', out_path)
    log(f'Posted {count} limit close orders')
    return 0

if __name__=='__main__':
    main()
