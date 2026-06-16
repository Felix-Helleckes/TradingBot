import os
os.environ['USE_LOCAL_TS'] = '1'
os.environ['BT_COVERAGE_THRESHOLD'] = '0'
import sys
sys.path.insert(0, 'scripts')
from backtest_v3_detailed import fetch_ohlc, LOCAL_TS_DIR, CACHE_DIR, MENTOR_CACHE_DIR, USE_LOCAL_TS, _COVERAGE_THRESHOLD, _NAS_DEFAULT, _NAS_ROOT, _NAS, _BOT_CACHE, _MENTOR_CACHE_DIR
print('USE_LOCAL_TS:', USE_LOCAL_TS)
print('LOCAL_TS_DIR:', LOCAL_TS_DIR)
print('CACHE_DIR:', CACHE_DIR)
print('MENTOR_CACHE_DIR:', MENTOR_CACHE_DIR)
print('_COVERAGE_THRESHOLD:', _COVERAGE_THRESHOLD)
print('_NAS_DEFAULT:', _NAS_DEFAULT)
print('_NAS_ROOT:', _NAS_ROOT)
print('_NAS:', _NAS)
print('_BOT_CACHE:', _BOT_CACHE)
print('_MENTOR_CACHE_DIR:', _MENTOR_CACHE_DIR)
pair = 'XXBTZEUR'
since = 1780660800
end = 1780794000
interval = 60
print('Calling fetch_ohlc for', pair, 'since', since, 'end', end, 'interval', interval)
result = fetch_ohlc(pair, since, end, interval)
print('Result length:', len(result))
if len(result):
    print('First 5:', list(result.items())[:5])
else:
    print('Empty result')
