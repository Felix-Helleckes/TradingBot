# 🤖 KiDev Handoff – Kraken Trading Bot

> Übergabe-Dokument für zukünftige Copilot-Sessions.  
> Zuletzt aktualisiert: 2026-04-06 | Letzter Commit: `1c26ee0`

---

## 📍 Aktueller Stand

- Bot läuft **24/7 auf einem Raspberry Pi** als systemd-Service `kraken-bot`
- Kapital: ~**100 EUR**, 4 Paare: BTC, ETH, SOL, XRP
- Status: **RISK_ON / ACTIVE**, ~140+ Trades insgesamt
- Take-Profit: **3.5%** (gesenkt von 4.5% am 06.04.2026)
- Alle kritischen Bugs behoben (siehe unten)

---

## 🐛 Behobene kritische Bugs (chronologisch)

### 1. `_count_open_positions` Crash (`ccb2b57`)
- **Bug:** `self.pairs` statt `self.trade_pairs` → `AttributeError` bei jedem Loop
- **Fix:** Variable korrigiert

### 2. Spot-SELL durch Caps blockiert (`60ac605`)
- **Bug:** `kraken_interface.py` prüfte Short-Exposure-Caps auch für Spot-Sells → Verkäufe blockiert
- **Fix:** `is_spot_sell` Early-Exit in `place_order()` vor dem Cap-Check

### 3. Stop-Loss TP-Gate Bug (`5f8fb6b`) ⚠️ KRITISCH
- **Bug:** `execute_sell_order()` wurde mit `require_profit_target=True` aufgerufen → ATR/HARD_STOP/TRAILING stops wurden blockiert. Log: `SELL blocked: 4.50% not reached`
- **Fix:** `_stop_types = {"ATR", "ATR_TRAIL", "HARD_STOP", "BREAK_EVEN", "TIME_STOP", "TRAILING_STOP"}` → wenn `risk_type in _stop_types` → `require_profit_target=False`

### 4. Portfolio-Drawdown False Trigger (`5f8fb6b`) ⚠️ KRITISCH
- **Bug:** Drawdown wurde nur auf EUR-Cash berechnet. Nach 20€ BUY → Cash -20€ → Bot berechnete ~20% Drawdown → pausierte 60min nach JEDEM Trade
- **Fix:** `portfolio_value = EUR_cash + Σ(holdings × current_prices)`

### 5. VWAP Phantom-Positionen (`b14a920`)
- **Bug:** `load_purchase_prices_from_history()` replayed alle 292 Trades inkl. alter Sessions → 73+ Phantom-XRP → falscher Avg-Entry 1.187 statt 1.164
- **Fix:** Wenn `history_qty > live_qty * 1.10` → Fallback auf letzten BUY-Preis aus History

### 6. Telegram Import-Order Bug (`b14a920`)
- **Bug:** `core/notifier.py` liest `TELEGRAM_TOKEN` bei Modulimport. `from trading_bot import` passierte VOR `load_dotenv()` in `main.py` → Token immer leer
- **Fix:** `load_dotenv()` an erste Stelle in `main.py` (vor allen anderen Imports)

---

## ⚙️ Aktuelle Config (`config.toml`)

| Parameter | Wert |
|---|---|
| `loop_interval_seconds` | 30 |
| `take_profit_percent` | **3.5%** |
| `hard_stop_percent` | 2.5% |
| `atr_multiplier` | 2.5 |
| `atr_trail_multiplier` | 3.0 |
| `min_buy_score` | 12.0 |
| `volume_filter_min_ratio` | 0.3 |
| `trade_cooldown_seconds` | 3600 |
| `global_trade_cooldown_seconds` | 1800 |
| `mtf_regime_min_score` | -5.0 |
| `enable_trend_breakout_signals` | true |
| `enable_trading_hours` | false |
| `shorting.enabled` | false |
| `adaptive_take_profit` | true |
| `max_take_profit_percent` | 9.0 |

---

## 🏗️ Architektur-Überblick

