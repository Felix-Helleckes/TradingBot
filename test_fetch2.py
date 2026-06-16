import os
os.environ['USE_LOCAL_TS'] = '1'
os.environ['BT_COVERAGE_THRESHOLD'] = '0'
import sys
sys.path.insert(0, 'scripts')
from backtest_v3_detailed import fetch_ohlc
import time
# simulate what the backtest does for days=1
end_ts = int(time.time())
since = int((end_ts - 1*86400))  # 1 day ago
print('end_ts:', end_ts, 'since:', since)
pair = 'XXBTZEUR'
interval = 60
result = fetch_ohlc(pair, since, end_ts, interval)
print('Result length:', len(result))
if len(result):
    print('First 5:', list(result.items())[:5])
    print('Last 5:', list(result.items())[-5:])
else:
    print('Empty result')
    # check if cache exists
    from backtest_v3_detailed import CACHE_DIR
    cache_path = CACHE_DIR / f"{pair}_{since}_{end_ts}_{interval}m.json"
    print('Cache path:', cache_path)
    print('Cache exists?', cache_path.exists())
    if cache_path.exists():
        print('Cache content:', cache_path.read_text()[:200])
