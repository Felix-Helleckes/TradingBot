# 🤖 Kraken Trading Bot

An automated, signal-driven spot trading bot for [Kraken](https://www.kraken.com) — built for EUR pairs, designed to be lean, transparent, and safe to run with real money.

> ⚠️ **This bot executes real trades.** Always start with a small amount and monitor logs closely. Never risk more than you can afford to lose.

---

## ✨ Features

- **Multi-pair trading** — BTC, ETH, SOL, XRP (EUR pairs, configurable)
- **Dual signal engine** — Mean-reversion (RSI) + trend breakout (Bollinger Bands)
- **OHLC-seeded history** — warms up from real 15-minute candles on startup, no waiting
- **Smart entry filters** — volume filter (skips low-liquidity entries), time-of-day filter (optional)
- **Fee-aware exits** — take-profit includes Kraken fee buffer (maker + taker)
- **Risk controls** — hard stop-loss, break-even stop, ATR trailing, time-stop, drawdown circuit breaker
- **Regime filter** — switches to risk-off sizing in bear markets (BTC benchmark)
- **Position recovery** — reconstructs holdings and PnL from Kraken trade history on restart
- **Auto-monitoring** — cron-based watchdog restarts the bot if it crashes
- **Log rotation** — weekly cleanup keeps logs lean

---

## 🚀 Quick Start

**1. Clone and install dependencies**
```bash
git clone https://github.com/irgendwasmitfelix/TradingBot.git
cd TradingBot
pip install -r requirements.txt
```

**2. Set up API credentials**
```bash
cp .env.example .env
# Edit .env and add your Kraken API key and secret
```
> Create a Kraken API key with **Trade** permissions only. Never enable withdrawals.

**3. Configure the bot**

Edit `config.toml` to set your capital and pairs:
```toml
trade_amount_eur = 20.0       # EUR per trade
initial_balance = 100.0       # your starting balance
target_balance_eur = 150.0    # stop target
```

**4. Test your connection**
```bash
python main.py --test
```

**5. Run the bot**
```bash
python main.py
```

---

## 📁 Project Structure

| File | Purpose |
|---|---|
| `main.py` | Entry point, logging setup, single-instance lock |
| `trading_bot.py` | Strategy logic, order execution, risk management |
| `analysis.py` | Technical indicators and signal scoring |
| `kraken_interface.py` | Kraken API wrapper |
| `config.toml` | All settings — pairs, risk, filters, sizing |
| `utils.py` | Config loading and validation |
| `scripts/monitor_bot.sh` | Watchdog: restarts bot if crashed (run via cron) |
| `scripts/rotate_logs.sh` | Weekly log rotation |

---

## ⚙️ How It Works

Each cycle (~60 seconds) the bot:

1. Fetches live ticker prices for all configured pairs
2. Seeds/updates price history from 15m OHLC candles if needed
3. Generates a signal score using RSI, SMA, and Bollinger Bands
4. Applies entry filters (volume, regime, score threshold, cooldowns)
5. Executes the best-scoring BUY or checks open positions for exits

**Exit logic:** Positions are only sold when the configured profit target is reached (default 4.5% + fee buffer). A hard stop-loss (default 4%) limits downside.

---

## 🛡️ Risk Management

| Control | Default | Description |
|---|---|---|
| Take-profit | 4.5% + fees | Minimum gain before selling |
| Hard stop-loss | 4.0% | Maximum loss per position |
| Break-even stop | enabled | Moves SL to entry after 1.5% gain |
| Trade cooldown | 60 min/pair | Prevents overtrading |
| Max open positions | 2 | Limits concurrent exposure |
| Drawdown circuit breaker | 10% | Pauses trading after large portfolio drop |
| Loss streak pause | 3 losses | 60 min cooldown after consecutive losses |

---

## 📊 Status Display

The bot prints a live status line every cycle:

```
[42] BTC:HOLD ETH:BUY SOL:HOLD XRP:HOLD | RISK_ON/ACTIVE | Best: ETHEUR (BUY) | Bal: 104.20EUR | Start: 100.00EUR | AdjPnL: +4.20EUR | Trades: 3
```

Full logs are written to `logs/bot_activity.log`.

---

## 🔧 Monitoring & Ops

Set up the watchdog and log rotation via cron:

```bash
# Check every 5 minutes if bot is running, restart if not
*/5 * * * * /path/to/tradingbot/scripts/monitor_bot.sh

# Clear logs every Sunday at 03:00
0 3 * * 0 /path/to/tradingbot/scripts/rotate_logs.sh
```

---

## 📚 Further Reading

- [Setup Guide](SETUP_GUIDE.md) — detailed installation and configuration walkthrough
- [Changelog](CHANGELOG.md) — full history of changes and improvements
- `scripts/` — backtesting, data collection, and research tools

---

## ⚖️ Disclaimer

This software is for educational purposes. Trading cryptocurrency involves significant risk. Past backtest performance does not guarantee future results. The authors are not responsible for any financial losses.

---

*Active development — contributions and feedback welcome.*
