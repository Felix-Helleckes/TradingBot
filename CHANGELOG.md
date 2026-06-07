# Changelog

## 2026-06-07 — Bugfixes & Stabilisierung

### 🔧 Bugfixes
- **fix(shorts): Shorts permanent blockiert bei deaktiviertem Regime-Filter**  
  `_is_risk_on_regime()` gab bei `enable_regime_filter = false` immer `True` zurück.  
  Shorts wurden nie geöffnet, weil `not True` → `False`.  
  Fix: Regime-Check nur bei aktiviertem Filter; sonst reichen *bearish Trend + negative Score*.
- **fix(trend): Doppelfilter MTF-Trend (SMA) vs. EMA-Trend behoben**  
  `_is_mtf_trend_bullish` (SMA20/50, lokaler Cache) und `_is_ema_trend_bullish` (EMA20/50, 1h-OHLC)  
  nutzten unterschiedliche Datenquellen → Deadlock: BUYs geblockt (EMA bearish), Shorts geblockt (MTF bullish).  
  Fix: Beide Pfade nutzen jetzt einheitlich EMA20/50 auf 1h-OHLC.

### ⚡ Optimierungen
- **Config vereinfacht** — Regime-Filter, Pyramiding, Partial-Exit, Break-Even, MTF-MACD,  
  Volume-Filter, Daily-Drawdown, Volatility-Targeting deaktiviert (Over-Engineering entfernt)
- **Take-Profit 3.0 %** (Long), **Short-TP 1.5 %** (Short) — ohne Stop-Loss  
  (Felix-Regel: nie bei Verlust schließen, nur bei echtem Nettogewinn)
- **4 Handelspaare**: XXBTZEUR, XETHZEUR, SOLEUR, XXRPZEUR
- **Trade-Cooldown 1 h** — hektisches Overtrading vermieden

### 🛡️ Ops
- **Watchdog-Cronjob** (alle 5 min): prüft Bot-Status, startet bei Crash neu
- **Daily-Report** (08:00 Uhr): Telegram-Nachricht mit Balance, Trades, P&L
- **Backup & Git-Commit** aller relevanten Dateien vor Änderungen

---

## 2026-06-03 — Early Short-Close & Systemd

- **feat(shorts): Early-Close auf BUY-Signal**  
  Bei einem bullishen Signal wird ein offener Short sofort geschlossen,  
  unabhängig vom aktuellen PnL — verhindert adverse Moves gegen die Position.
- **fix(systemd): Kraken-Bot Service** stabilisiert (stale lock cleanup)
- **docs(README)**: Features, Short-Logik, Risk-Management dokumentiert
- **push**: Branch `auto/per-symbol-dot-20260529`

---

## 2026-06-02 — Short-Logik & Airbag

- **fix(shorts)**: Open nur in bestätigtem Downtrend (bearish MTF + risk-off + negative score)
- **fix(shorts)**: Close nur bei echtem Nettogewinn nach Fees
- **fix(airbag)**: Airbag deaktiviert (Threshold 99 % — verhindert Fehlsells)
- **fix(pairs)**: Pair-Handling normalisiert (Groß-/Kleinschreibung)

---

## 2026-06-01 — Short-Close & Persistenz

- **fix(close)**: Short-Close nutzt `reduce_only` und rundet Volumes auf Exchange-Minimum
- **fix(persist)**: `most-recent-buy` persistiert bei Phantom-Positionen; Rate-Limit 60 s
- **chore(rebuild)**: `purchase_prices.json` aus Logs rekonstruiert nach Recovery-Run

---

## 2026-05-30 — DOTEUR & Helpers

- **test**: Fokussierte DOTEUR-Verify-Outputs
- **chore**: Helper-Skripte hinzugefügt

---

*Letzte Commits siehe [GitHub](https://github.com/felix-helleckes/TradingBot/commits/main)*
