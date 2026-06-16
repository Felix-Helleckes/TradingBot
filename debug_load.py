import os
os.environ['USE_LOCAL_TS'] = '1'
os.environ['BT_COVERAGE_THRESHOLD'] = '0'
import sys
sys.path.insert(0, 'scripts')
from pathlib import Path
LOCAL_TS_DIR = Path('/mnt/fritz_nas/Volume/kraken/2026')
pair = 'XXBTZEUR'
interval = 60
since_ts = 1780747959  # 2026-06-06 12:12:39
end_ts = 1780834359    # 2026-06-07 12:12:39
bucket = max(1, int(interval)) * 60
print(f"bucket size: {bucket}")
fpath = LOCAL_TS_DIR / pair / f"ohlc_{interval}m.csv"
print(f"Reading from: {fpath}")
out = {}
seen_window = False
with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
    for line_num, line in enumerate(f):
        parts = line.strip().split(",")
        if len(parts) < 2:
            continue
        try:
            ts = int(float(parts[0]))
            px = float(parts[1])
        except Exception as e:
            # print(f"Line {line_num}: parse error: {e}")
            continue
        if ts < since_ts:
            continue
        if ts > end_ts:
            if seen_window:
                break
            continue
        seen_window = True
        bts = (ts // bucket) * bucket
        out[bts] = px  # last price in bucket
        # print(f"Added: ts={ts} -> bts={bts}, px={px}")
print(f"Number of entries in out: {len(out)}")
if len(out):
    print("First 5:", sorted(out.items())[:5])
    print("Last 5:", sorted(out.items())[-5:])
else:
    print("No data found.")
    # Let's see what timestamps we have in the file around the range
    # Read first and last few lines
    with open(fpath, "r") as f:
        lines = f.readlines()
    # skip header
    data_lines = lines[1:]
    timestamps = []
    for line in data_lines[:5]:
        parts = line.strip().split(",")
        if len(parts) >= 1:
            try:
                ts = int(float(parts[0]))
                timestamps.append(ts)
            except:
                pass
    print("First 5 timestamps in file:", timestamps)
    timestamps = []
    for line in data_lines[-5:]:
        parts = line.strip().split(",")
        if len(parts) >= 1:
            try:
                ts = int(float(parts[0]))
                timestamps.append(ts)
            except:
                pass
    print("Last 5 timestamps in file:", timestamps)
