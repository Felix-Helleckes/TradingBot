import importlib.util, os

# Load project utils module directly (tests may be run from venv/CWD)
utils_path = os.path.join(os.path.dirname(__file__), '..', 'utils.py')
spec = importlib.util.spec_from_file_location('utils', os.path.abspath(utils_path))
utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utils)

pct_to_frac = utils.pct_to_frac
apply_trade_costs = utils.apply_trade_costs


def test_pct_to_frac_examples():
    # fraction input stays the same
    assert abs(pct_to_frac(0.0026) - 0.0026) < 1e-9
    # percent as 0.26 -> 0.0026
    assert abs(pct_to_frac(0.26) - 0.0026) < 1e-9
    # percent as 26 -> 0.26
    assert abs(pct_to_frac(26) - 0.26) < 1e-9
    # zero and None
    assert pct_to_frac(0) == 0.0
    assert pct_to_frac(None) == 0.0


def test_apply_trade_costs_buy_and_sell():
    cfg = {'risk_management': {'fees_maker_percent': 0.16, 'fees_taker_percent': 0.26}}
    price = 100.0
    qty = 1.0
    # buy with maker fee (0.16% => 0.0016)
    r_buy = apply_trade_costs(price, qty, cfg, maker=True, side='buy')
    assert 'fee' in r_buy and 'net_cost' in r_buy
    assert abs(r_buy['fee'] - (100.0 * 0.0016)) < 1e-6
    # sell with taker fee
    r_sell = apply_trade_costs(price, qty, cfg, maker=False, side='sell')
    assert 'fee' in r_sell and 'net_proceeds' in r_sell
    assert abs(r_sell['fee'] - (100.0 * 0.0026)) < 1e-6


if __name__ == '__main__':
    test_pct_to_frac_examples()
    test_apply_trade_costs_buy_and_sell()
    print('OK')
