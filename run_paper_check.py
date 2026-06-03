#!/usr/bin/env python3
import toml, sys
sys.path.insert(0, '/home/felix/tradingbot')
from kraken_interface import KrakenAPI
from trading_bot import TradingBot

cfg = toml.load('/home/felix/tradingbot/config.paper.toml')
kraken = KrakenAPI(api_key='', api_secret='', paper_mode=True)
bot = TradingBot(kraken, cfg)
# Print the effective value read from config
print(f"max_short_notional_eur={getattr(bot, 'max_short_notional_eur', 'MISSING')}")
