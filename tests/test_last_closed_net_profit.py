import importlib.util, os, tempfile, json

# load utils
utils_path = os.path.join(os.path.dirname(__file__), '..', 'utils.py')
spec = importlib.util.spec_from_file_location('utils', os.path.abspath(utils_path))
utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utils)


def test_last_closed_net_profit():
    d = tempfile.mkdtemp(prefix='tbtest2_')
    try:
        jpath = os.path.join(d, 'logs', 'trade_events.jsonl')
        os.makedirs(os.path.dirname(jpath), exist_ok=True)
        # create BUY then SELL
        buy = {'type': 'BUY', 'pair': 'XBTEUR', 'price': 100.0}
        sell = {'type': 'SELL', 'pair': 'XBTEUR', 'price': 105.0}
        with open(jpath, 'w', encoding='utf-8') as f:
            f.write(json.dumps(buy) + "\n")
            f.write(json.dumps(sell) + "\n")
        net = utils.last_closed_trade_net_profit_pct(jpath, 'XBTEUR', 0.16, 0.26)
        # gross = 5.0%, fees_total = 0.16% + 0.26% = 0.42% => net ~4.58%
        assert net is not None
        assert abs(net - 4.58) < 0.01
        print('OK')
    finally:
        try:
            import shutil
            shutil.rmtree(d)
        except Exception:
            pass


if __name__ == '__main__':
    test_last_closed_net_profit()
