#!/usr/bin/env python3
"""Fast OHLC collector for one year — writes to data/mentor_cache_1h/.
Uses Kraken public OHLC API, paginates properly.
Run this script and wait for it to complete before running the sweep.
"""
import json, time, requests, datetime, sys
from pathlib import Path

PAIRS = ["XETHZEUR", "SOLEUR", "ADAEUR", "XXRPZEUR", "LINKEUR", "XXBTZEUR", "DOTEUR"]
INTERVAL = 60
DAYS = 365
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "mentor_cache_1h"
OUT_DIR.mkdir(parents=True, exist_ok=True)

now = datetime.datetime.now(datetime.timezone.utc)
end_ts = int(now.timestamp())
since_ts = int((now - datetime.timedelta(days=DAYS)).timestamp())

print(f"Range: {datetime.datetime.utcfromtimestamp(since_ts).date()} → {datetime.datetime.utcfromtimestamp(end_ts).date()}")

sess = requests.Session()

for pair in PAIRS:
    out: dict = {}
    curr = since_ts
    calls = 0
    t0 = time.time()

    while curr < end_ts:
        calls += 1
        for attempt in range(6):
            try:
                r = sess.get(
                    "https://api.kraken.com/0/public/OHLC",
                    params={"pair": pair, "interval": INTERVAL, "since": curr},
                    timeout=30,
                )
                j = r.json()
            except Exception as e:
                if attempt == 5:
                    print(f"  {pair}: request failed: {e}")
                    break
                time.sleep(2 + attempt)
                continue

            errs = j.get("error") or []
            if any("Too many" in e for e in errs):
                time.sleep(3 + attempt * 2)
                continue
            if errs:
                print(f"  {pair}: API error: {errs}")
                curr = end_ts  # abort this pair
                break
            break
        else:
            break

        res = j.get("result", {})
        key = [k for k in res if k != "last"]
        if not key:
            break

        rows = res[key[0]]
        if not rows:
            break

        last_ts_row = curr
        for row in rows:
            ts = int(row[0])
            if since_ts <= ts <= end_ts:
                out[ts] = float(row[4])  # close price
            last_ts_row = max(last_ts_row, ts)

        next_since = int(res.get("last", last_ts_row + 1))
        if next_since <= curr:
            next_since = last_ts_row + INTERVAL * 60
        curr = next_since
        time.sleep(0.35)

    elapsed = time.time() - t0
    if out:
        fs = min(out.keys())
        fe = max(out.keys())
        out_path = OUT_DIR / f"{pair}_{fs}_{fe}_60m.json"
        out_path.write_text(json.dumps({str(k): v for k, v in sorted(out.items())}))
        print(f"  {pair}: {len(out)} candles ({datetime.datetime.utcfromtimestamp(fs).date()} → {datetime.datetime.utcfromtimestamp(fe).date()}) in {calls} calls / {elapsed:.1f}s → {out_path.name}")
        sys.stdout.flush()
    else:
        print(f"  {pair}: NO DATA after {calls} calls")
        sys.stdout.flush()

print("\nDone.")
