#!/usr/bin/env python3
import os
import re
import json
import sys
from urllib.parse import urlencode
from urllib.request import urlopen, Request

BOT_ACTIVITY='logs/bot_activity.log'
TRADE_EVENTS='logs/trade_events.jsonl'

TOKEN=os.environ.get('TELEGRAM_TOKEN')
CHAT=os.environ.get('TELEGRAM_CHAT_ID') or os.environ.get('TELEGRAM_CHAT')
if not TOKEN or not CHAT:
    print('Telegram token or chat id missing in environment')
    sys.exit(1)

def tail(path, n=500):
    try:
        with open(path,'rb') as f:
            f.seek(0,2)
            size=f.tell()
            block=1024
            data=b''
            while size>0 and data.count(b'\n')<=n:
                read_size=min(block,size)
                size-=read_size
                f.seek(size)
                data = f.read(read_size)+data
            return data.decode(errors='ignore').splitlines()[-n:]
    except Exception as e:
        return []

lines = tail(BOT_ACTIVITY, 500)
text='*Trading‑Bot Update*\n'
# find last status line with Bal: and TotalPnL
status=None
pat=re.compile(r'Bal: *([0-9\.]+)EUR.*TotalPnL: *([-0-9\.]+)EUR')
for l in reversed(lines):
    m=pat.search(l)
    if m:
        bal=m.group(1)
        pnl=m.group(2)
        status=(bal,pnl)
        break
if status:
    text+=f"Balance: *{status[0]} EUR*\nTotalPnL: *{status[1]} EUR*\n"
else:
    text+="Balance: unknown\n"

# last trades
trade_lines = tail(TRADE_EVENTS, 20)
trades=[]
for l in trade_lines:
    try:
        obj=json.loads(l)
        trades.append(obj)
    except:
        continue
if trades:
    text+="\n_Last trades:_\n"
    for t in trades[-5:]:
        ts=t.get('ts','?')
        typ=t.get('type','?')
        pair=t.get('pair','?')
        price=t.get('price')
        pnl=t.get('pnl_eur')
        text+=f"{ts} {typ} {pair} price={price} pnl={pnl} EUR\n"
else:
    text+="\nNo recent trades found.\n"

# quick checks
text+="\n_Monitor rules:_ hard stop ≤50 EUR, daily loss ≤10 EUR, pause on ≥3 loss sells.\n"

# send via telegram
url=f'https://api.telegram.org/bot{TOKEN}/sendMessage'
post = urlencode({'chat_id': CHAT, 'text': text, 'parse_mode':'Markdown'})
req = Request(url, data=post.encode())
try:
    resp = urlopen(req, timeout=15)
    out = resp.read().decode()
    print('Message sent')
except Exception as e:
    print('Failed to send message:', e)
    sys.exit(2)
