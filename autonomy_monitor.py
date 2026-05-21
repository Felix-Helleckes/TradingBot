#!/usr/bin/env python3
import time, json, os
from datetime import datetime, timedelta
from decimal import Decimal

BASE='/home/felix/tradingbot'
TRADE_LOG=os.path.join(BASE,'logs','trade_events.jsonl')
MON_LOG=os.path.join(BASE,'auto_monitor.log')
PAUSE_FILE=os.path.join(BASE,'PAUSE')

# Defaults (as agreed)
TRADE_AMOUNT_EUR=5.0
MAX_DAILY_LOSS_EUR=10.0
HARD_STOP_BALANCE_EUR=50.0
MAX_CONSECUTIVE_LOSSES=3
CHECK_INTERVAL=60

def log(msg):
    ts=datetime.utcnow().isoformat()
    line=f"{ts} {msg}\n"
    with open(MON_LOG,'a') as f:
        f.write(line)
    print(line, end='')

def read_trades():
    if not os.path.exists(TRADE_LOG):
        return []
    with open(TRADE_LOG,'r') as f:
        lines=f.read().splitlines()
    trades=[]
    for l in lines:
        if not l.strip():
            continue
        try:
            obj=json.loads(l)
            trades.append(obj)
        except Exception:
            continue
    return trades

def last_balance(trades):
    for obj in reversed(trades):
        bal=obj.get('balance_eur')
        if bal is not None:
            try:
                return Decimal(str(bal))
            except:
                continue
    return None


def sum_pnl_last_24h(trades):
    cutoff=datetime.utcnow()-timedelta(hours=24)
    s=Decimal('0')
    for obj in trades:
        ts=obj.get('ts')
        if not ts:
            continue
        try:
            t=datetime.fromisoformat(ts)
        except Exception:
            continue
        if t>=cutoff and obj.get('pnl_eur') is not None:
            try:
                s+=Decimal(str(obj.get('pnl_eur')))
            except:
                pass
    return s

def consecutive_losses(trades):
    count=0
    for obj in reversed(trades):
        if obj.get('type')!='SELL':
            continue
        pnl=obj.get('pnl_eur')
        try:
            pnlD=Decimal(str(pnl))
        except:
            pnlD=Decimal('0')
        if pnlD<0:
            count+=1
        else:
            break
    return count


def create_pause(reason):
    try:
        with open(PAUSE_FILE,'w') as f:
            f.write('PAUSE: '+reason+'\n')
        log(f'PAUSE created: {reason}')
    except Exception as e:
        log(f'ERROR creating PAUSE: {e}')

if __name__=='__main__':
    log('Autonomy monitor started')
    while True:
        try:
            trades=read_trades()
            bal=last_balance(trades)
            if bal is not None:
                log(f'Last balance: {bal} EUR')
                if bal<=Decimal(str(HARD_STOP_BALANCE_EUR)):
                    create_pause(f'balance_below_{HARD_STOP_BALANCE_EUR}')
                    break
            daily_sum=sum_pnl_last_24h(trades)
            log(f'PnL last 24h: {daily_sum} EUR')
            if daily_sum<=Decimal(str(-MAX_DAILY_LOSS_EUR)):
                create_pause(f'daily_loss_exceeded_{MAX_DAILY_LOSS_EUR}')
                break
            cons=consecutive_losses(trades)
            log(f'Consecutive sell losses: {cons}')
            if cons>=MAX_CONSECUTIVE_LOSSES:
                create_pause(f'consecutive_losses_{cons}')
                break
        except Exception as e:
            log('Monitor error: '+str(e))
        time.sleep(CHECK_INTERVAL)
    log('Monitor exiting')
