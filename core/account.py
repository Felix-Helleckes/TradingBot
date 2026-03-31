# Account and State Handling for TradingBot
# NOTE: This module is a planned stub. All account logic currently lives in
# trading_bot.py.  Do NOT import these classes in production code until they
# are fully implemented — calling any method will raise NotImplementedError.

import warnings
warnings.warn(
    "core.account is a stub module and not yet implemented. "
    "All live account logic is in trading_bot.py.",
    stacklevel=2,
)


class AccountHandler:
    def __init__(self, bot):
        self.bot = bot

    def get_eur_balance(self):
        raise NotImplementedError("core.AccountHandler.get_eur_balance is not implemented. Use trading_bot.TradingBot.get_eur_balance.")

    def get_crypto_holdings(self):
        raise NotImplementedError("core.AccountHandler.get_crypto_holdings is not implemented.")

    def sync_account_state(self):
        raise NotImplementedError("core.AccountHandler.sync_account_state is not implemented.")

    def load_purchase_prices_from_history(self):
        raise NotImplementedError("core.AccountHandler.load_purchase_prices_from_history is not implemented.")

