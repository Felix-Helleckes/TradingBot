import os
os.environ['USE_LOCAL_TS'] = '1'
os.environ['BT_COVERAGE_THRESHOLD'] = '0'
import sys
sys.path.insert(0, 'scripts')
import backtest_v3_detailed as bt
print('USE_LOCAL_TS:', bt.USE_LOCAL_TS)
print('BT_COVERAGE_THRESHOLD:', bt._COVERAGE_THRESHOLD if hasattr(bt, '_COVERAGE_THRESHOLD') else 'not found')
print('CACHE_DIR:', bt.CACHE_DIR)
print('MENTOR_CACHE_DIR:', bt.MENTOR_CACHE_DIR)
print('LOCAL_TS_DIR:', bt.LOCAL_TS_DIR)
pair = 'XXBTZEUR'
since = 1780747959
end = 1780834359
interval = 60
print('\\n--- Calling fetch_ohlc ---')
result = bt.fetch_ohlc(pair, since, end, interval)
print('Result length:', len(result))
if len(result):
    print('First 5:', list(result.items())[:5])
else:
    print('Empty')
    # Let's manually walk through fetch_ohlc
    print('\\n--- Manual walkthrough ---')
    from pathlib import Path
    CACHE_DIR = bt.CACHE_DIR
    MENTOR_CACHE_DIR = bt.MENTOR_CACHE_DIR
    LOCAL_TS_DIR = bt.LOCAL_TS_DIR
    _COVERAGE_THRESHOLD = bt._COVERAGE_THRESHOLD
    USE_LOCAL_TS = bt.USE_LOCAL_TS
    import json, time
    # 1. Exact-match cache
    cache_path = CACHE_DIR / f"{pair}_{since}_{end}_{interval}m.json"
    print('1. Cache path:', cache_path, 'exists?', cache_path.exists())
    if cache_path.exists():
        print('   Would return cached')
    # 2. mentor_cache_1h
    print('2. Mentor cache dir exists?', MENTOR_CACHE_DIR.exists())
    if MENTOR_CACHE_DIR.exists():
        import glob
        candidates = sorted(MENTOR_CACHE_DIR.glob(f"{pair}_*_60m.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        print('   Candidates:', len(candidates))
        if candidates:
            merged = {}
            for cp in candidates[:3]:  # just first few
                try:
                    raw = {int(k): float(v) for k, v in json.loads(cp.read_text()).items()}
                    merged.update(raw)
                except Exception as e:
                    print('   Error reading', cp, e)
            filtered = {k: v for k, v in merged.items() if since <= k <= end}
            expected_candles = (end - since) / (interval * 60)
            coverage = len(filtered) / max(1, expected_candles)
            print('   Filtered len:', len(filtered), 'expected_candles:', expected_candles, 'coverage:', coverage)
            print('   Threshold:', _COVERAGE_THRESHOLD)
            if coverage >= _COVERAGE_THRESHOLD:
                print('   Would return mentor cache')
            else:
                print('   Coverage too low')
    # 3. USE_LOCAL_TS
    print('3. USE_LOCAL_TS:', USE_LOCAL_TS)
    if USE_LOCAL_TS:
        print('   Calling load_local_timesales_ohlc')
        local = bt.load_local_timesales_ohlc(pair, since, end, interval)
        print('   Local result length:', len(local))
        if len(local):
            print('   First 5:', list(local.items())[:5])
        else:
            print('   Local empty')
    # 4. API fallback
    print('4. Would fall back to API')
