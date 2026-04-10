# 🤖 Kraken Trading Bot

[![Watch Live](https://img.shields.io/badge/▶_Watch_Live-YouTube-red?style=for-the-badge&logo=youtube)](https://www.youtube.com/@TheEfficientDev)
[![Trading Bot](https://img.shields.io/badge/Trading_Bot-GitHub-181717?style=for-the-badge&logo=github)](https://github.com/irgendwasmitfelix/TradingBot)
[![Portfolio](https://img.shields.io/badge/Portfolio-felix-helleckes.github.io-0a66c2?style=for-the-badge&logo=github)](https://felix-helleckes.github.io)

An automated, signal-driven spot trading bot for [Kraken](https://www.kraken.com) — built for EUR pairs, designed to be lean, transparent, and safe to run with real money.

> ⚠️ **This bot executes real trades.** Always start with a small amount and monitor logs closely. Never risk more than you can afford to lose.

---

## ✨ Features

- **Multi-pair trading** — BTC, ETH, SOL, XRP (EUR pairs, configurable)
- **Dual signal engine** — Mean-reversion (RSI) + trend breakout (Bollinger Bands)
- **NAS 5m OHLC seeding** — fills 200-tick history buffer from local NAS on startup, no API wait
- **Smart entry filters** — volume filter, regime filter, score threshold, per-pair cooldowns
- **Fee-aware exits** — take-profit includes Kraken fee buffer (maker + taker)
- **Risk controls** — ATR trailing stop, break-even stop, hard stop-loss, time-stop, drawdown circuit breaker
- **Stop-loss actually works** — emergency exits (ATR, hard stop, time-stop) bypass the TP gate
- **Portfolio-aware drawdown** — computed from EUR cash + open position value (no false pauses)
- **Regime filter** — switches to risk-off sizing in bear markets (BTC benchmark)
- **Bear Shield** — parks everything in FIAT when BTC drops below 4h EMA50
- **Position recovery** — reconstructs holdings and PnL from Kraken trade history on restart
- **Cooldown persistence** — per-pair cooldown state survives restarts (no re-buying immediately)
- **Telegram notifications** — instant alerts on every trade and critical error
- **Systemd service** — auto-restart on crash, watchdog heartbeat, rate-limiting
- **Log rotation** — `RotatingFileHandler` keeps logs at ≤5 MB × 5 backups

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

**3. (Optional) Set up Telegram notifications**
```bash
# Add to .env:
TELEGRAM_TOKEN=your_bot_token   # from @BotFather
TELEGRAM_CHAT_ID=your_chat_id  # run: python3 scripts/setup_telegram.py
```

**4. Configure the bot**

Edit `config.toml` to set your capital and pairs:
```toml
trade_amount_eur = 20.0       # EUR per trade
initial_balance = 100.0       # your starting balance
target_balance_eur = 150.0    # stop target
```

**5. Run the bot**
```bash
python main.py
```

Or as a systemd service (recommended for 24/7 Pi operation):
```bash
sudo cp kraken-bot.service /etc/systemd/system/
sudo systemctl enable --now kraken-bot
sudo journalctl -u kraken-bot -f
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
| `core/notifier.py` | Telegram notification helper |
| `kraken-bot.service` | systemd unit file for 24/7 Pi operation |
| `scripts/` | Backtesting, data collection, ops tools |

---

## ⚙️ How It Works

Each cycle (~30 seconds) the bot:

1. Fetches live ticker prices for all configured pairs
2. Seeds price history from NAS 5m OHLC files on startup (200-tick buffer)
3. Generates a signal score using RSI, SMA, and Bollinger Bands
4. Applies entry filters (volume, regime, score threshold, cooldowns)
5. Executes the best-scoring BUY or checks open positions for exits

**Exit logic:** ATR trailing stop manages exits dynamically. Hard stop-loss (2.5%) and time-stop (16h) act as safety nets. All stop exits bypass the take-profit gate — they always execute.

---

## 🛡️ Risk Management

| Control | Default | Description |
|---|---|---|
| Take-profit | 4.5% + fees | Minimum gain before selling |
| Hard stop-loss | 2.5% | Maximum loss per position |
| ATR trailing stop | 2.5× ATR | Dynamic stop that ratchets up with price |
| Break-even stop | enabled | Moves SL to entry after 1.5% gain |
| Trade cooldown | 60 min/pair | Prevents overtrading |
| Global cooldown | 30 min | Minimum gap between any two trades |
| Max open positions | 2 | Limits concurrent exposure |
| Drawdown circuit breaker | 10% portfolio | Pauses buys after large portfolio drop |
| Loss streak pause | 3 losses | 60 min cooldown after consecutive losses |
| Bear Shield | enabled | Parks in FIAT when BTC < 4h EMA50 |

---

## 📊 Status Display

The bot prints a live status line every cycle:

```
[42] BTC:HOLD ETH:BUY SOL:HOLD XRP:HOLD | RISK_ON/ACTIVE | Best: ETHEUR (BUY) | Bal: 104.20EUR | Start: 100.00EUR | AdjPnL: +4.20EUR | TotalPnL: +4.20EUR | Trades: 3
```

Full logs are written to `logs/bot_activity.log` (rotated at 5 MB).

---

## 🔧 Monitoring & Ops

The bot runs as a **systemd service** — no cron watchdog needed:

```bash
sudo systemctl status kraken-bot     # check status
sudo journalctl -u kraken-bot -f     # follow live logs
sudo systemctl restart kraken-bot    # restart after config change
```

Systemd handles auto-restart (30s delay), rate-limiting (max 5 restarts / 5 min), and watchdog (kills + restarts if bot hangs for >120s).

---

## 🏗️ Architecture Overview

```
tradingbot/
├── main.py              # Entry point: logging, single-instance lock, load_dotenv
├── trading_bot.py       # Core engine: TradingBot class + Backtester (~2 200 lines)
├── analysis.py          # Signal engine: TechnicalAnalysis (RSI, SMA, Bollinger Bands)
├── kraken_interface.py  # Kraken API wrapper: rate-limit backoff, order locking
├── price_action.py      # Bar-pattern helpers: wick ratio, engulfing, breakout squeeze
├── utils.py             # Shared utils: load_config(), validate_config(), nas_paths()
├── order_lock.py        # File-based exclusive lock to prevent duplicate orders
├── core/
│   └── notifier.py      # Telegram notifications (reads TELEGRAM_TOKEN/CHAT_ID from .env)
├── config.toml          # Single source of truth for all runtime parameters
├── kraken-bot.service   # systemd unit (Restart=always, WatchdogSec=120)
├── reports/             # Trade journal CSV + weekly HTML reports
├── logs/                # bot_activity.log (rotating), trade_events.jsonl
├── data/                # history_buffer.json, cooldown_state.json, pnl_state.json
└── scripts/             # Ops, backtesting, data-collection, and reporting tools
```

---

## 🔄 Signal Flow

A trade decision flows through four layers:

```
1. analysis.py
   TechnicalAnalysis.generate_signal_with_score()  ◄── live ticker price
        │
        │  Mean-reversion path (enable_mr_signals):
        │    RSI ≤ mr_rsi_oversold  → BUY  score +
        │    RSI ≥ mr_rsi_overbought→ SELL score −
        │    Score scales with distance from threshold (not hardcoded 30/70)
        │
        │  Trend/breakout path (enable_trend_signals):
        │    price > Bollinger upper + RSI ≥ 55 → BUY  score +
        │    price < Bollinger lower + RSI ≤ 45 → SELL score −
        │
        │  returns (signal: str, score: float  in  [−50, +50])
        ▼
2. trading_bot.py
   TradingBot.analyze_all_pairs()
        │  picks highest |score| pair
        ▼
   TradingBot.start_trading()  — layered BUY guards:
        1. Not in loss-streak pause
        2. Portfolio drawdown < max_drawdown_percent (includes open position value)
        3. Bear Shield not active (BTC above 4h EMA50)
        4. Regime filter: BTC score ≥ regime_min_score (RISK_ON)
        5. Signal score ≥ min_buy_score
        6. Sentiment guard: no bad-news keywords (optional)
        7. Open positions < max_open_positions
        8. MTF trend (1h SMA crossover) is bullish
        9. Trading hours window (optional, disabled by default)
       10. Volume filter: latest candle ≥ 30% of 20-candle avg
        │
        ▼
3. kraken_interface.py
   KrakenAPI.place_order()  →  Kraken REST API
        │  order lock acquired first (order_lock.py)
        │  post-only (maker) by default; market fallback if needed
        ▼
   trade journalled to:
        reports/trade_journal.csv
        logs/trade_events.jsonl
        Telegram notification sent via core/notifier.py
```

---

## ⚙️ Key Config Parameters

All settings live in `config.toml`.  The most important ones:

| Parameter | Section | Default | Description |
|---|---|---|---|
| `trade_pairs` | `[bot_settings]` | 4 EUR pairs | Which pairs to trade (Kraken altname format) |
| `trade_amount_eur` | `[bot_settings.trade_amounts]` | 20.0 | Base EUR per trade (auto-scaled by ATR & regime) |
| `loop_interval_seconds` | `[bot_settings]` | 30 | Main loop interval in seconds (hot-reloadable) |
| `min_buy_score` | `[risk_management]` | 12.0 | Minimum signal score required to open a long |
| `take_profit_percent` | `[risk_management]` | 4.5 | Minimum % gain before the bot sells |
| `hard_stop_loss_percent` | `[risk_management]` | 2.5 | Hard stop-loss % below entry |
| `enable_bear_shield` | `[bear_shield]` | true | Park in FIAT when BTC is below 4h EMA50 |
| `bear_ema_period` | `[bear_shield]` | 50 | EMA period on 4h chart for bear detection |
| `bear_confirm_candles` | `[bear_shield]` | 3 | Consecutive 4h closes below EMA to trigger |
| `enabled` | `[shorting]` | false | Allow opening leveraged short positions |
| `enable_regime_filter` | `[risk_management]` | true | RISK_ON/RISK_OFF based on BTC benchmark score |
| `enable_atr_stop` | `[risk_management]` | true | Use ATR-based trailing stop |
| `atr_multiplier` | `[risk_management]` | 2.5 | Initial ATR stop distance at entry (× ATR) |
| `atr_trail_multiplier` | `[risk_management]` | 3.0 | Ratchet distance as price moves up (× ATR) |
| `enable_break_even` | `[risk_management]` | true | Move SL to entry after 1.5% gain |
| `trade_cooldown_seconds` | `[risk_management]` | 3600 | Per-pair cooldown between trades |
| `global_trade_cooldown_seconds` | `[risk_management]` | 1800 | Global cooldown between any two trades |
| `volume_filter_min_ratio` | `[risk_management]` | 0.3 | Skip buy if volume < 30% of 20-candle avg |
| `max_open_positions` | `[risk_management]` | 2 | Max simultaneous long positions |
| `nas_root` | `[paths]` | `/mnt/fritz_nas/Volume/kraken` | NAS mount point |

> **Tip:** Edit `config.toml` while the bot is running — it hot-reloads every 5 minutes automatically.

---

## 📂 Scripts Overview

| Script | Purpose |
|---|---|
| `setup_telegram.py` | **One-time setup** — finds your Telegram chat ID |
| `monitor_bot.sh` | **Watchdog** — legacy cron fallback (systemd preferred) |
| `rotate_logs.sh` | **Log rotation** — manual fallback (systemd RotatingFileHandler is primary) |
| `weekly_report.py` | **Weekly report** — reads NAS trade history, outputs P&L/win-rate summary |
| `collect_kraken_history.py` | Download full OHLC + trade history from Kraken REST API |
| `collect_kraken_history_incremental.py` | Incremental OHLC update (faster, appends to existing files) |
| `collect_2026_incremental.sh` | Shell wrapper for the 2026 incremental OHLC collection |
| `fill_missing_ohlc.py` | Detects and backfills gaps in downloaded OHLC candle data |
| `collect_15m_daytrading.py` | Collect 15m candles for intraday backtests |
| `backtest_daytrading_15m.py` | Daytrading backtest: EMA crossover + RSI + ATR-TP on 15m data |
| `backtest_daytrade_rsi_mr.py` | Mean-reversion-specific backtest on 15m candles |
| `backtest_v3_detailed.py` | Full-featured swing-trade backtest with detailed per-trade output |
| `sweep_v3.py` | Parameter sweep over `backtest_v3_detailed` — finds optimal RSI/TP/ATR config |

---

## 📂 NAS Layout

```
/mnt/fritz_nas/Volume/kraken/
├── 2026/
│   ├── XXBTZEUR/ohlc_5m.csv    # 5-min OHLC (used for startup history seeding)
│   ├── XETHZEUR/ohlc_5m.csv
│   ├── SOLEUR/ohlc_5m.csv
│   ├── XXRPZEUR/ohlc_5m.csv
│   └── trade_history/
│       └── trades_2026.json    # cached Kraken trade history (VWAP source)
├── 2025/ohlcvt/                # 2025 OHLC archives
└── bot_cache/                  # pre-computed indicator data
```

---

- [Setup Guide](SETUP_GUIDE.md) — detailed installation and configuration walkthrough
- [Changelog](CHANGELOG.md) — full history of changes and improvements
- `scripts/` — backtesting, data collection, and research tools

---

## ⚖️ Disclaimer

This software is for educational purposes. Trading cryptocurrency involves significant risk. Past backtest performance does not guarantee future results. The authors are not responsible for any financial losses.

---

*Active development — contributions and feedback welcome.*


[![Watch Live](https://img.shields.io/badge/▶_Watch_Live-YouTube-red?style=for-the-badge&logo=youtube)](https://www.youtube.com/@TheEfficientDev)
[![Trading Bot](https://img.shields.io/badge/Trading_Bot-GitHub-181717?style=for-the-badge&logo=github)](https://github.com/irgendwasmitfelix/TradingBot)
[![Portfolio](https://img.shields.io/badge/Portfolio-felix-helleckes.github.io-0a66c2?style=for-the-badge&logo=github)](https://felix-helleckes.github.io)

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

## 🏗️ Architecture Overview

```
tradingbot/
├── main.py              # Entry point: arg parsing, logging setup, single-instance lock
├── trading_bot.py       # Core engine: TradingBot class + Backtester (1 800 lines)
├── analysis.py          # Signal engine: TechnicalAnalysis (RSI, SMA, Bollinger Bands)
├── kraken_interface.py  # Kraken API wrapper: rate-limit backoff, order locking
├── price_action.py      # Bar-pattern helpers: wick ratio, engulfing, breakout squeeze
├── utils.py             # Shared utils: load_config(), validate_config(), nas_paths()
├── order_lock.py        # File-based exclusive lock to prevent duplicate orders
├── config.toml          # Single source of truth for all runtime parameters
├── reports/             # Trade journal CSV + weekly HTML reports
├── logs/                # bot_activity.log, trade_events.jsonl, monitor.log
├── data/                # history_buffer.json (RSI/SMA warm-up cache)
└── scripts/             # Ops, backtesting, data-collection, and reporting tools
```

| File | Role |
|---|---|
| `main.py` | Bootstraps logging, parses `--test` flag, enforces single-instance lock |
| `trading_bot.py` | All strategy logic, risk management, order execution, state management |
| `analysis.py` | Pure signal generation — no Kraken calls, no side effects |
| `kraken_interface.py` | Every Kraken API call lives here; handles rate limits transparently |
| `price_action.py` | Optional bar-pattern utilities (not wired into live signals by default) |
| `utils.py` | Config I/O and NAS path resolution — no strategy logic |
| `order_lock.py` | `acquire_order_lock()` prevents simultaneous order submissions |

---

## 🔄 Signal Flow

A trade decision flows through four layers:

```
1. price_action.py  ──►  (optional bar-pattern context)
                                │
2. analysis.py                  │
   TechnicalAnalysis             │
   .generate_signal_with_score() ◄─── live ticker price
        │
        │  Mean-reversion path (enable_mr_signals):
        │    RSI < mr_rsi_oversold  → BUY  score +
        │    RSI > mr_rsi_overbought→ SELL score −
        │
        │  Trend/breakout path (enable_trend_signals):
        │    price > Bollinger upper + RSI ≥ 55 → BUY  score +
        │    price < Bollinger lower + RSI ≤ 45 → SELL score −
        │
        │  returns (signal: str, score: float  in  [−50, +50])
        ▼
3. trading_bot.py
   TradingBot.analyze_all_pairs()
        │  picks highest |score| pair
        ▼
   TradingBot.start_trading()  — layered BUY guards:
        1. Not in loss-streak pause
        2. Daily drawdown limit not exceeded
        3. Bear Shield not active (BTC above 4h EMA50)
        4. Regime filter: BTC score ≥ regime_min_score (RISK_ON)
        5. Signal score ≥ min_buy_score
        6. Sentiment guard: no bad-news keywords (optional)
        7. Open positions < max_open_positions
        8. MTF trend (1h SMA crossover) is bullish
        9. Trading hours window (optional)
       10. Volume filter: latest 15m candle ≥ 50% of 20-candle avg
        │
        ▼
4. kraken_interface.py
   KrakenAPI.place_order()  →  Kraken REST API
        │  order lock acquired first (order_lock.py)
        │  post-only (maker) by default; market fallback if needed
        ▼
   trade journalled to:
        reports/trade_journal.csv
        logs/trade_events.jsonl
```

---

## ⚙️ Key Config Parameters

All settings live in `config.toml`.  The most important ones:

| Parameter | Section | Default | Description |
|---|---|---|---|
| `trade_pairs` | `[bot_settings]` | 4 EUR pairs | Which pairs to trade (Kraken altname format) |
| `trade_amount_eur` | `[bot_settings.trade_amounts]` | 20.0 | Base EUR per trade (auto-scaled by ATR & regime) |
| `min_buy_score` | `[risk_management]` | 15.0 | Minimum signal score required to open a long |
| `take_profit_percent` | `[risk_management]` | 6.5 | Minimum % gain before the bot sells |
| `stop_loss_percent` | `[risk_management]` | 2.0 | Hard stop-loss % below entry |
| `enable_bear_shield` | `[bear_shield]` | false | Park in FIAT when BTC is below 4h EMA50 |
| `bear_ema_period` | `[bear_shield]` | 50 | EMA period on 4h chart for bear detection |
| `bear_confirm_candles` | `[bear_shield]` | 3 | Consecutive 4h closes below EMA to trigger |
| `enabled` | `[shorting]` | true | Allow opening leveraged short positions |
| `leverage` | `[shorting]` | "2" | Leverage for margin shorts (Kraken format) |
| `max_short_notional_eur` | `[shorting]` | 50.0 | Maximum EUR notional per short position |
| `enable_regime_filter` | `[risk_management]` | true | RISK_ON/RISK_OFF based on BTC benchmark score |
| `regime_min_score` | `[risk_management]` | −10.0 | BTC score threshold for RISK_ON |
| `enable_atr_stop` | `[risk_management]` | true | Use ATR-based trailing stop instead of fixed % |
| `atr_multiplier` | `[risk_management]` | 1.5 | Initial ATR stop distance at entry (× ATR) |
| `atr_trail_multiplier` | `[risk_management]` | 2.0 | Ratchet distance as price moves up (× ATR) |
| `enable_break_even` | `[risk_management]` | true | Move SL to entry after 1.5% gain |
| `trade_cooldown_seconds` | `[risk_management]` | 3600 | Per-pair cooldown between trades |
| `max_open_positions` | `[risk_management]` | 2 | Max simultaneous long positions |
| `enable_volume_filter` | `[risk_management]` | true | Skip buy if volume < 50% of 20-candle avg |
| `nas_root` | `[paths]` | `/mnt/fritz_nas/Volume/kraken` | NAS mount point |

> **Tip:** Edit `config.toml` while the bot is running — it hot-reloads every 5 minutes automatically.

---

## 📂 Scripts Overview

| Script | Purpose |
|---|---|
| `monitor_bot.sh` | **Watchdog** — called by cron every 5 min; restarts `main.py` if not running |
| `rotate_logs.sh` | **Log rotation** — clears `bot.log` (keeps `.bak`) and trims `monitor.log` to 500 lines |
| `weekly_report.py` | **Weekly report** — reads NAS trade history, outputs a P&L/win-rate summary to `reports/` |
| `collect_kraken_history.py` | Download full OHLC + trade history from Kraken REST API |
| `collect_kraken_history_incremental.py` | Incremental OHLC update (faster, appends to existing files) |
| `collect_2026_incremental.sh` | Shell wrapper for the 2026 incremental OHLC collection |
| `fill_missing_ohlc.py` | Detects and backfills gaps in downloaded OHLC candle data |
| `collect_15m_daytrading.py` | Collect 15m candles for intraday backtests |
| `backtest_daytrading_15m.py` | Daytrading backtest: EMA crossover + RSI + ATR-TP on 15m data |
| `backtest_daytrade_rsi_mr.py` | Mean-reversion-specific backtest on 15m candles |
| `backtest_v3_detailed.py` | Full-featured swing-trade backtest with detailed per-trade output |
| `sweep_v3.py` | Parameter sweep over `backtest_v3_detailed` — finds optimal RSI/TP/ATR config |
| `prod_dev_yearly_backtest.py` | Annual backtest comparing production vs development configs |
| `main_dev_local_robust_eval.py` | Robust local evaluation with multiple years of data |
| `mentor_beta_challenge_loop.py` | Iterative strategy improvement challenge loop |
| `mentor_beta_review.py` | Review script for mentor/beta strategy validation |
| `release_gate_prod_dev.py` | Gate check before promoting dev config to production |
| `autosim_main_dev_loop.sh` | Automated simulation loop for development |
| `autosim_runner.sh` | Runner wrapper for the autosim loop |
| `notify_pause.sh` | Called by the bot when a trading pause activates (e.g. loss streak) |

---

## 🕐 Cron Jobs

The bot runs on a Raspberry Pi with the following scheduled jobs:

```cron
# Bot watchdog — restart if crashed (every 5 minutes)
*/5 * * * * /home/felix/tradingbot/scripts/monitor_bot.sh

# Log rotation — clear bot.log every Sunday at 03:00
0 3 * * 0 /home/felix/tradingbot/scripts/rotate_logs.sh

# NAS sync — sync reports/logs to NAS every Sunday at 03:30
30 3 * * 0 rsync -a /home/felix/tradingbot/reports/ /mnt/fritz_nas/Volume/kraken/reports/

# Weekly P&L report — generate summary every Sunday at 04:00
0 4 * * 0 cd /home/felix/tradingbot && python scripts/weekly_report.py >> logs/weekly_report.log 2>&1
```

**NAS layout** (`/mnt/fritz_nas/Volume/kraken/`):

```
kraken/
├── 2025/ohlcvt/          # 2025 OHLC candle archives (gzipped CSV)
├── 2026/                 # 2026 OHLC + trade history
│   └── trade_history/trades_2026.json
├── bot_cache/            # Pre-computed indicator data
└── reports/              # Synced weekly reports
```

---

- [Setup Guide](SETUP_GUIDE.md) — detailed installation and configuration walkthrough
- [Changelog](CHANGELOG.md) — full history of changes and improvements
- `scripts/` — backtesting, data collection, and research tools

---

## ⚖️ Disclaimer

This software is for educational purposes. Trading cryptocurrency involves significant risk. Past backtest performance does not guarantee future results. The authors are not responsible for any financial losses.

---

*Active development — contributions and feedback welcome.*
