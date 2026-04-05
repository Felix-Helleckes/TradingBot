#!/usr/bin/env python3
"""
Close short positions automatically when unrealized profit percent >= threshold + fee
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

# Config
MIN_PROFIT_PCT = 3.0    # user-specified base percent
FEE_RATE = 0.002       # assumed taker fee (0.2%) for closing
REQUIRED_PCT = MIN_PROFIT_PCT + (FEE_RATE*100)
MIN_NOTIONAL_EUR = 1.0 # don't attempt to close tiny positions

LOG_PATH = _HERE / 'logs' / 'close_on_profit.log'

def log(msg):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f"{ts} - {msg}\n"
    with open(LOG_PATH,'a') as f:
        f.write(line)
    print(line, end='')


def fetch_open_positions():
    r = api.query_private('OpenPositions')
    if r.get('error'):
        raise RuntimeError(f"OpenPositions error: {r.get('error')}")
    return r.get('result',{})


def fetch_tickers(pairs):
    if not pairs:
        return {}
    pair_str = ','.join(pairs)
    r = api.query_public('Ticker', {'pair': pair_str})
    if r.get('error'):
        raise RuntimeError(f"Ticker error: {r.get('error')}")
    return r.get('result',{})


def compute_pnl_pct(pos, tickers):
    vol = float(pos.get('vol',0))
    cost = float(pos.get('cost',0))
    typ = pos.get('type')
    pair = pos.get('pair')
    if vol <= 0 or cost <= 0:
        return None
    entry = cost/vol
    # find current price
    cur = None
    for k in tickers.keys():
        if k.lower().endswith(pair.lower()):
            try:
                cur = float(tickers[k]['c'][0])
            except:
                cur = None
            break
    if cur is None:
        return None
    if typ == 'sell':
        pnl = (entry - cur) * vol
    else:
        pnl = (cur - entry) * vol
    pnl_pct = (pnl / cost) * 100.0
    return pnl_pct, pnl, cost, vol, entry, cur


def close_position(pair, vol):
    # market buy to close short
    params = {'pair': pair, 'type': 'buy', 'ordertype': 'market', 'volume': str(vol)}
    with acquire_order_lock(timeout_seconds=5.0) as locked:
        if not locked:
            return {'error': ['order lock busy']}
        r = api.query_private('AddOrder', params)
    return r


def main():
    try:
        positions = fetch_open_positions()
    except Exception as e:
        log(f"ERROR fetching positions: {e}")
        return 1
    pairs = set(p.get('pair') for p in positions.values())
    try:
        tickers = fetch_tickers(pairs)
    except Exception as e:
        log(f"ERROR fetching tickers: {e}")
        tickers = {}

    to_close = []
    for pid, pos in positions.items():
        typ = pos.get('type')
        if typ != 'sell':
            continue
        res = compute_pnl_pct(pos, tickers)
        if not res:
            continue
        pnl_pct, pnl, cost, vol, entry, cur = res
        log(f"Pos {pid} {pos.get('pair')} vol={vol:.8f} entry={entry:.6f} cur={cur} pnl_pct={pnl_pct:.3f} pnl={pnl:.2f}")
        if cost < MIN_NOTIONAL_EUR:
            continue
        if pnl_pct >= REQUIRED_PCT:
            to_close.append((pid, pos.get('pair'), vol, pnl, pnl_pct))

    if not to_close:
        log('No positions meet profit threshold')
        return 0

    log(f"Closing {len(to_close)} positions meeting threshold ({REQUIRED_PCT:.2f}%)")
    results = []
    for pid,pair,vol,pnl,pct in to_close:
        log(f"Attempting close {pid} {pair} vol={vol:.8f} pnl_pct={pct:.3f}")
        try:
            r = close_position(pair, vol)
            results.append({'pid':pid,'pair':pair,'vol':vol,'resp':r})
            log(f"API resp: {r}")
        except Exception as e:
            log(f"Exception closing {pid}: {e}")
        time.sleep(1.0)

    # write results
    out = _HERE / 'logs' / 'close_on_profit_results.json'
    with open(out+'.tmp','w') as f:
        json.dump(results, f, indent=2)
    os.replace(out+'.tmp', out)
    log('Done')
    return 0

if __name__ == '__main__':
    main()
