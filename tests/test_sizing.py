import tempfile
import os
from trading_bot import TradingBot
from types import SimpleNamespace


def make_bot_stub():
    # minimal stub: api_client with required methods and config dict
    class APIMock:
        def get_ohlc_data(self, pair, interval, since=None):
            return {pair: [[0,0,0,0,100,0,0,0]]*30}
        def query_public(self, *a, **k):
            return {}
    cfg = {
        'bot_settings': {'trade_pairs': ['XBTEUR'], 'trade_amounts': {'trade_amount_eur': 30.0}},
        'risk_management': {
            'allocation_per_trade_percent': 10.0,
            'small_account_fixed_trade_eur': 25.0,
            'small_account_threshold_eur': 200.0,
            'target_volatility_pct': 1.6
        }
    }
    api = APIMock()
    return TradingBot(api, cfg)


def test_small_account_override():
    bot = make_bot_stub()
    amt = bot._get_dynamic_trade_amount_eur('XBTEUR', 100.0)
    assert amt <= 25.0
