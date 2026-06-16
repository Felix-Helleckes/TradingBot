import os
os.environ['USE_LOCAL_TS'] = '1'
os.environ['BT_COVERAGE_THRESHOLD'] = '0'
import sys
sys.path.insert(0, 'scripts')
from backtest_v3_detailed import load_local_timesales_ohlc
import time
from datetime import datetime, timezone, timedelta

end_ts = int(datetime.now(timezone.utc).timestamp())
since = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
print(f"end_ts: {end_ts} ({datetime.fromtimestamp(end_ts, tz=timezone.utc)})")
print(f"since:  {since}  ({datetime.fromtimestamp(since, tz=timezone.utc)})")

pair = 'XXBTZEUR'
interval = 60
result = load_local_timesales_ohlc(pair, since, end_ts, interval)
print(f"Number of candles: {len(result)}")
if len(result):
    print("First 5:", list(result.items())[:5])
    print("Last 5:", list(result.items())[-5:])
else:
    print("No data in range.")
    # Let's check the file directly
    from pathlib import Path
    LOCAL_TS_DIR = Path('/mnt/fritz_nas/Volume/kraken/2026')
    fpath = LOCAL_TS_DIR / pair / f'ohlc_{interval}m.csv'
    print(f"Checking file: {fpath}")
    if fpath.exists():
        # Count lines in the file
        with open(fpath, 'r') as f:
            lines = f.readlines()
        print(f"Total lines in file: {len(lines)}")
        # Skip header
        data_lines = lines[1:] if len(lines) > 0 else []
        print(f"Data lines: {len(data_lines)}")
        # Parse a few lines to see the timestamp range
        timestamps = []
        for line in data_lines[:10]:
            parts = line.strip().split(',')
            if len(parts) >= 1:
                try:
                    ts = int(float(parts[0]))
                    timestamps.append(ts)
                except:
                    pass
        print(f"First 10 timestamps: {timestamps}")
        # Check last 10
        timestamps = []
        for line in data_lines[-10:]:
            parts = line.strip().split(',')
            if len(parts) >= 1:
                try:
                    ts = int(float(parts[0]))
                    timestamps.append(ts)
                except:
                    pass
        print(f"Last 10 timestamps: {timestamps}")
    else:
        print("File does not exist!")
