#!/usr/bin/env python3
"""Collect up to 365 days of 1h OHLC from Kraken API for all bot pairs.

Saves into data/mentor_cache_1h/{pair}_{since}_{end}_60m.json
Each file is a dict {ts_str: close_price}.
Paginates automatically: Kraken returns max 720 candles per request.
"""
import json, time, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

PAIRS = ["XETHZEUR", "SOLEUR", "ADAEUR", "XXRPZEUR", "LINKEUR", "XXBTZEUR", "DOTEUR"]
INTERVAL = 60  # minutes (1h candles)
DAYS = 365

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "mentor_cache_1h"
OUT_DIR.mkdir(parents=True, exist_ok=True)

end_ts = int(datetime.now(timezone.utc).timestamp())
since_ts = int((datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp())

sess = requests.Session()

def fetch_pair(pair: str) -> dict[str, float]:
    out: dict[int, float] = {}
    since = since_ts
    loops = 0
    print(f"  {pair}: fetching {DAYS} days...", end="", flush=True)
    while since < end_ts and loops < 600:
        loops += 1
        for attempt in range(8):
            try:
                r = sess.get(
                    "https://api.kraken.com/0/public/OHLC",
                    params={"pair": pair, "interval": INTERVAL, "since": since},
                    timeout=30,
                )
                j = r.json()
            except Exception as e:
                time.sleep(2 + attempt)
                continue
            errs = j.get("error") or []
            if errs and any("Too many" in e for e in errs):
                time.sleep(2 + attempt * 1.5)
                continue
            if errs:
                print(f"\n    API error: {errs}")
                return out
            break
        else:
            print(f"\n    Rate-limit exhausted for {pair}")
            return out

        res = j.get("result", {})
        key = [k for k in res.keys() if k != "last"]
        if not key:
            break
        rows = res[key[0]]
        if not rows:
            break

        last_ts = since
        for row in rows:
            ts = int(row[0])
            if since_ts <= ts <= end_ts:
                out[ts] = float(row[4])  # close price
            last_ts = max(last_ts, ts)

        nxt = int(res.get("last", last_ts + 1))
        since = nxt if nxt > since else (last_ts + 1)
        time.sleep(0.4)

        if loops % 5 == 0:
            print(".", end="", flush=True)

    print(f" {len(out)} candles ({datetime.utcfromtimestamp(min(out.keys()) if out else 0).date()} – {datetime.utcfromtimestamp(max(out.keys()) if out else 0).date()})")
    return out

print(f"Collecting {DAYS}d of 1h OHLC for {len(PAIRS)} pairs → {OUT_DIR}")
print(f"Range: {datetime.utcfromtimestamp(since_ts).date()} → {datetime.utcfromtimestamp(end_ts).date()}\n")

for pair in PAIRS:
    data = fetch_pair(pair)
    if not data:
        print(f"  WARNING: no data for {pair}, skipping")
        continue
    actual_since = min(data.keys())
    actual_end = max(data.keys())
    out_path = OUT_DIR / f"{pair}_{actual_since}_{actual_end}_60m.json"
    out_path.write_text(json.dumps({str(k): v for k, v in sorted(data.items())}))
    print(f"  → saved {out_path.name}")

print("\nDone.")
