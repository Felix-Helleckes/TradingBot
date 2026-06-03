#!/usr/bin/env python3
import sys, time, logging
sys.path.insert(0, '/home/felix/tradingbot')
from kraken_interface import KrakenAPI
from trading_bot import TradingBot
from utils import load_config

CONFIG_PATH = '/home/felix/tradingbot/config.toml'
LOG_PATH = '/home/felix/tradingbot/logs/paper_smoke.log'

logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('paper_smoke')

def main(iterations=6, delay=10):
    cfg = load_config(CONFIG_PATH)
    kraken = KrakenAPI(api_key='', api_secret='', paper_mode=True)
    bot = TradingBot(kraken, cfg)
    logger.info('Paper smoke test started (paper_mode=True)')
    for i in range(iterations):
        try:
            best_pair, best_signal, best_score = bot.analyze_all_pairs()
            logger.info(f'ITER {i+1}: best={best_pair} signal={best_signal} score={best_score:.2f}')
            # also dump per-pair summary
            for p in bot.trade_pairs:
                sig = bot.pair_signals.get(p, 'HOLD')
                sc = float(bot.pair_scores.get(p, 0.0))
                price = bot.pair_prices.get(p, 0.0)
                logger.info(f'PAIR_SUMMARY {p}: {sig} | score={sc:.2f} | price={price}')
        except Exception as e:
            logger.exception(f'Error during smoke iter: {e}')
        time.sleep(delay)
    logger.info('Paper smoke test finished')

if __name__ == '__main__':
    main()