```
main.py  →  trading_bot.py (TradingBot)
               ├── analysis.py (TechnicalAnalysis – Signals, RSI, ATR, MTF)
               ├── kraken_interface.py (KrakenAPI – Orders, Balance)
               └── core/notifier.py (Telegram)
```

- Config hot-reload alle 300s (kein Restart nötig für Config-Änderungen)
- Telegram-Credentials in `.env` (nicht in Git!)
- Cooldown-State: `data/cooldown_state.json`
- P&L-Baseline: `data/pnl_state.json` (`start_eur: 100.0`)
- History-Buffer: `data/history_buffer.json` (200 Candles pro Pair)

---

## 📁 NAS-Anbindung

- **Pfad:** `/mnt/fritz_nas/Volume/kraken/2026/`
- **5m OHLC:** `{FOLDER}/ohlc_5m.csv` — wird beim Start in den History-Buffer geladen (Warmup)
- **Trade-History:** `trade_history/trades_2026.json` (VWAP-Quelle, ~292 Trades)
- **Folder-Mapping:** XBTEUR→XXBTZEUR, ETHEUR→XETHZEUR, XRPEUR→XXRPZEUR, SOLEUR→SOLEUR
- CSV-Format: `ts,open,high,low,close,vwap,volume,count`

---

## 🚀 Systemd Service

```bash
sudo systemctl status kraken-bot    # Status prüfen
sudo systemctl restart kraken-bot   # Neu starten
sudo journalctl -u kraken-bot -f    # Live-Logs
```

- `StartLimitBurst=5`, `WatchdogSec=120`, `RestartSec=30`

---

## 📌 Offene Punkte / Nice-to-have

| Prio | Thema | Details |
|---|---|---|
| Medium | SD-Card-Wear | `history_buffer.json` wird alle 30s geschrieben → NAS-Mirror wäre besser, aber NAS-Ausfall wäre neues Risiko |
| Low | Backtest-Validierung | `scripts/sweep_v3.py` mit NAS-Daten gegen aktuelle Config laufen lassen |
| Low | Kelly Criterion | Teilweise implementiert, aber unklar ob korrekt verdrahtet in `execute_buy_order()` |
| Low | Pair-Korrelation | Kein Check ob XRP+BTC gleichzeitig perfekt korreliert sind beim Kaufen |
| Low | CHANGELOG.md | Nicht aktuell gehalten |

---

## 💡 Wichtige Code-Stellen

| Was | Datei | Zeile (ca.) |
|---|---|---|
| Stop-Loss TP-Gate Fix | `trading_bot.py` | ~1667 (`_stop_types`) |
| Portfolio-Drawdown | `trading_bot.py` | ~1678 |
| VWAP Phantom Fix | `trading_bot.py` | ~921 |
| Trade-History Replay | `trading_bot.py` | ~840 (`load_purchase_prices_from_history`) |
| TP/Stop Check | `trading_bot.py` | ~1415 (`check_take_profit_or_stop_loss`) |
| Sell-Order Ausführung | `trading_bot.py` | ~1990 (`execute_sell_order`) |
| Spot-SELL Bypass | `kraken_interface.py` | ~256 (`is_spot_sell`) |
| NAS 5m Seeding | `analysis.py` | ~105 (`seed_from_nas_ohlc`) |
| Signal-Generierung | `analysis.py` | ~209 (`generate_signal_with_score`) |
| Telegram-Token Fix | `main.py` | Zeile 1-15 (`load_dotenv` first) |

---

## 🧪 Zuletzt geprüfte Log-Signale (April 2026)

- ATR-Stop feuert korrekt und wird ausgeführt (nach Fix): `[ATR] XRPEUR at -2.02% → SELL executed`
- Portfolio-Drawdown bleibt stabil nach BUY (kein falscher Pause-Trigger mehr)
- Telegram-Notifications: eingehend nach Fix ✅

---

*Dieses File nicht löschen — es ist die Gedächtnisbrücke zwischen Sessions!*
