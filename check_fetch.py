import os
os.environ['USE_LOCAL_TS'] = '1'
os.environ['BT_COVERAGE_THRESHOLD'] = '0'
import sys
sys.path.insert(0, 'scripts')
import backtest_v3_detailed as bt
print('USE_LOCAL_TS:', bt.USE_LOCAL_TS)
print('BT_COVERAGE_THRESHOLD:', bt._COVERAGE_THRESHOLD)
print('CACHE_DIR:', bt.CACHE_DIR)
print('MENTOR_CACHE_DIR:', bt.MENTOR_CACHE_DIR)
print('LOCAL_TS_DIR:', bt.LOCAL_TS_DIR)
print('LOCAL_TS_DIR exists:', bt.LOCAL_TS_DIR.exists())
pair = 'XXBTZEUR'
since = 1780747959
end = 1780834359
interval = 60
print('Calling fetch_ohlc')
result = bt.fetch_ohlc(pair, since, end, interval)
print('Result length:', len(result))
if len(result):
    print('First 5:', list(result.items())[:5])
else:
    print('Empty')
    # check cache
    cache_path = bt.CACHE_DIR / f"{pair}_{since}_{end}_{interval}m.json"
    print('Cache path exists:', cache_path.exists())
    if cache_path.exists():
        print('Cache content:', cache_path.read_text()[:200])
    # check mentor
    if bt.MENTOR_CACHE_DIR.exists():
        print('Mentor cache exists')
        candidates = list(bt.MENTOR_CACHE_DIR.glob(f"{pair}_*_60m.json"))
        print('Mentor candidates:', len(candidates))
    # check local
    if bt.USE_LOCAL_TS:
        print('Calling load_local_timesales_ohlc')
        local = bt.load_local_timesales_ohlc(pair, since, end, interval)
        print('Local length:', len(local))
        if len(local):
            print('Local first 5:', list(local.items())[:5])
