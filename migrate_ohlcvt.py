#!/usr/bin/env python3
import os, shutil
from pathlib import Path

BASE = Path('/mnt/fritz_nas/Volume/kraken')
YEAR_DIRS = ['2025', '2026']  # we know these exist; could also scan

def migrate_year_ohlcvts(year_dir: Path):
    ohlcvt = year_dir / 'ohlcvt'
    if not ohlcvt.is_dir():
        print(f"No ohlcvt in {year_dir}")
        return
    moved = 0
    for file in ohlcvt.iterdir():
        if not file.is_file():
            continue
        # Expect format like <pair>_<interval>.csv
        name = file.name
        if not name.endswith('.csv'):
            continue
        stem = name[:-4]
        # Split at last underscore? Actually interval may have underscore? interval is numeric, pair may have underscores? Our pairs are like XXBTZEUR, no underscore.
        # So split at '_' where the suffix is numeric
        if '_' not in stem:
            print(f"Skipping {name}: no underscore")
            continue
        pair, interval = stem.rsplit('_', 1)
        if not interval.isdigit():
            print(f"Skipping {name}: interval not numeric: {interval}")
            continue
        target_dir = BASE / year_dir.name / pair
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"ohlc_{interval}m.csv"
        print(f"Moving {file} -> {target_file}")
        shutil.move(str(file), str(target_file))
        moved += 1
    # Try to remove ohlcvt if empty
    try:
        ohlcvt.rmdir()
        print(f"Removed empty directory {ohlcvt}")
    except OSError as e:
        print(f"Could not remove {ohlcvt}: {e}")
    return moved

def main():
    total = 0
    for year in YEAR_DIRS:
        ydir = BASE / year
        if ydir.is_dir():
            total += migrate_year_ohlcvts(ydir)
        else:
            print(f"Year directory {ydir} does not exist")
    print(f"Total files moved: {total}")

if __name__ == '__main__':
    main()