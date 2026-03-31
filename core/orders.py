# Order Handling for TradingBot
# NOTE: This module is a planned stub. All order logic currently lives in
# trading_bot.py.  Do NOT import these classes in production code until fully
# implemented — calling any method will raise NotImplementedError.

import warnings
warnings.warn(
    "core.orders is a stub module and not yet implemented. "
    "All live order logic is in trading_bot.py.",
    stacklevel=2,
)


class OrderHandler:
    def __init__(self, bot):
        self.bot = bot

    def execute_buy_order(self, pair, price):
        raise NotImplementedError("core.OrderHandler.execute_buy_order is not implemented. Use trading_bot.TradingBot.execute_buy_order.")

    def execute_sell_order(self, pair, price, require_profit_target=True, reason=None):
        raise NotImplementedError("core.OrderHandler.execute_sell_order is not implemented.")

    def execute_open_short_order(self, pair, price):
        raise NotImplementedError("core.OrderHandler.execute_open_short_order is not implemented.")

    def execute_close_short_order(self, pair, price):
        raise NotImplementedError("core.OrderHandler.execute_close_short_order is not implemented.")

