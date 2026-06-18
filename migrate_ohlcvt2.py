#!/usr/bin/env python3
import os, shutil
from pathlib import Path
import sys

BASE = Path('/mnt/fritz_nas/Volume/kraken')
YEAR_DIRS = ['2025', '2026']  # we know these exist; could also scan

def migrate_year_ohlcvts(year_dir: Path):
    ohlcvt = year_dir / 'ohlcvt'
    if not ohlcvt.is_dir():
        print(f"No ohlcvt in {year_dir}")
        return 0
    moved = 0
    errors = 0
    for file in ohlcvt.iterdir():
        if not file.is_file():
            continue
        name = file.name
        if not name.endswith('.csv'):
            continue
        stem = name[:-4]
        if '_' not in stem:
            continue
        pair, interval = stem.rsplit('_', 1)
        if not interval.isdigit():
            continue
        target_dir = BASE / year_dir.name / pair
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"ohlc_{interval}m.csv"
        try:
            shutil.move(str(file), str(target_file))
            moved += 1
        except Exception as e:
            # print(f"Error moving {file}: {e}")
            errors += 1
            continue
    # Try to remove ohlcvt if empty
    try:
        ohlcvt.rmdir()
        print(f"Removed empty directory {ohlcvt}")
    except OSError:
        pass
    return moved, errors

def main():
    total_moved = 0
    total_errors = 0
    for year in YEAR_DIRS:
        ydir = BASE / year
        if ydir.is_dir():
            moved, errors = migrate_year_ohlcvts(ydir)
            total_moved += moved
            total_errors += errors
            print(f"Year {year}: moved {moved}, errors {errors}")
        else:
            print(f"Year directory {ydir} does not exist")
    print(f"Total moved: {total_moved}, total errors: {total_errors}")

if __name__ == '__main__':
    main()