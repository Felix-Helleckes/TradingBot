import os
os.environ['USE_LOCAL_TS'] = '1'
os.environ['BT_COVERAGE_THRESHOLD'] = '0'
import sys
sys.path.insert(0, 'scripts')
from backtest_v3_detailed import fetch_ohlc, PAIRS
import time
from datetime import datetime, timezone, timedelta

end_ts = int(datetime.now(timezone.utc).timestamp())
since = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
print(f"end_ts: {end_ts} ({datetime.fromtimestamp(end_ts, tz=timezone.utc)})")
print(f"since:  {since}  ({datetime.fromtimestamp(since, tz=timezone.utc)})")
print(f"PAIRS: {PAIRS}")
series = {}
for p in PAIRS:
    print(f"Fetching for {p}...")
    result = fetch_ohlc(p, since, end_ts, 60)
    print(f"  -> length: {len(result)}")
    if len(result):
        print(f"    first: {list(result.items())[0]}")
        print(f"    last:  {list(result.items())[-1]}")
    series[p] = result
print(f"Series dict: { {k: len(v) for k, v in series.items()} }")
