# Risk and Regime Management for TradingBot
# NOTE: This module is a planned stub. All risk logic currently lives in
# trading_bot.py.  Do NOT import these classes in production code until fully
# implemented — calling any method will raise NotImplementedError.

import warnings
warnings.warn(
    "core.risk is a stub module and not yet implemented. "
    "All live risk logic is in trading_bot.py.",
    stacklevel=2,
)


class RiskManager:
    def __init__(self, bot):
        self.bot = bot

    def is_risk_on_regime(self):
        raise NotImplementedError("core.RiskManager.is_risk_on_regime is not implemented. Use trading_bot.TradingBot._is_risk_on_regime.")

    def compute_mtf_regime_score(self):
        raise NotImplementedError("core.RiskManager.compute_mtf_regime_score is not implemented.")

    def benchmark_volatility_pct(self):
        raise NotImplementedError("core.RiskManager.benchmark_volatility_pct is not implemented.")

    def allocation_multiplier(self):
        raise NotImplementedError("core.RiskManager.allocation_multiplier is not implemented.")

