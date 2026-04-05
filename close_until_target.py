#!/usr/bin/env python3
"""
Close profitable short positions (>= threshold %) in order of efficiency (pnl / margin)
until EUR wallet (ZEUR) >= target_eur or no more eligible positions.
Runs while bot may be active; performs pre-flight tradebalance checks before each close.
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

TARGET_EUR = 400.0
MIN_PROFIT_PCT = 3.0
FEE_RATE = 0.002
REQUIRED_PCT = MIN_PROFIT_PCT + (FEE_RATE*100)
SLEEP_BETWEEN=1.0
LOG= _HERE / 'logs' / 'close_until_target.log'

def log(msg):
    ts=time.strftime('%Y-%m-%d %H:%M:%S')
    line=f"{ts} - {msg}\n"
    with open(LOG,'a') as f:
        f.write(line)
    print(line,end='')


def trade_balance():
    r=api.query_private('TradeBalance')
    if r.get('error'):
        raise RuntimeError(str(r.get('error')))
    return r['result']


def open_positions():
    r=api.query_private('OpenPositions')
    if r.get('error'):
        raise RuntimeError(str(r.get('error')))
    return r['result']


def tickers_for(pairs):
    if not pairs:
        return {}
    r=api.query_public('Ticker', {'pair':','.join(pairs)})
    if r.get('error'):
        raise RuntimeError(str(r.get('error')))
    return r['result']


def compute_candidates():
    pos=open_positions()
    pairs=set(p.get('pair') for p in pos.values())
    ticks=tickers_for(pairs)
    cand=[]
    for pid,p in pos.items():
        if p.get('type')!='sell':
            continue
        vol=float(p.get('vol',0))
        cost=float(p.get('cost',0))
        margin=float(p.get('margin',0))
        if vol<=0 or cost<=0:
            continue
        entry=cost/vol
        cur=None
        for k in ticks:
            if k.lower().endswith(p.get('pair').lower()):
                try:
                    cur=float(ticks[k]['c'][0])
                except:
                    cur=None
                break
        if cur is None:
            continue
        pnl=(entry-cur)*vol
        pnl_pct=(pnl/cost)*100.0 if cost>0 else 0.0
        if pnl_pct>=REQUIRED_PCT and cost>=1.0:
            efficiency = pnl / margin if margin>0 else pnl
            cand.append({'id':pid,'pair':p.get('pair'),'vol':vol,'entry':entry,'cur':cur,'pnl':pnl,'pnl_pct':pnl_pct,'margin':margin,'cost':cost,'eff':efficiency})
    cand.sort(key=lambda x: x['eff'], reverse=True)
    return cand


def close_market(pair, vol):
    params={'pair': pair, 'type':'buy', 'ordertype':'market', 'volume': str(vol)}
    with acquire_order_lock(timeout_seconds=5.0) as locked:
        if not locked:
            return {'error': ['order lock busy']}
        return api.query_private('AddOrder', params)


def eur_wallet():
    tb=trade_balance()
    # ZEUR may be in different keys (zeur / e / eb)
    for k in ['zeur','e','eb']:
        if k in tb:
            try:
                return float(tb[k])
            except:
                pass
    return None


def main():
    log('Starting close-until-target run')
    try:
        current_eur=eur_wallet()
    except Exception as e:
        log(f'Failed to read tradebalance: {e}')
        return 1
    log(f'Current EUR wallet: {current_eur}')
    if current_eur is not None and current_eur>=TARGET_EUR:
        log('Target already reached; nothing to do')
        return 0

    candidates=compute_candidates()
    log(f'Found {len(candidates)} eligible candidates (pnl_pct >= {REQUIRED_PCT:.2f}%)')
    idx=0
    results=[]
    while current_eur is not None and current_eur<TARGET_EUR and idx<len(candidates):
        c=candidates[idx]
        log(f"Trying candidate {c['id']} {c['pair']} pnl={c['pnl']:.2f} pnl%={c['pnl_pct']:.2f} margin={c['margin']:.2f} eff={c['eff']:.4f}")
        # pre-flight check: ensure at least some free margin exists (mf > 0)
        tb=trade_balance()
        mf=float(tb.get('mf',0))
        log(f"Free margin (mf)={mf}")
        if mf <= 0:
            log('No free margin available; cannot place close order safely. Trying next candidate.')
            idx+=1
            time.sleep(SLEEP_BETWEEN)
            continue
        # attempt close
        try:
            resp=close_market(c['pair'], c['vol'])
            log(f'API resp: {resp}')
            results.append({'cand':c,'resp':resp})
        except Exception as e:
            log(f'Exception when closing: {e}')
        time.sleep(SLEEP_BETWEEN)
        try:
            current_eur=eur_wallet()
        except Exception:
            current_eur=None
        log(f'New EUR wallet: {current_eur}')
        idx+=1
    log('Finished run; writing results')
    out= _HERE / 'logs' / 'close_until_target_results.json'
    with open(out+'.tmp','w') as f:
        json.dump(results,f,indent=2)
    os.replace(out+'.tmp', out)
    log('Done')
    return 0

if __name__=='__main__':
    main()
