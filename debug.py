import os
os.environ['USE_LOCAL_TS'] = '1'
os.environ['BT_COVERAGE_THRESHOLD'] = '0'
import sys
sys.path.insert(0, 'scripts')
from backtest_v3_detailed import load_local_timesales_ohlc, LOCAL_TS_DIR
print('LOCAL_TS_DIR:', LOCAL_TS_DIR)
print('Exists?', LOCAL_TS_DIR.exists())
pair = 'XXBTZEUR'
interval = 60
since = 1780660800  # 2026-06-06 00:00:00
end = 1780794000    # 2026-06-07 23:00:00
print('since', since, 'end', end)
result = load_local_timesales_ohlc(pair, since, end, interval)
print('len:', len(result))
if len(result):
    print('sample:', list(result.items())[:2])
else:
    print('empty')
    # check file
    fpath = LOCAL_TS_DIR / pair / f'ohlc_{interval}m.csv'
    print('fpath:', fpath)
    print('exists?', fpath.exists())
    if fpath.exists():
        with open(fpath, 'r') as f:
            lines = f.readlines()
            print('total lines:', len(lines))
            if len(lines) > 0:
                print('first line:', lines[0].strip())
                print('second line:', lines[1].strip() if len(lines)>1 else 'none')
                # parse first data line
                if len(lines) > 1:
                    parts = lines[1].strip().split(',')
                    print('parts:', parts)
                    if len(parts) >= 2:
                        ts = int(float(parts[0]))
                        px = float(parts[1])
                        print('ts:', ts, 'px:', px)
                        bucket = max(1, int(interval)) * 60
                        bts = (ts // bucket) * bucket
                        print('bucket:', bucket, 'bts:', bts)
