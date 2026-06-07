# 🤖 Kraken Trading Bot

[![Watch Live](https://img.shields.io/badge/▶_Watch_Live-YouTube-red?style=for-the-badge&logo=youtube)](https://www.youtube.com/@TheEfficientDev)
[![Trading Bot](https://img.shields.io/badge/Trading_Bot-GitHub-181717?style=for-the-badge&logo=github)](https://github.com/felix-helleckes/TradingBot)
[![Portfolio](https://img.shields.io/badge/Portfolio-felix--helleckes.github.io-0a66c2?style=for-the-badge&logo=github)](https://felix-helleckes.github.io/)

An automated, signal-driven spot trading bot for [Kraken](https://www.kraken.com) — built for EUR pairs, designed to be lean, transparent, and safe to run with real money.

> ⚠️ **This bot executes real trades.** Always start with a small amount and monitor logs closely. Never risk more than you can afford to lose.

---

## ✨ Features

- **Multi-pair trading** — BTC, ETH, SOL, XRP (EUR pairs, configurable)
- **Dual signal engine** — Mean-reversion (RSI) + trend breakout (Bollinger Bands)
- **Smart entry filters** — volume filter, regime filter, score threshold, per-pair cooldowns
- **Fee-aware exits** — take‑profit includes Kraken fee buffer (maker + taker)
- **Risk controls** — ATR trailing stop, cooldowns, drawdown circuit breaker
- **Regime filter** — switches to risk‑off sizing in bear markets (BTC benchmark)
- **Bear Shield** — parks everything in FIAT when BTC drops below 4h EMA50
- **Position recovery** — reconstructs holdings and PnL from Kraken trade history on restart
- **Cooldown persistence** — per‑pair cooldown state survives restarts (no immediate re‑buy)
- **Telegram notifications** — instant alerts on every trade and critical error (optional)
- **Systemd service** — auto‑restart on crash, watchdog heartbeat, rate‑limiting
- **Log rotation** — `RotatingFileHandler` keeps logs at ≤5 MB × 5 backups
- **Short‑selling support** — leveraged shorts (configurable) with Felix’s rules:
  - Open only in confirmed downtrend (bearish EMA crossover + negative score)
  - Close only on real net profit after fees **or** on an early bullish signal (BUY) to avoid adverse moves
- **Hot‑reload configuration** – `config.toml` is checked every 5 minutes and applied without restart

---

## 🚀 Quick Start

**1. Clone and install dependencies**
```bash
git clone https://github.com/felix-helleckes/TradingBot.git
cd TradingBot
pip install -r requirements.txt
```

**2. Set up API credentials**
```bash
cp .env.example .env
# Edit .env and add your Kraken API key and secret
```
> Create a Kraken API key with **Trade** permissions only. Never enable withdrawals.

**3. (Optional) Enable Telegram notifications**
- Fill `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`
- The notifier is active by default; set `ENABLE_TELEGRAM=false` in `config.toml` to disable.

**4. Configure the bot**
Edit `config.toml` to set your capital, pairs, and risk parameters. Example:
```toml
trade_amount_eur = 20.0       # EUR per trade
initial_balance = 100.0       # your starting balance
target_balance_eur = 150.0    # stop target (optional)
```

**5. Run the bot**
```bash
python main.py
```
Or as a systemd service (recommended for 24/7 operation):
```bash
sudo cp kraken-bot.service /etc/systemd/system/
sudo systemctl enable --now kraken-bot
sudo journalctl -u kraken-bot -f   # follow live logs
```

---

## 📁 Project Structure

| File / Directory | Purpose |
|------------------|---------|
| `main.py` | Entry point, logging setup, single‑instance lock, dotenv load |
| `trading_bot.py` | Core engine: strategy logic, order execution, risk management, state |
| `analysis.py` | Technical indicators and signal scoring (RSI, SMA, Bollinger Bands) |
| `kraken_interface.py` | Kraken API wrapper with rate‑limit backoff and order locking |
| `utils.py` | Config loading, validation, NAS path helpers |
| `core/notifier.py` | Telegram notifications (reads `TELEGRAM_TOKEN`/`CHAT_ID` from `.env`) |
| `order_lock.py` | File‑based exclusive lock to prevent duplicate orders |
| `config.toml` | Single source of truth for all runtime parameters |
| `logs/` | Rotating logs (`bot_activity.log`) and trade events JSONL |
| `data/` | Persistent state: history buffer, cooldowns, PnL, short positions |
| `reports/` | Trade journal CSV and optional HTML reports |
| `scripts/` | Ops, backtesting, data‑collection, and reporting tools |
| `kraken-bot.service` | systemd unit file (Restart=always, WatchdogSec=120) |

---

## ⚙️ How It Works

Each loop (~30 seconds) the bot:

1. **Fetches live ticker prices** for all configured pairs.
2. **Seeds/updates price history** from local 5‑minute OHLC files (NAS) – no API warm‑up wait.
3. **Generates a signal score** using RSI (mean‑reversion) and Bollinger Bands (trend/breakout).
4. **Applies entry filters**:
   - Volume ≥ 30 % of 20‑candle average
   - Regime filter (BTC‑based RISK_ON/RISK_OFF — derzeit deaktiviert)
   - Score threshold (`min_buy_score`)
   - Per‑pair and global cooldowns
   - EMA trend confirmation (1h EMA20/50 crossover) für Longs / Shorts
5. **Executes the best‑scoring action**:
   - **Long**: opens a BUY if all guards pass and signal is BUY.
   - **Short**: opens a leveraged short only if:
     - Shorting enabled in config
     - Confirmed bearish 1h EMA trend (`not _is_ema_trend_bullish`)
     - Score ≤ `-min_buy_score`
     - (Bei aktiviertem Regime-Filter zusätzlich: Risk‑off)\n   - **Exit logic**:
     - Longs are closed only when `_can_sell_profit_target` is true (real net profit after fees) **or** by a hard stop/ATR/time stop (these bypass the profit gate).
     - Shorts are closed when:
       - `_can_close_short_profit_target` is true (real net profit after fees) **OR**
       - An opposing bullish signal (BUY) appears – early close to avoid adverse move (added 2026‑06‑03).
     - All stop‑loss mechanisms (hard stop, ATR trailing, time stop) always execute regardless of profit target.

All order placement goes through `kraken_interface.py`, which acquires an exclusive lock first to avoid duplicate submissions.

---

## 🛡️ Risk Management

| Control | Default | Description |
|---------|---------|-------------|
| Take‑profit (long) | 3.0 % + fees | Minimum gain before selling |
| Take‑profit (short) | 1.5 % | Minimum gain before buying back |
| ATR trailing stop | 2.0 × ATR | Dynamic stop that ratchets up with price |
| Trade cooldown | 60 min/pair | Prevents overtrading the same instrument |
| Global cooldown | 60 min | Minimum gap between any two trades |
| Max open positions | 1 | Limits concurrent exposure |
| Drawdown circuit breaker | 10 % portfolio | Pauses buys after large portfolio drop |
| Short‑selling leverage | 2.0× (config) | Leverage for leveraged shorts |
| Max short notional | 25 EUR (config) | Cap per‑short to limit tail risk |

---

## 🔧 Monitoring & Ops

The bot runs as a **systemd service** — no external watchdog needed:

```bash
sudo systemctl status kraken-bot       # check status
sudo journalctl -u kraken-bot -f       # follow live logs
sudo systemctl restart kraken-bot      # restart after config change
```

Systemd provides:
- `Restart=always` – auto‑restart on crash
- `WatchdogSec=120` – kills and restarts if the bot hangs > 120 s
- Rate‑limiting (max 5 restarts / 5 min) to avoid tight‑loop failures

Logs are rotated automatically by Python’s `RotatingFileHandler` (5 MiB per file, 5 backups).

---

## 📅 Recent Changes

See **[CHANGELOG.md](./CHANGELOG.md)** for the full history.

**2026‑06‑07** — Bugfixes: Short-Blocker (Regime-Filter) & Doppelfilter (MTF vs EMA) behoben. Config vereinfacht. Watchdog + Daily-Report per Cron.

**2026‑06‑03** — Early Short‑Close auf BUY‑Signal. Systemd‑Service stabilisiert.

**2026‑06‑02** — Short‑Logik (Downtrend‑Only + Net‑Profit‑Only), Airbag deaktiviert.

---

## ⚖️ Disclaimer

This software is for educational purposes. Trading cryptocurrency involves significant risk. Past backtest performance does not guarantee future results. The authors are not responsible for any financial losses.

> 💡 **Tip**: Start with a small amount (e.g., 20–50 EUR) and observe the bot’s behavior for at least one full cycle before increasing exposure.

---

*Active development — contributions and feedback welcome.*

[![Watch Live](https://img.shields.io/badge/▶_Watch_Live-YouTube-red?style=for-the-badge&logo=youtube)](https://www.youtube.com/@TheEfficientDev)
[![Trading Bot](https://img.shields.io/badge/Trading_Bot-GitHub-181717?style=for-the-badge&logo=github)](https://github.com/felix-helleckes/TradingBot)
[![Portfolio](https://img.shields.io/badge/Portfolio-felix--helleckes.github.io-0a66c2?style=for-the-badge&logo=github)](https://felix-helleckes.github.io/)