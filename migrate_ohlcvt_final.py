#!/usr/bin/env python3
import os, shutil
from pathlib import Path

BASE = Path('/mnt/fritz_nas/Volume/kraken')
YEAR = '2025'  # we are migrating from 2025/ohlcvt
SRC = BASE / YEAR / 'ohlcvt'

def move_file(src_file: Path):
    name = src_file.name
    if not name.endswith('.csv'):
        return
    stem = name[:-4]
    if '_' not in stem:
        return
    pair, interval = stem.rsplit('_', 1)
    if not interval.isdigit():
        return
    target_dir = BASE / YEAR / pair
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"ohlc_{interval}m.csv"
    try:
        shutil.move(str(src_file), str(target_file))
        return True, None
    except Exception as e:
        return False, str(e)

def main():
    if not SRC.is_dir():
        print(f"Source {SRC} not found")
        return
    moved = 0
    errors = 0
    for root, dirs, files in os.walk(SRC):
        for f in files:
            src_file = Path(root) / f
            ok, err = move_file(src_file)
            if ok:
                moved += 1
            else:
                errors += 1
                # optionally print error
                # print(f"Error moving {src_file}: {err}")
    print(f"Moved {moved} files, errors {errors}")
    # Remove empty directories
    for root, dirs, files in os.walk(SRC, topdown=False):
        for d in dirs:
            dir_path = Path(root) / d
            try:
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
            except OSError:
                pass
    # Finally try to remove ohlcvt if empty
    try:
        if not any(SRC.iterdir()):
            SRC.rmdir()
            print(f"Removed {SRC}")
    except OSError:
        pass

if __name__ == '__main__':
    main()