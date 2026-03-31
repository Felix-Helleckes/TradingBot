# Backtester class
# NOTE: This module is a planned stub. The working Backtester class currently
# lives in trading_bot.py. Do NOT import this in production code until the
# full implementation is moved here.

import warnings
warnings.warn(
    "core.backtester is a stub module and not yet implemented. "
    "The live Backtester class is in trading_bot.py.",
    stacklevel=2,
)


class Backtester:
    def __init__(self, api_client=None, config=None):
        raise NotImplementedError(
            "core.Backtester is not yet implemented. "
            "Use the Backtester class inside trading_bot.py instead."
        )

    def run(self):
        raise NotImplementedError("core.Backtester.run is not implemented.")

