#!/usr/bin/env python3
import toml, copy, sys, json, os, random, time
sys.path.insert(0, '/home/felix/tradingbot')
from trading_bot import Backtester
from kraken_interface import KrakenAPI

BASE_CONFIG_PATH = '/home/felix/tradingbot/config.toml'
base_cfg = toml.load(BASE_CONFIG_PATH)

param_grid = {
    'min_buy_score': [4.0, 6.0, 8.0, 10.0],
    'take_profit_percent': [2.0, 3.5, 5.0],
    'hard_stop_loss_percent': [1.5, 2.0, 2.5],
    'atr_multiplier': [1.0, 1.5, 2.0],
    # allocation_per_trade_percent maybe vary later
}

def update_cfg(cfg, params):
    cfg = copy.deepcopy(cfg)
    risk = cfg.setdefault('risk_management', {})
    for k, v in params.items():
        if k in risk:
            risk[k] = v
        else:
            # fallback
            risk[k] = v
    return cfg

def run_backtest(cfg_dict):
    # Write temporary config
    tmp_path = '/tmp/config_opt_test.toml'
    with open(tmp_path, 'w') as f:
        toml.dump(cfg_dict, f)
    # Run backtest
    bt = Backtester(KrakenAPI('', ''), cfg_dict)
    # Capture output? Backtester.run prints to stdout; we can redirect
    import io
    import contextlib
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        try:
            bt.run()
        except Exception as e:
            print(f"Backtest error: {e}")
            return None
    output = f.getvalue()
    # parse
    total_return = None
    sharpe = None
    max_dd = None
    trades = None
    for line in output.split('\\n'):
        if line.startswith('Total Return:'):
            try:
                total_return = float(line.split(':')[1].strip().replace('%',''))
            except:
                pass
        if line.startswith('Sharpe Ratio:'):
            try:
                sharpe = float(line.split(':')[1].strip())
            except:
                pass
        if line.startswith('Max Drawdown:'):
            try:
                max_dd = float(line.split(':')[1].strip().replace('%',''))
            except:
                pass
        if line.startswith('Total Trades:'):
            try:
                trades = int(line.split(':')[1].strip())
            except:
                pass
    return {
        'total_return': total_return,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'trades': trades,
        'output': output
    }

def main():
    base = toml.load(BASE_CONFIG_PATH)
    results = []
    # simple grid, limit combos
    import itertools
    keys = list(param_grid.keys())
    values = [param_grid[k] for k in keys]
    count = 0
    for combo in itertools.product(*values):
        # raise limit to explore more combos but keep an upper bound
        if count >= 100:
            break
        params = dict(zip(keys, combo))
        cfg = update_cfg(base, params)

        # run backtest with retries if data was rate-limited or produced no trades
        attempts = 0
        res = None
        while attempts < 3:
            res = run_backtest(cfg)
            if res is not None and res.get('total_return') is not None:
                break
            attempts += 1
            sleep_secs = 1 + attempts + random.random()
            print(f"Retrying backtest for {params} (attempt {attempts}) after {sleep_secs:.1f}s")
            time.sleep(sleep_secs)

        if res is None or res.get('total_return') is None:
            print(f"Skipping {params} due to failed runs")
            continue

        res['params'] = params
        results.append(res)
        count += 1
        print(f"Tested {count}: {params} -> return {res['total_return']}%")

        # polite pause between combos to reduce API rate pressure
        time.sleep(0.5 + random.random() * 1.5)
    # sort by total_return descending
    results_sorted = sorted(results, key=lambda x: x['total_return'] if x['total_return'] is not None else -999, reverse=True)
    print("\\nTop 5:")
    for r in results_sorted[:5]:
        print(f"Params {r['params']}: return {r['total_return']}%, sharpe {r['sharpe']}, maxdd {r['max_dd']}%, trades {r['trades']}")
    # save best
    best = results_sorted[0] if results_sorted else None
    if best:
        best_path = '/home/felix/tradingbot/optimization_best.json'
        with open(best_path, 'w') as f:
            json.dump(best, f, indent=2)
        print(f"Best config saved to {best_path}")
    else:
        print("No successful runs.")

if __name__ == '__main__':
    main()