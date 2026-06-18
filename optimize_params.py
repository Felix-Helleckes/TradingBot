#!/usr/bin/env python3
"""
Weekly parameter optimization for the Kraken trading bot.
Loads multi-year OHLC data from the NAS, runs a simple grid search over
RSI buy/sell thresholds, and writes the best values back to config.toml.
The bot hot-reloads config every 5 minutes, so changes take effect quickly.
"""

import os
import re
from pathlib import Path

# ---------- Configuration ----------
NAS_ROOT = Path("/mnt/fritz_nas/Volume/kraken")
PAIRS = ["XXBTZEUR", "XETHZEUR", "SOLEUR", "ADAEUR", "DOTEUR", "XXRPZEUR", "LINKEUR"]
# Grid search ranges (inclusive)
RSI_BUY_RANGE = range(20, 41, 5)   # 20,25,30,35,40
RSI_SELL_RANGE = range(55, 81, 5)  # 55,60,65,70,75,80
RSI_PERIOD = 14
CONFIG_PATH = Path("/home/felix/tradingbot/config.toml")
# ----------------------------------

def load_multi_year_ohlc(pair: str, nas_root: Path) -> list:
    """Load all available 5‑minute close prices for a pair from all year folders."""
    folder_map = {
        'XBTEUR': 'XXBTZEUR',
        'XETHZEUR': 'XETHZEUR',
        'XRPEUR': 'XXRPZEUR',
        'SOLEUR': 'SOLEUR',
        'ADAEUR': 'ADAEUR',
        'DOTEUR': 'DOTEUR',
        'LINKEUR': 'LINKEUR',
    }
    folder = folder_map.get(pair, pair)
    closes = []
    # Gather all year directories
    years = sorted([int(p.name) for p in nas_root.iterdir()
                    if p.is_dir() and p.name.isdigit()])
    for y in years:
        csv_path = nas_root / str(y) / folder / 'ohlc_5m.csv'
        if not csv_path.exists():
            continue
        try:
            with open(csv_path, newline='') as f:
                import csv
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        closes.append(float(row['close']))
                    except (KeyError, ValueError):
                        continue
                if len(closes) >= 10000:  # safety break to avoid too huge list
                    break
        except Exception as e:
            print(f"[WARN] Failed to read {csv_path}: {e}")
    return closes

def calculate_rsi(prices, period=RSI_PERIOD):
    """Simple RSI calculation (Wilder's smoothing)."""
    if len(prices) < period + 1:
        return [50.0] * len(prices)  # neutral
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    seed = deltas[:period]
    up = sum([x for x in seed if x >= 0]) / period
    down = -sum([x for x in seed if x < 0]) / period
    if down == 0:
        rs = float('inf')
    else:
        rs = up / down
    rsi = [100.0 - (100.0 / (1.0 + rs))]
    for i in range(period, len(deltas)):
        delta = deltas[i]
        up_val = max(delta, 0)
        down_val = -min(delta, 0)
        up = (up * (period - 1) + up_val) / period
        down = (down * (period - 1) + down_val) / period
        if down == 0:
            rs = float('inf')
        else:
            rs = up / down
        rsi.append(100.0 - (100.0 / (1.0 + rs)))
    # Pad beginning to align with prices
    return [50.0] * (len(prices) - len(rsi)) + rsi

def simulate(pair_closes, rsi_buy, rsi_sell):
    """Very simple long-only simulation: enter when RSI < rsi_buy, exit when RSI > rsi_sell.
    Returns total profit percent (sum of per-trade returns)."""
    in_position = False
    entry_price = 0.0
    total_profit = 0.0
    rsi_vals = calculate_rsi(pair_closes)
    for i in range(1, len(pair_closes)):
        price = pair_closes[i]
        rsi = rsi_vals[i]
        if not in_position and rsi < rsi_buy:
            # Enter at this close
            in_position = True
            entry_price = price
        elif in_position and rsi > rsi_sell:
            # Exit at this close
            in_position = False
            exit_price = price
            if entry_price > 0:
                total_profit += (exit_price - entry_price) / entry_price
    # If still in position at end, close at last price
    if in_position and len(pair_closes) > 0:
        exit_price = pair_closes[-1]
        if entry_price > 0:
            total_profit += (exit_price - entry_price) / entry_price
    return total_profit

def update_config(rsi_buy: int, rsi_sell: int):
    """Update the two RSI threshold lines in config.toml."""
    try:
        text = CONFIG_PATH.read_text()
        # Replace mr_rsi_oversold_threshold line
        text = re.sub(
            r'^mr_rsi_oversold_threshold\s*=\s*\d+',
            f'mr_rsi_oversold_threshold = {rsi_buy}',
            text,
            flags=re.MULTILINE
        )
        # Replace mr_rsi_overbought_threshold line
        text = re.sub(
            r'^mr_rsi_overbought_threshold\s*=\s*\d+',
            f'mr_rsi_overbought_threshold = {rsi_sell}',
            text,
            flags=re.MULTILINE
        )
        CONFIG_PATH.write_text(text)
        print(f"[INFO] Updated config: oversold={rsi_buy}, overbought={rsi_sell}")
    except Exception as e:
        print(f"[ERROR] Failed to update config: {e}")

def main():
    # Load data for all pairs
    all_data = {}
    for pair in PAIRS:
        print(f"[INFO] Loading data for {pair}...")
        closes = load_multi_year_ohlc(pair, NAS_ROOT)
        if len(closes) < RSI_PERIOD + 10:
            print(f"[WARN] Not enough data for {pair}: {len(closes)} candles")
            continue
        all_data[pair] = closes
        print(f"[INFO] Loaded {len(closes)} candles for {pair}")

    best_profit = -float('inf')
    best_params = (None, None)

    # Grid search
    for rsi_buy in RSI_BUY_RANGE:
        for rsi_sell in RSI_SELL_RANGE:
            if rsi_buy >= rsi_sell:
                continue  # invalid
            total = 0.0
            for pair, closes in all_data.items():
                profit = simulate(closes, rsi_buy, rsi_sell)
                total += profit
            if total > best_profit:
                best_profit = total
                best_params = (rsi_buy, rsi_sell)

    print(f"[RESULT] Best RSI buy={best_params[0]}, sell={best_params[1]} with profit={best_profit:.4f}")

    if best_params[0] is not None:
        update_config(best_params[0], best_params[1])
    else:
        print("[WARN] No valid parameters found; config not changed.")

if __name__ == '__main__':
    main()