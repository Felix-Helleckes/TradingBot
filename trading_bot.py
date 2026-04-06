# Trading Bot Core Logic - Multi-Pair Analysis
"""
Kraken Trading Bot — Core Engine
=================================
This module is the heart of the trading bot.  It contains the ``TradingBot``
class that orchestrates the full trading lifecycle, plus a minimal ``Backtester``
helper for offline strategy validation.

Signal flow (high level)
------------------------
::

    price_action.py  (bar-pattern helpers — optional context)
         │
    analysis.py  TechnicalAnalysis.generate_signal_with_score()
         │         ↳ RSI mean-reversion  (enable_mr_signals)
         │         ↳ Bollinger-Band breakout (enable_trend_signals)
         │         returns (signal: str, score: float  [-50 … +50])
         │
    TradingBot.analyze_all_pairs()
         │         ↳ fetches live ticker prices for all configured pairs
         │         ↳ seeds price history from 60m OHLC when too sparse
         │         ↳ picks the highest-scoring actionable pair
         │
    TradingBot.start_trading()  — main loop (~60 s cycle)
         │         ↳ check_take_profit_or_stop_loss()  (exits first)
         │         ↳ layered BUY guards (see below)
         │         ↳ execute_buy_order() / execute_sell_order()
         │              execute_open_short_order() / execute_close_short_order()
         │
    kraken_interface.py  KrakenAPI.place_order()
                          ↳ exclusive order lock (order_lock.py)
                          ↳ exponential back-off on rate-limit errors

Layered BUY entry guards (all must pass before a buy is placed)
---------------------------------------------------------------
1. Not temporarily paused (loss-streak cooldown)
2. Daily drawdown limit not hit
3. Bear Shield not active (BTC above 4h EMA50)
4. Regime filter: BTC benchmark score ≥ regime_min_score (RISK_ON)
5. Signal score ≥ min_buy_score (default 15.0)
6. Sentiment guard: no bad-news keywords in marquee file (optional)
7. Open positions < max_open_positions
8. MTF trend (1h SMA crossover) is bullish
9. Trading hours filter (UTC window, optional)
10. Volume filter: latest 15m candle ≥ volume_filter_min_ratio × 20-candle avg

Key responsibilities of TradingBot
-----------------------------------
- Maintains per-pair state: holdings, entry price, peak price, stop levels,
  short positions, trade metrics, cooldown timestamps.
- Reconciles holdings and average entry price from Kraken trade history on
  startup/restart (``load_purchase_prices_from_history``).
- Hot-reloads ``config.toml`` every 5 minutes — no restart needed for tweaks.
- Writes structured JSONL trade events to ``logs/trade_events.jsonl`` and a
  human-readable CSV to ``reports/trade_journal.csv``.
- Persists the price-history buffer to ``data/history_buffer.json`` so RSI/SMA
  indicators survive a bot restart without a warm-up gap.
- NAS paths (trade history, OHLC archives) are resolved via ``utils.nas_paths()``.

Usage (called from main.py)
---------------------------
::

    from trading_bot import TradingBot
    bot = TradingBot(api_client, config)
    bot.start_trading()
"""

import json
import logging
import time
import os
from datetime import datetime, timezone
from pathlib import Path
from analysis import TechnicalAnalysis
from utils import load_config

# Load .env if python-dotenv is available (graceful fallback otherwise)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        for _line in _env_path.read_text().splitlines():
            if "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from core import notifier as _notifier

# NAS root — read from config [paths] nas_root, fallback to default mount point
def _resolve_nas_root(config: dict) -> Path:
    return Path(config.get('paths', {}).get('nas_root', '/mnt/fritz_nas/Volume/kraken'))
_TRADE_HISTORY_REFRESH_INTERVAL = 600  # seconds between Kraken API fetches (10 min)


def _sd_notify_watchdog() -> None:
    """Send WATCHDOG=1 ping to systemd via the NOTIFY_SOCKET (no extra packages needed)."""
    import socket
    sock_path = os.environ.get("NOTIFY_SOCKET")
    if not sock_path:
        return
    try:
        addr = "\0" + sock_path[1:] if sock_path.startswith("@") else sock_path
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(b"WATCHDOG=1", addr)
    except Exception:
        pass


class TradingBot:
    def __init__(self, api_client, config):
        self.api_client = api_client
        self.config = config
        self.config_path = os.path.join(os.path.dirname(__file__), 'config.toml')
        self.logger = logging.getLogger(__name__)
        self.nas_root = _resolve_nas_root(config)

        self.analysis_tool = TechnicalAnalysis(rsi_period=14, sma_short=20, sma_long=50)

        # Signal engine mode: mean-reversion (reversion_bias) and/or trend/breakout (BB)
        self.enable_mr_signals = bool(self.config.get('risk_management', {}).get('enable_mean_reversion_signals', True))
        self.enable_trend_signals = bool(self.config.get('risk_management', {}).get('enable_trend_breakout_signals', True))
        self.mr_rsi_oversold = float(self.config.get('risk_management', {}).get('mr_rsi_oversold_threshold', 33.0))
        self.mr_rsi_overbought = float(self.config.get('risk_management', {}).get('mr_rsi_overbought_threshold', 67.0))
        self.analysis_tool.enable_mr_signals = self.enable_mr_signals
        self.analysis_tool.enable_trend_signals = self.enable_trend_signals
        self.analysis_tool.mr_rsi_buy = self.mr_rsi_oversold
        self.analysis_tool.mr_rsi_sell = self.mr_rsi_overbought

        self.trade_pairs = self.config['bot_settings'].get('trade_pairs', ['XBTEUR'])
        self.pair_signals = {}
        self.pair_prices = {}
        self.pair_scores = {}
        self.holdings = {}
        self.purchase_prices = {}
        self.peak_prices = {}
        self.position_qty = {}
        self.short_qty = {}
        self.short_entry_prices = {}
        self.realized_pnl = {}
        self.fees_paid = {}
        self.trade_metrics = {}
        self.closed_trade_pnls = []
        self.last_trade_at = {}
        self.entry_timestamps = {}
        self.last_global_trade_at = 0
        self._normalized_pair_logs_seen = set()
        self._last_empty_sell_log_at = {}
        self._load_cooldown_state()

        self.trade_count = 0
        self.consecutive_losses = 0
        self.trading_paused_until_ts = 0
        self.target_balance_eur = self._get_target_balance()
        # stop info per pair (stop_price, type)
        self.stop_info = {}
        # journaling path
        self.journal_path = os.path.join(os.path.dirname(__file__), 'reports', 'trade_journal.csv')
        # structured JSONL trade log for observability
        self.json_journal_path = os.path.join(os.path.dirname(__file__), 'logs', 'trade_events.jsonl')
        os.makedirs(os.path.dirname(self.json_journal_path), exist_ok=True)
        # manual kill-switch file: if present, bot will pause buys
        self.kill_switch_path = os.path.join(os.path.dirname(__file__), 'PAUSE')
        self.take_profit_percent = self._get_take_profit_percent()
        self.stop_loss_percent = self._get_stop_loss_percent()
        self.max_open_positions = int(self.config.get('risk_management', {}).get('max_open_positions', 3))
        self.trade_cooldown_sec = int(self.config.get('risk_management', {}).get('trade_cooldown_seconds', 180))
        self.global_trade_cooldown_sec = int(self.config.get('risk_management', {}).get('global_trade_cooldown_seconds', 300))
        self.trailing_stop_percent = float(self.config.get('risk_management', {}).get('trailing_stop_percent', 1.5))
        self.min_buy_score = float(self.config.get('risk_management', {}).get('min_buy_score', 18.0))
        self.adaptive_tp_enabled = bool(self.config.get('risk_management', {}).get('adaptive_take_profit', True))
        self.max_tp_percent = float(self.config.get('risk_management', {}).get('max_take_profit_percent', 14.0))
        self.sell_fee_buffer_percent = float(self.config.get('risk_management', {}).get('sell_fee_buffer_percent', 0.0))
        self.empty_sell_log_cooldown_sec = int(self.config.get('risk_management', {}).get('empty_sell_log_cooldown_seconds', 1800))
        # ATR stop config
        self.enable_atr_stop = bool(self.config.get('risk_management', {}).get('enable_atr_stop', False))
        self.atr_period = int(self.config.get('risk_management', {}).get('atr_period', 14))
        self.atr_multiplier = float(self.config.get('risk_management', {}).get('atr_multiplier', 1.5))
        self.atr_trail_multiplier = float(self.config.get('risk_management', {}).get('atr_trail_multiplier', 0.75))
        # ATR dynamic take-profit: TP floor = atr_tp_multiplier × ATR%
        self.enable_atr_dynamic_tp = bool(self.config.get('risk_management', {}).get('enable_atr_dynamic_tp', False))
        self.atr_tp_multiplier = float(self.config.get('risk_management', {}).get('atr_tp_multiplier', 2.0))
        
        # Break-even stop-loss
        self.enable_break_even = bool(self.config.get('risk_management', {}).get('enable_break_even', True))
        self.break_even_trigger_pct = float(self.config.get('risk_management', {}).get('break_even_trigger_percent', 1.5))
        
        # pyramiding
        self.enable_pyramiding = bool(self.config.get('risk_management', {}).get('enable_pyramiding', False))
        self.pyramiding_add_pct = float(self.config.get('risk_management', {}).get('pyramiding_add_pct', 0.5))
        self.enable_regime_filter = bool(self.config.get('risk_management', {}).get('enable_regime_filter', True))
        self.regime_benchmark_pair = str(self.config.get('risk_management', {}).get('regime_benchmark_pair', 'XBTEUR')).upper()
        self.regime_min_score = float(self.config.get('risk_management', {}).get('regime_min_score', -5.0))
        self.enable_hard_stop_loss = bool(self.config.get('risk_management', {}).get('enable_hard_stop_loss', True))
        self.hard_stop_loss_percent = float(self.config.get('risk_management', {}).get('hard_stop_loss_percent', 4.0))
        self.enable_mtf_regime_scoring = bool(self.config.get('risk_management', {}).get('enable_mtf_regime_scoring', True))
        self.mtf_regime_min_score = float(self.config.get('risk_management', {}).get('mtf_regime_min_score', -2.0))
        self.enable_time_stop = bool(self.config.get('risk_management', {}).get('enable_time_stop', True))
        self.time_stop_hours = int(self.config.get('risk_management', {}).get('time_stop_hours', 72))
        self.enable_daily_drawdown = bool(self.config.get('risk_management', {}).get('enable_daily_drawdown', True))
        self.daily_drawdown_percent = float(self.config.get('risk_management', {}).get('daily_loss_limit_percent', 3.0))
        self.risk_off_allocation_multiplier = float(self.config.get('risk_management', {}).get('risk_off_allocation_multiplier', 0.35))
        self.enable_volatility_targeting = bool(self.config.get('risk_management', {}).get('enable_volatility_targeting', True))
        self.target_volatility_pct = float(self.config.get('risk_management', {}).get('target_volatility_pct', 1.6))
        self.max_consecutive_losses = int(self.config.get('risk_management', {}).get('max_consecutive_losses', 3))
        self.pause_after_loss_streak_minutes = int(self.config.get('risk_management', {}).get('pause_after_loss_streak_minutes', 180))
        self.enable_live_shorts = bool(self.config.get('shorting', {}).get('enabled', False))
        self.short_leverage = str(self.config.get('shorting', {}).get('leverage', '2'))
        self.max_short_notional_eur = float(self.config.get('shorting', {}).get('max_short_notional_eur', 50.0))
        self.short_take_profit_percent = float(self.config.get('shorting', {}).get('short_take_profit_percent', 2.5))
        self.short_stop_loss_percent = float(self.config.get('shorting', {}).get('short_stop_loss_percent', 3.0))

        # Fast scalp / hit-and-run profile
        self.enable_fast_scalp = bool(self.config.get('profiles', {}).get('fast_scalp', {}).get('enabled', False))
        self.fast_scalp_require_flag = bool(self.config.get('profiles', {}).get('fast_scalp', {}).get('require_enable_flag', True))
        self.fast_scalp_time_stop_minutes = int(self.config.get('profiles', {}).get('fast_scalp', {}).get('time_stop_minutes', 30))
        self.fast_scalp_stop_loss_pct = float(self.config.get('profiles', {}).get('fast_scalp', {}).get('stop_loss_percent', 0.6))
        self.fast_scalp_take_profit_pct = float(self.config.get('profiles', {}).get('fast_scalp', {}).get('take_profit_percent', 1.2))

        self.start_time = datetime.now()
        self.last_config_reload = datetime.now()
        self.config_reload_interval = 300
        self.loop_interval_sec = int(self.config.get('bot_settings', {}).get('loop_interval_seconds', 60))
        self.daily_start_balance = None
        self.initial_balance_eur = None
        self.start_timestamp = int(time.time())
        self.net_deposits_eur = 0.0
        self.net_withdrawals_eur = 0.0
        self._last_cashflow_refresh_ts = 0
        self.cashflow_refresh_interval_sec = int(self.config.get('reporting', {}).get('cashflow_refresh_seconds', 600))
        if self.cashflow_refresh_interval_sec > 300:
            self.logger.warning(
                f"cashflow_refresh_seconds is {self.cashflow_refresh_interval_sec}s (>5m). "
                f"Deposits/withdrawals may not be reflected for up to {self.cashflow_refresh_interval_sec}s. "
                f"Consider setting cashflow_refresh_seconds = 60 in config.toml [reporting]."
            )
        self.last_daily_reset_ts = int(time.time())

        self.valid_pairs = self._fetch_valid_trade_pairs(self.trade_pairs)
        self.trade_pairs = self.valid_pairs if self.valid_pairs else []
        self._init_pair_state(self.trade_pairs)
        
        # Flash-crash airbag tracking: {pair: [(timestamp, price), ...]}
        self.price_history_airbag = {p: [] for p in self.trade_pairs}
        self.airbag_drop_threshold = float(self.config.get('risk_management', {}).get('airbag_drop_threshold', 15.0))
        self.airbag_window_minutes = int(self.config.get('risk_management', {}).get('airbag_window_minutes', 10))
        
        # Sentiment integration (opt-in)
        self.enable_sentiment_guard = bool(self.config.get('risk_management', {}).get('enable_sentiment_guard', False))
        self.news_marquee_path = "/tmp/youtube_stream/news_marquee.txt"
        self.sentiment_pause_keywords = ["crash", "hack", "dump", "sec", "lawsuit", "regulation", "ban"]
        self.sentiment_active = False

        # Time-of-day filter: only open new positions during high-volume hours (UTC)
        self.enable_trading_hours = bool(self.config.get('risk_management', {}).get('enable_trading_hours', True))
        self.trading_hours_start_utc = int(self.config.get('risk_management', {}).get('trading_hours_start_utc', 14))
        self.trading_hours_end_utc = int(self.config.get('risk_management', {}).get('trading_hours_end_utc', 22))

        # Volume filter: skip entries when volume is unusually low
        self.enable_volume_filter = bool(self.config.get('risk_management', {}).get('enable_volume_filter', True))
        self.volume_filter_min_ratio = float(self.config.get('risk_management', {}).get('volume_filter_min_ratio', 0.5))
        self._volume_cache = {}  # {pair: (timestamp, ratio)}

        # Bear Shield: auto-park in FIAT during confirmed downtrends
        bear_cfg = self.config.get('bear_shield', {})
        self.enable_bear_shield = bool(bear_cfg.get('enable_bear_shield', False))
        self.bear_ema_period = int(bear_cfg.get('bear_ema_period', 50))
        self.bear_confirm_candles = int(bear_cfg.get('bear_confirm_candles', 3))
        self.bear_benchmark_pair = str(bear_cfg.get('bear_benchmark_pair', 'XETHZEUR')).upper()
        self.bear_log_interval_minutes = int(bear_cfg.get('bear_log_interval_minutes', 60))
        self._bear_mode_active = False          # current state
        self._bear_last_log_ts = 0              # throttle logging

        # Trade history cache: avoids hitting Kraken API every loop iteration
        self._trade_history_cache: dict = {}    # {trade_id: trade_dict}
        self._trade_history_last_fetch: float = 0.0  # unix timestamp of last API fetch

    def _notify_pause(self, reason):
        """Log and attempt to notify an external channel when a trading pause activates."""
        try:
            import json, subprocess, datetime, os
            logp = os.path.join(os.path.dirname(__file__), 'logs', 'pause_events.log')
            os.makedirs(os.path.dirname(logp), exist_ok=True)
            entry = {
                'ts': datetime.datetime.utcnow().isoformat(),
                'reason': reason,
                'balance': float(self.get_eur_balance()),
                'consecutive_losses': int(getattr(self,'consecutive_losses',0))
            }
            with open(logp,'a') as f:
                f.write(json.dumps(entry) + "\n")
            # call optional notifier script
            script = os.path.join(os.path.dirname(__file__), 'scripts', 'notify_pause.sh')
            if os.path.exists(script) and os.access(script, os.X_OK):
                try:
                    subprocess.Popen([script, reason], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception as e:
                    self.logger.debug(f"notify_pause: could not run notifier script: {e}")
        except Exception as e:
            self.logger.warning(f"notify_pause: failed to write pause log: {e}")

    # ── Bear Shield ───────────────────────────────────────────────────────────

    def _calc_ema(self, prices, period):
        """Simple EMA calculation (no external dependencies)."""
        if len(prices) < period:
            return None
        k = 2.0 / (period + 1)
        ema = prices[0]
        for p in prices[1:]:
            ema = p * k + ema * (1 - k)
        return ema

    def _is_bear_market(self):
        """Return True when the 4h trend has confirmed a downtrend for bear_confirm_candles.

        Logic: fetch 4h OHLC for bear_benchmark_pair, compute EMA(bear_ema_period).
               If the last bear_confirm_candles closes are ALL below EMA → bear mode.
               If price crosses back above EMA → bull mode restored.
        Fails safe: returns False (allow trading) if API call fails.
        """
        if not self.enable_bear_shield:
            return False
        try:
            ohlc = self.api_client.get_ohlc_data(self.bear_benchmark_pair, interval=240)  # 4h
            if not ohlc:
                return False
            key = [k for k in ohlc.keys() if k != 'last']
            if not key:
                return False
            rows = ohlc[key[0]]
            closes = [float(r[4]) for r in rows if r and len(r) >= 5]
            if len(closes) < self.bear_ema_period + self.bear_confirm_candles:
                return False

            ema = self._calc_ema(closes[:-self.bear_confirm_candles], self.bear_ema_period)
            if ema is None:
                return False

            # Check last N candles are all below EMA
            last_n = closes[-self.bear_confirm_candles:]
            return all(c < ema for c in last_n)
        except Exception as e:
            self.logger.debug(f"Bear shield check failed (safe fallback to False): {e}")
            return False

    def _bear_shield_exit_all(self):
        """Sell all open long positions to park in FIAT (bear market escape)."""
        sold_any = False
        for pair in list(self.trade_pairs):
            qty = self.holdings.get(pair, 0.0)
            min_vol = self._get_min_volume(pair)
            if qty >= min_vol:
                price = self.pair_prices.get(pair, 0.0)
                if price > 0:
                    self.logger.warning(
                        f"BEAR SHIELD: selling {qty:.6f} {pair} @ {price:.4f} EUR to park in FIAT"
                    )
                    self.execute_sell_order(pair, price)
                    sold_any = True
        return sold_any

    def _update_airbag_history(self, pair, price):
        """Append (timestamp, price) to the rolling flash-crash window for *pair*.

        The window is kept to the last ``airbag_window_minutes`` minutes.
        Called every cycle from ``analyze_all_pairs`` before the airbag check.
        """
        now = time.time()
        history = self.price_history_airbag.get(pair, [])
        history.append((now, price))
        # Remove old entries
        cutoff = now - (self.airbag_window_minutes * 60)
        self.price_history_airbag[pair] = [h for h in history if h[0] >= cutoff]
        
    def _check_airbag_trigger(self, pair):
        """Return True if price has dropped ≥ airbag_drop_threshold% within the airbag window.

        When triggered, the caller (``analyze_all_pairs``) immediately issues a
        market sell to exit the position — this is the "flash-crash airbag".
        Requires at least 2 data points; returns False if insufficient history.
        """
        history = self.price_history_airbag.get(pair, [])
        if len(history) < 2:
            return False
        peak_price = max(h[1] for h in history)
        current_price = history[-1][1]
        drop = ((peak_price - current_price) / peak_price) * 100.0
        if drop >= self.airbag_drop_threshold:
            self.logger.critical(f"AIRBAG TRIGGERED for {pair}: drop of {drop:.2f}% in {self.airbag_window_minutes}m")
            return True
        return False

    def _scan_news_sentiment(self):
        try:
            if not os.path.exists(self.news_marquee_path):
                return False
            import re, fcntl
            with open(self.news_marquee_path, 'r') as f:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                    content = f.read().lower()
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except (OSError, BlockingIOError):
                    # File is being written to; skip this cycle safely
                    return self.sentiment_active  # Keep previous state
            # Use word boundaries to avoid false positives (like 'sec' in 'secretary')
            found = [k for k in self.sentiment_pause_keywords if re.search(r'\b' + re.escape(k) + r'\b', content)]
            if found:
                if not self.sentiment_active:
                    self.logger.warning(f"SENTIMENT GUARD: Keywords found in news ({', '.join(found)}). Pausing Buys.")
                return True
            return False
        except Exception:
            return False

    def _init_pair_state(self, pairs):
        """Initialise all per-pair state dicts for newly added pairs.

        Called once at startup for all configured pairs and again whenever
        ``reload_config`` detects that new pairs have been added to the config.
        Safe to call multiple times — ``setdefault`` prevents overwriting
        existing state for pairs that are already active.
        """
        for pair in pairs:
            self.pair_signals.setdefault(pair, "HOLD")
            self.holdings.setdefault(pair, 0.0)
            self.purchase_prices.setdefault(pair, 0.0)
            self.peak_prices.setdefault(pair, 0.0)
            self.position_qty.setdefault(pair, 0.0)
            self.short_qty.setdefault(pair, 0.0)
            self.short_entry_prices.setdefault(pair, 0.0)
            self.realized_pnl.setdefault(pair, 0.0)
            self.fees_paid.setdefault(pair, 0.0)
            self.trade_metrics.setdefault(pair, {"closed": 0, "wins": 0, "losses": 0, "sum_pnl": 0.0})
            self.last_trade_at.setdefault(pair, 0)
            self.entry_timestamps.setdefault(pair, None)

    def _get_target_balance(self):
        try:
            return self.config['bot_settings']['trade_amounts'].get('target_balance_eur', 1000.0)
        except Exception:
            return self.config['bot_settings'].get('target_balance_eur', 1000.0)

    def _get_take_profit_percent(self):
        try:
            return float(self.config['risk_management'].get('take_profit_percent', 5.0))
        except Exception:
            return 5.0

    def _get_stop_loss_percent(self):
        try:
            return float(self.config['risk_management'].get('stop_loss_percent', 2.0))
        except Exception:
            return 2.0

    def _get_trade_amount_eur(self):
        try:
            return float(self.config['bot_settings']['trade_amounts'].get('trade_amount_eur', 30.0))
        except Exception:
            return 30.0

    def _get_dynamic_trade_amount_eur(self, pair, available_eur):
        """Dynamic sizing: adjusted by ATR volatility and available EUR."""
        base_amount = self._get_trade_amount_eur()
        
        # 1. Start with percentage-based sizing
        allocation_pct = float(self.config.get('risk_management', {}).get('allocation_per_trade_percent', 10.0))
        amount = available_eur * (allocation_pct / 100.0)
        
        # 2. ATR adjustment (Vol Targeting)
        # We target a specific % movement per trade.
        atr = self.analysis_tool.calculate_atr(pair)
        current_price = self.pair_prices.get(pair, 0)
        
        if atr and current_price > 0:
            # How many units to buy so that 1 ATR movement = X% of trade
            # Normalizing factor: higher volatility -> lower amount
            volatility_ratio = (atr / current_price) * 100.0
            # Reference: 1.5% ATR is "normal". If vol is 3%, we halve the size.
            target_vol = 1.5 
            vol_multiplier = target_vol / max(0.5, volatility_ratio)
            amount *= vol_multiplier

        # 3. Apply risk-off multiplier from regime
        amount *= self._allocation_multiplier()
        
        # Cap at configured max base amount and available funds
        return min(base_amount * 1.5, amount, available_eur * 0.95)

    def _is_mtf_trend_bullish(self, pair):
        """Check 1h timeframe to confirm bullish trend."""
        try:
            ohlc = self.api_client.get_ohlc_data(pair, interval=60) # 1h
            if not ohlc:
                return True # Fallback to allow trade if API fails
            
            data_key = list(ohlc.keys())[0]
            # Kraken returns [time, open, high, low, close, vwap, volume, count]
            closes = [float(row[4]) for row in ohlc[data_key]]
            
            return self.analysis_tool.check_mtf_trend(closes)
        except Exception as e:
            self.logger.error(f"MTF check failed for {pair}: {e}")
            return True

    def _get_min_volume(self, pair):
        try:
            min_volumes = self.config['bot_settings'].get('min_volumes', {})
            if pair in min_volumes:
                return float(min_volumes.get(pair, 0.0001))

            # alias fallback (altname <-> wsname style)
            aliases = {
                'XBTEUR': 'XXBTZEUR',
                'ETHEUR': 'XETHZEUR',
                'XRPEUR': 'XXRPZEUR',
                'XXBTZEUR': 'XBTEUR',
                'XETHZEUR': 'ETHEUR',
                'XXRPZEUR': 'XRPEUR',
            }
            alt = aliases.get(pair)
            if alt and alt in min_volumes:
                return float(min_volumes.get(alt, 0.0001))

            return 0.0001
        except Exception:
            return 0.0001

    def _calculate_volume(self, pair, price, available_eur=None):
        trade_amount_eur = self._get_trade_amount_eur()
        if available_eur is not None:
            trade_amount_eur = min(trade_amount_eur, max(0.0, available_eur))
        min_volume = self._get_min_volume(pair)
        if price <= 0:
            return 0.0
        calculated_volume = trade_amount_eur / price
        return max(calculated_volume, min_volume)

    def _fetch_valid_trade_pairs(self, requested_pairs):
        assets = self.api_client.get_asset_pairs()
        if not assets:
            self.logger.warning("Could not fetch AssetPairs; using configured pairs unchanged")
            return requested_pairs

        valid_requested = []
        seen = set()

        # Build flexible normalization index (ALTNAME, WSNAME, and slashless variants)
        pair_index = {}
        for key, meta in assets.items():
            alt = (meta.get('altname') or key or '').upper()
            ws = (meta.get('wsname') or '').upper()
            ws_noslash = ws.replace('/', '')
            key_u = (key or '').upper()
            for alias in [alt, ws, ws_noslash, key_u, alt.replace('/', '')]:
                if alias:
                    pair_index[alias] = alt

        for raw_pair in requested_pairs:
            pair = (raw_pair or '').upper()
            normalized = pair_index.get(pair) or pair_index.get(pair.replace('/', ''))
            if normalized:
                if normalized not in seen:
                    valid_requested.append(normalized)
                    seen.add(normalized)
                if pair != normalized:
                    normalization_key = f"{pair}->{normalized}"
                    if normalization_key not in self._normalized_pair_logs_seen:
                        self.logger.info(f"Pair normalized: {pair} -> {normalized}")
                        self._normalized_pair_logs_seen.add(normalization_key)
            else:
                self.logger.warning(f"Skipping unknown Kraken pair: {raw_pair}")
        self.kelly_fraction = self._calculate_kelly_fraction()

        if not valid_requested:
            self.logger.error("No valid trading pairs after Kraken validation")
        else:
            self.logger.info(f"Validated trading pairs: {valid_requested}")
        return valid_requested

    def reload_config(self):
        """Hot-reload config.toml and apply all changed settings without restarting.

        Called automatically every ``config_reload_interval`` seconds from the
        main loop.  Detects newly added trade pairs and initialises their state.
        Existing holdings and entry prices are preserved across reloads.
        Returns True on success, False if the config file cannot be parsed.
        """
        try:
            new_config = load_config(self.config_path)
            if not new_config:
                return False

            old_pairs = set(self.trade_pairs)
            self.config = new_config
            requested = self.config['bot_settings'].get('trade_pairs', ['XBTEUR'])
            self.trade_pairs = self._fetch_valid_trade_pairs(requested)
            new_pairs = set(self.trade_pairs)
            # Only initialise state for truly NEW pairs; preserve holdings/entry-prices for existing ones
            added_pairs = list(new_pairs - old_pairs)
            if added_pairs:
                self._init_pair_state(added_pairs)
            # Immediately reconcile live state so no stale holdings data lingers
            self._sync_account_state()

            self.target_balance_eur = self._get_target_balance()
            self.take_profit_percent = self._get_take_profit_percent()
            self.stop_loss_percent = self._get_stop_loss_percent()
            self.max_open_positions = int(self.config.get('risk_management', {}).get('max_open_positions', self.max_open_positions))
            self.trade_cooldown_sec = int(self.config.get('risk_management', {}).get('trade_cooldown_seconds', self.trade_cooldown_sec))
            self.global_trade_cooldown_sec = int(self.config.get('risk_management', {}).get('global_trade_cooldown_seconds', self.global_trade_cooldown_sec))
            self.trailing_stop_percent = float(self.config.get('risk_management', {}).get('trailing_stop_percent', self.trailing_stop_percent))
            self.empty_sell_log_cooldown_sec = int(self.config.get('risk_management', {}).get('empty_sell_log_cooldown_seconds', self.empty_sell_log_cooldown_sec))
            self.enable_regime_filter = bool(self.config.get('risk_management', {}).get('enable_regime_filter', self.enable_regime_filter))
            self.regime_benchmark_pair = str(self.config.get('risk_management', {}).get('regime_benchmark_pair', self.regime_benchmark_pair)).upper()
            self.regime_min_score = float(self.config.get('risk_management', {}).get('regime_min_score', self.regime_min_score))
            self.enable_hard_stop_loss = bool(self.config.get('risk_management', {}).get('enable_hard_stop_loss', self.enable_hard_stop_loss))
            self.hard_stop_loss_percent = float(self.config.get('risk_management', {}).get('hard_stop_loss_percent', self.hard_stop_loss_percent))
            self.enable_mtf_regime_scoring = bool(self.config.get('risk_management', {}).get('enable_mtf_regime_scoring', self.enable_mtf_regime_scoring))
            self.mtf_regime_min_score = float(self.config.get('risk_management', {}).get('mtf_regime_min_score', self.mtf_regime_min_score))
            self.enable_time_stop = bool(self.config.get('risk_management', {}).get('enable_time_stop', self.enable_time_stop))
            self.time_stop_hours = int(self.config.get('risk_management', {}).get('time_stop_hours', self.time_stop_hours))
            self.enable_daily_drawdown = bool(self.config.get('risk_management', {}).get('enable_daily_drawdown', self.enable_daily_drawdown))
            self.daily_drawdown_percent = float(self.config.get('risk_management', {}).get('daily_loss_limit_percent', self.daily_drawdown_percent))
            self.risk_off_allocation_multiplier = float(self.config.get('risk_management', {}).get('risk_off_allocation_multiplier', self.risk_off_allocation_multiplier))
            self.enable_volatility_targeting = bool(self.config.get('risk_management', {}).get('enable_volatility_targeting', self.enable_volatility_targeting))
            self.target_volatility_pct = float(self.config.get('risk_management', {}).get('target_volatility_pct', self.target_volatility_pct))
            self.max_consecutive_losses = int(self.config.get('risk_management', {}).get('max_consecutive_losses', self.max_consecutive_losses))
            self.pause_after_loss_streak_minutes = int(self.config.get('risk_management', {}).get('pause_after_loss_streak_minutes', self.pause_after_loss_streak_minutes))
            self.sell_fee_buffer_percent = float(self.config.get('risk_management', {}).get('sell_fee_buffer_percent', self.sell_fee_buffer_percent))
            self.enable_sentiment_guard = bool(self.config.get('risk_management', {}).get('enable_sentiment_guard', self.enable_sentiment_guard))
            # Signal engine mode reload
            self.enable_mr_signals = bool(self.config.get('risk_management', {}).get('enable_mean_reversion_signals', self.enable_mr_signals))
            self.enable_trend_signals = bool(self.config.get('risk_management', {}).get('enable_trend_breakout_signals', self.enable_trend_signals))
            self.mr_rsi_oversold = float(self.config.get('risk_management', {}).get('mr_rsi_oversold_threshold', self.mr_rsi_oversold))
            self.mr_rsi_overbought = float(self.config.get('risk_management', {}).get('mr_rsi_overbought_threshold', self.mr_rsi_overbought))
            self.analysis_tool.enable_mr_signals = self.enable_mr_signals
            self.analysis_tool.enable_trend_signals = self.enable_trend_signals
            self.analysis_tool.mr_rsi_buy = self.mr_rsi_oversold
            self.analysis_tool.mr_rsi_sell = self.mr_rsi_overbought
            # ATR + pyramiding reload
            self.enable_atr_stop = bool(self.config.get('risk_management', {}).get('enable_atr_stop', self.enable_atr_stop))
            self.atr_period = int(self.config.get('risk_management', {}).get('atr_period', self.atr_period))
            self.atr_multiplier = float(self.config.get('risk_management', {}).get('atr_multiplier', self.atr_multiplier))
            self.atr_trail_multiplier = float(self.config.get('risk_management', {}).get('atr_trail_multiplier', self.atr_trail_multiplier))
            self.enable_atr_dynamic_tp = bool(self.config.get('risk_management', {}).get('enable_atr_dynamic_tp', self.enable_atr_dynamic_tp))
            self.atr_tp_multiplier = float(self.config.get('risk_management', {}).get('atr_tp_multiplier', self.atr_tp_multiplier))
            self.enable_break_even = bool(self.config.get('risk_management', {}).get('enable_break_even', self.enable_break_even))
            self.break_even_trigger_pct = float(self.config.get('risk_management', {}).get('break_even_trigger_percent', self.break_even_trigger_pct))
            self.enable_pyramiding = bool(self.config.get('risk_management', {}).get('enable_pyramiding', self.enable_pyramiding))
            self.pyramiding_add_pct = float(self.config.get('risk_management', {}).get('pyramiding_add_pct', self.pyramiding_add_pct))

            if old_pairs != new_pairs:
                self.logger.info(f"CONFIG RELOAD: trade_pairs changed {sorted(old_pairs)} -> {sorted(new_pairs)}")

            # Bear Shield reload
            bear_cfg = self.config.get('bear_shield', {})
            self.enable_bear_shield = bool(bear_cfg.get('enable_bear_shield', self.enable_bear_shield))
            self.bear_ema_period = int(bear_cfg.get('bear_ema_period', self.bear_ema_period))
            self.bear_confirm_candles = int(bear_cfg.get('bear_confirm_candles', self.bear_confirm_candles))
            self.bear_benchmark_pair = str(bear_cfg.get('bear_benchmark_pair', self.bear_benchmark_pair)).upper()
            self.bear_log_interval_minutes = int(bear_cfg.get('bear_log_interval_minutes', self.bear_log_interval_minutes))

            self.last_config_reload = datetime.now()
            self.loop_interval_sec = int(self.config.get('bot_settings', {}).get('loop_interval_seconds', self.loop_interval_sec))
            return True
        except Exception as e:
            self.logger.error(f"Error reloading config: {e}")
            return False

    def get_eur_balance(self):
        """Return current EUR (ZEUR) balance from Kraken; returns 0.0 on error."""
        try:
            balance = self.api_client.get_account_balance()
            if balance:
                return float(balance.get('ZEUR', 0))
            return 0.0
        except Exception as e:
            self.logger.error(f"Error getting EUR balance: {e}")
            return 0.0

    def get_crypto_holdings(self):
        """Refresh ``self.holdings`` dict from Kraken account balance.

        Maps Kraken asset codes (e.g. 'XXBT') back to our pair keys
        (e.g. 'XBTEUR').  Only updates pairs listed in ``self.trade_pairs``.
        """
        try:
            balance = self.api_client.get_account_balance()
            if not balance:
                return

            pair_to_balance = {
                'XBTEUR': 'XXBT', 'XXBTZEUR': 'XXBT',
                'ETHEUR': 'XETH', 'XETHZEUR': 'XETH',
                'SOLEUR': 'SOL',
                'ADAEUR': 'ADA',
                'DOTEUR': 'DOT',
                'XRPEUR': 'XXRP', 'XXRPZEUR': 'XXRP',
                'LINKEUR': 'LINK',
                'MATICEUR': 'MATIC',
                'POLEUR': 'POL'
            }
            for pair in self.trade_pairs:
                key = pair_to_balance.get(pair)
                if not key:
                    continue
                self.holdings[pair] = float(balance.get(key, 0))
        except Exception as e:
            self.logger.error(f"Error getting holdings: {e}")

    def _reconcile_open_orders(self):
        """Compare open orders on Kraken with local position state at startup.

        Detects 'orphaned' orders that exist on Kraken but are not reflected
        locally (e.g. bot died between placing an order and updating state).
        Logs a warning so the operator can decide to cancel manually if needed.
        """
        try:
            open_orders_result = self.api_client.get_open_orders()
            if not open_orders_result:
                return
            open_map = open_orders_result.get('open', open_orders_result) if isinstance(open_orders_result, dict) else {}
            if not open_map:
                return

            watched = set(self.trade_pairs)
            # Build alias map so we can match Kraken pair names to our normalised pairs
            pair_aliases = {
                'XXBTZEUR': 'XBTEUR', 'XBTEUR': 'XBTEUR',
                'XETHZEUR': 'ETHEUR', 'ETHEUR': 'ETHEUR',
                'SOLEUR': 'SOLEUR', 'ADAEUR': 'ADAEUR',
                'DOTEUR': 'DOTEUR',
                'XXRPZEUR': 'XRPEUR', 'XRPEUR': 'XRPEUR',
                'LINKEUR': 'LINKEUR',
            }

            for txid, order in open_map.items():
                raw_pair = str(order.get('descr', {}).get('pair', '') or order.get('pair', '')).upper()
                norm_pair = pair_aliases.get(raw_pair, raw_pair)
                if norm_pair not in watched:
                    continue
                side = str(order.get('descr', {}).get('type', '') or '').lower()
                vol = float(order.get('vol', 0) or 0)
                local_holding = self.holdings.get(norm_pair, 0.0)
                local_short = self.short_qty.get(norm_pair, 0.0)

                # Check for mismatches
                if side == 'buy' and local_holding < self._get_min_volume(norm_pair):
                    self.logger.warning(
                        f"RECONCILE: Open BUY order {txid} ({vol:.6f} {norm_pair}) exists on Kraken "
                        f"but local holdings={local_holding:.8f}. Bot may have crashed before state update."
                    )
                elif side == 'sell' and local_short <= 0 and local_holding < self._get_min_volume(norm_pair):
                    self.logger.warning(
                        f"RECONCILE: Open SELL order {txid} ({vol:.6f} {norm_pair}) exists on Kraken "
                        f"but no local long/short position found."
                    )

            self.logger.info(f"Order reconciliation complete. {len(open_map)} open order(s) checked.")
        except Exception as e:
            self.logger.error(f"Order reconciliation failed: {e}", exc_info=True)

    def _sync_account_state(self, force_history: bool = False):
        """Refresh local holdings and purchase-price state from the Kraken API.

        Called after every trade and at startup.  When ``force_history=True``
        (post-trade or on first boot) it bypasses the 10-minute cache and
        re-fetches the full trade history from Kraken / NAS to recompute the
        average entry price.
        """
        self.get_crypto_holdings()
        self.load_purchase_prices_from_history(force=force_history)

    def _load_trade_history_from_nas(self, year: int) -> dict:
        """Load persisted trade history from NAS JSON file. Returns {} if unavailable."""
        path = self.nas_root / str(year) / 'trade_history' / f'trades_{year}.json'
        try:
            if path.exists():
                with open(path, 'r') as f:
                    data = json.load(f)
                self.logger.info(f"Loaded {len(data)} trades from NAS cache ({path.name})")
                return data
        except Exception as e:
            self.logger.warning(f"Could not load NAS trade history ({path}): {e}")
        return {}

    def _save_trade_history_to_nas(self, trades: dict, year: int) -> None:
        """Persist trade history to NAS JSON file for future incremental loads."""
        try:
            trade_history_dir = self.nas_root / str(year) / 'trade_history'
            trade_history_dir.mkdir(parents=True, exist_ok=True)
            path = trade_history_dir / f'trades_{year}.json'
            with open(path, 'w') as f:
                json.dump(trades, f, separators=(',', ':'))
            self.logger.debug(f"Saved {len(trades)} trades to NAS cache ({path.name})")
        except Exception as e:
            self.logger.warning(f"Could not save trade history to NAS ({e}) — NAS mounted?")

    def _refresh_trade_history_cache(self, force: bool = False) -> None:
        """Fetch trade history from Kraken API and merge into in-memory + NAS cache.

        Uses TTL: only fetches if cache is older than _TRADE_HISTORY_REFRESH_INTERVAL seconds.
        Always fetches after a trade (force=True).
        Incremental: only requests trades newer than the last cached entry.
        """
        now = time.time()
        if not force and (now - self._trade_history_last_fetch) < _TRADE_HISTORY_REFRESH_INTERVAL:
            return

        year = datetime.now(tz=timezone.utc).year
        year_start_ts = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())

        # Bootstrap from NAS on first run (cache is empty)
        if not self._trade_history_cache:
            self._trade_history_cache = self._load_trade_history_from_nas(year)

        # Only fetch trades newer than the latest entry we already have
        if self._trade_history_cache:
            last_ts = max(float(t.get('time', 0)) for t in self._trade_history_cache.values())
            fetch_start = max(year_start_ts, int(last_ts))
        else:
            fetch_start = year_start_ts

        new_trades = self.api_client.get_trade_history(start=fetch_start, fetch_all=True)
        if new_trades:
            self._trade_history_cache.update(new_trades)
            self._save_trade_history_to_nas(self._trade_history_cache, year)

        self._trade_history_last_fetch = now
        self.logger.debug(
            f"Trade history cache refreshed: {len(self._trade_history_cache)} total trades "
            f"(+{len(new_trades) if new_trades else 0} new, start={fetch_start})"
        )

    def load_purchase_prices_from_history(self, force: bool = False):
        """Rebuild per-pair average entry price + realized PnL from Kraken trade history.

        Logic:
        - BUY increases position size and weighted average entry (including fees)
        - SELL reduces position and realizes PnL (net of fees)

        Uses an in-memory + NAS cache to avoid hitting the Kraken API on every loop iteration.
        Pass force=True immediately after a trade to ensure fresh data.
        """
        try:
            self._refresh_trade_history_cache(force=force)
            trades = self._trade_history_cache
            if not trades:
                return

            watched = set(self.trade_pairs)
            pair_aliases = {
                'XXBTZEUR': 'XBTEUR', 'XBTEUR': 'XBTEUR',
                'XETHZEUR': 'ETHEUR', 'ETHEUR': 'ETHEUR',
                'SOLEUR': 'SOLEUR',
                'ADAEUR': 'ADAEUR',
                'DOTEUR': 'DOTEUR',
                'XXRPZEUR': 'XRPEUR', 'XRPEUR': 'XRPEUR',
                'LINKEUR': 'LINKEUR',
                'MATICEUR': 'MATICEUR',
                'POLEUR': 'POLEUR'
            }

            # Reset state before replay
            for pair in watched:
                self.position_qty[pair] = 0.0
                self.purchase_prices[pair] = 0.0
                self.realized_pnl[pair] = 0.0
                self.fees_paid[pair] = 0.0

            sorted_trades = sorted(trades.values(), key=lambda t: float(t.get('time', 0)))
            history_trade_count = 0

            for trade in sorted_trades:
                raw_pair = trade.get('pair', '')
                pair = pair_aliases.get(raw_pair, raw_pair)
                if pair not in watched:
                    continue

                ttype = trade.get('type', '').lower()
                vol = float(trade.get('vol', 0) or 0)
                cost = float(trade.get('cost', 0) or 0)  # quote currency (EUR)
                fee = float(trade.get('fee', 0) or 0)
                if vol <= 0:
                    continue

                self.fees_paid[pair] += fee
                qty = self.position_qty.get(pair, 0.0)
                avg = self.purchase_prices.get(pair, 0.0)

                if ttype == 'buy':
                    history_trade_count += 1
                    total_cost = cost + fee
                    new_qty = qty + vol
                    if new_qty > 0:
                        new_avg = ((avg * qty) + total_cost) / new_qty
                    else:
                        new_avg = 0.0
                    self.position_qty[pair] = new_qty
                    self.purchase_prices[pair] = new_avg
                    self.peak_prices[pair] = max(self.peak_prices.get(pair, 0.0), new_avg)

                elif ttype == 'sell':
                    history_trade_count += 1
                    sell_qty = min(qty, vol)
                    proceeds_net = cost - fee
                    if sell_qty > 0 and avg > 0:
                        cost_basis = avg * sell_qty
                        self.realized_pnl[pair] += (proceeds_net - cost_basis)
                    remaining_qty = max(0.0, qty - sell_qty)
                    self.position_qty[pair] = remaining_qty
                    if remaining_qty <= self._get_min_volume(pair):
                        self.purchase_prices[pair] = 0.0
                        self.peak_prices[pair] = 0.0

            # Keep displayed trade counter consistent across restarts (history + new trades)
            if history_trade_count > 0:
                self.trade_count = history_trade_count

            # Reconcile with live holdings from balance (source of truth for quantity)
            for pair in watched:
                live_qty = self.holdings.get(pair, 0.0)
                self.position_qty[pair] = live_qty
                min_vol = self._get_min_volume(pair)
                # Use a small grace margin (5%) so a position at exactly min_volume
                # is NOT treated as empty and does not lose its entry price.
                if live_qty < min_vol * 0.95:
                    self.purchase_prices[pair] = 0.0
                    self.peak_prices[pair] = 0.0
                    self.entry_timestamps[pair] = None
                elif self.purchase_prices.get(pair, 0.0) <= 0.0:
                    # Position exists but entry price is unknown (e.g. after a crash-restart)
                    self.logger.warning(
                        f"Position {pair} exists ({live_qty:.8f}) but entry price is unknown! "
                        f"TP/SL calculations may be inaccurate until next history replay."
                    )
                    if self.entry_timestamps.get(pair) is None:
                        self.entry_timestamps[pair] = int(time.time())
                else:
                    if self.entry_timestamps.get(pair) is None:
                        self.entry_timestamps[pair] = int(time.time())

        except Exception as e:
            self.logger.error(f"Error loading last purchase prices: {e}")

    def _resolve_benchmark_history(self):
        bench = self.regime_benchmark_pair
        aliases = [bench, bench.replace('/', '')]
        if bench == 'XBTEUR':
            aliases += ['XXBTZEUR']
        if bench == 'ETHEUR':
            aliases += ['XETHZEUR']
        for key in aliases:
            history = self.analysis_tool.pair_price_history.get(key)
            if history:
                return list(history)
        return []

    def _compute_mtf_regime_score(self):
        prices = self._resolve_benchmark_history()
        if len(prices) < 80:
            return None

        def _safe_rsi(window):
            val = self.analysis_tool.calculate_rsi(window)
            return 50.0 if val is None else float(val)

        rsi_fast = _safe_rsi(prices[-25:])
        rsi_mid = _safe_rsi(prices[-35:])
        rsi_slow = _safe_rsi(prices[-80:])

        sma10 = sum(prices[-10:]) / 10.0
        sma30 = sum(prices[-30:]) / 30.0
        sma70 = sum(prices[-70:]) / 70.0

        trend = (((sma10 - sma30) / sma30) * 100.0) * 0.9 + (((sma30 - sma70) / sma70) * 100.0) * 1.2
        momentum = ((rsi_fast - 50.0) * 0.4) + ((rsi_mid - 50.0) * 0.35) + ((rsi_slow - 50.0) * 0.25)

        recent = prices[-24:]
        mean = sum(recent) / len(recent)
        vol_pct = 0.0
        if mean > 0:
            variance = sum((p - mean) ** 2 for p in recent) / len(recent)
            vol_pct = ((variance ** 0.5) / mean) * 100.0
        vol_penalty = max(0.0, vol_pct - 2.2) * 1.5

        return trend + momentum - vol_penalty

    def _is_risk_on_regime(self):
        """Return True when the market regime is considered bullish (RISK_ON).

        When ``enable_mtf_regime_scoring`` is on, uses the composite MTF score
        (short/long SMA + RSI across multiple timeframes) for the BTC benchmark.
        Falls back to the simpler pair-score comparison against ``regime_min_score``.
        Always returns True when the regime filter is disabled in config.
        """
        if not self.enable_regime_filter:
            return True

        if self.enable_mtf_regime_scoring:
            mtf_score = self._compute_mtf_regime_score()
            if mtf_score is not None:
                return mtf_score >= self.mtf_regime_min_score

        benchmark = self.regime_benchmark_pair
        score = float(self.pair_scores.get(benchmark, 0.0))
        return score >= self.regime_min_score

    def _benchmark_volatility_pct(self):
        bench = self.regime_benchmark_pair
        aliases = [bench, bench.replace('/', '')]
        # analysis stores histories by raw Kraken key seen in ticker payload
        if bench == 'XBTEUR':
            aliases += ['XXBTZEUR']
        if bench == 'ETHEUR':
            aliases += ['XETHZEUR']

        try:
            history = None
            for key in aliases:
                history = self.analysis_tool.pair_price_history.get(key)
                if history and len(history) >= 20:
                    break
            if not history or len(history) < 20:
                return 0.0
            prices = list(history)[-20:]
            mean = sum(prices) / len(prices)
            if mean <= 0:
                return 0.0
            variance = sum((p - mean) ** 2 for p in prices) / len(prices)
            return ((variance ** 0.5) / mean) * 100.0
        except Exception:
            return 0.0

    def _allocation_multiplier(self):
        """Return a [0.2, 1.25] multiplier applied to every order size.

        Combines two scaling factors:
        - Regime factor: 1.0 in RISK_ON, ``risk_off_allocation_multiplier``
          (default 0.5) in RISK_OFF — cuts size in half during bear markets.
        - Volatility factor: ``target_volatility_pct / current_vol``; reduces
          size when BTC volatility is elevated above the target (default 1.6%).

        The product is clamped to [0.2, 1.25] so orders never vanish entirely
        or exceed 125 % of base size.
        """
        base = 1.0 if self._is_risk_on_regime() else self.risk_off_allocation_multiplier
        if not self.enable_volatility_targeting:
            return base
        vol = self._benchmark_volatility_pct()
        if vol <= 0:
            return base
        # Higher volatility -> smaller size, lower volatility -> allow base size
        vol_scale = min(1.25, max(0.35, self.target_volatility_pct / vol))
        return max(0.2, min(1.25, base * vol_scale))

    def _is_trading_hours(self):
        """Returns True if current UTC hour is within the configured trading window."""
        if not self.enable_trading_hours:
            return True
        hour = datetime.now(timezone.utc).hour
        start = self.trading_hours_start_utc
        end = self.trading_hours_end_utc
        if start < end:
            return start <= hour < end
        # Overnight window support (e.g. 22:00–06:00)
        return hour >= start or hour < end

    def _has_sufficient_volume(self, pair):
        """Returns True if the latest 15m candle volume is >= min_ratio × 20-candle average.
        Uses a 5-minute cache to avoid redundant API calls.
        """
        if not self.enable_volume_filter:
            return True
        try:
            cached = self._volume_cache.get(pair)
            if cached and (time.time() - cached[0]) < 300:
                return cached[1] >= self.volume_filter_min_ratio

            ohlc = self.api_client.get_ohlc_data(pair, interval=15)
            if not ohlc:
                return True
            data_key = next((k for k in ohlc if k != 'last'), None)
            if not data_key:
                return True
            rows = ohlc[data_key]
            if len(rows) < 3:
                return True
            volumes = [float(row[6]) for row in rows]
            window = volumes[-20:] if len(volumes) >= 20 else volumes
            avg_vol = sum(window) / len(window)
            current_vol = volumes[-1]
            ratio = current_vol / avg_vol if avg_vol > 0 else 1.0
            self._volume_cache[pair] = (time.time(), ratio)
            if ratio < self.volume_filter_min_ratio:
                self.logger.info(
                    f"BUY skipped for {pair}: low volume (ratio {ratio:.2f} < {self.volume_filter_min_ratio})"
                )
                return False
            return True
        except Exception as e:
            self.logger.warning(f"Volume check failed for {pair}: {e}")
            return True  # fail open — don't block trades on API errors

    def _is_temporarily_paused(self):
        """Return True while the bot is in a loss-streak or drawdown cooldown period."""
        return time.time() < self.trading_paused_until_ts

    def _available_eur_for_buy(self):
        """Return spendable EUR after reserving 1.5 % for fees and slippage."""
        # SMART FEE RESERVE: leave 1.5% for fees and slippage to avoid 'Insufficient funds'
        return max(0.0, self.get_eur_balance() * 0.985)

    def _daily_drawdown_hit(self):
        # If disabled via config, never trigger the daily drawdown circuit
        if not getattr(self, 'enable_daily_drawdown', True):
            return False

        current = self.get_eur_balance()
        if self.daily_start_balance is None:
            self.daily_start_balance = current
            return False
        if self.daily_start_balance <= 0:
            return False

        # Compute percentage drawdown relative to daily start
        dd = ((self.daily_start_balance - current) / self.daily_start_balance) * 100

        # Allow bypass for small absolute losses: require either percentage exceed or absolute EUR loss > threshold
        abs_loss = max(0.0, self.daily_start_balance - current)
        min_abs_loss = float(self.config.get('risk_management', {}).get('daily_loss_min_eur', 0.0))

        if dd >= self.daily_drawdown_percent and abs_loss >= min_abs_loss:
            self.logger.warning(f"Daily drawdown limit reached: {dd:.2f}% >= {self.daily_drawdown_percent:.2f}% (abs loss {abs_loss:.2f} EUR)")
            return True
        return False

    def _refresh_cashflows_from_ledger(self, force=False):
        now_ts = int(time.time())
        if not force and (now_ts - self._last_cashflow_refresh_ts) < self.cashflow_refresh_interval_sec:
            return

        try:
            ledgers = self.api_client.get_ledgers(asset='ZEUR', start=self.start_timestamp, fetch_all=True)
            if not ledgers:
                self._last_cashflow_refresh_ts = now_ts
                return

            deposits = 0.0
            withdrawals = 0.0
            for entry in ledgers.values():
                ltype = str(entry.get('type', '')).lower()
                try:
                    amount = abs(float(entry.get('amount', 0) or 0))
                except Exception:
                    amount = 0.0

                if amount <= 0:
                    continue

                if ltype == 'deposit':
                    deposits += amount
                elif ltype == 'withdrawal':
                    withdrawals += amount

            self.net_deposits_eur = deposits
            self.net_withdrawals_eur = withdrawals
            self._last_cashflow_refresh_ts = now_ts
        except Exception as e:
            self.logger.error(f"Error refreshing cashflows from ledger: {e}")

    def _adjusted_reference_balance(self):
        base = self.initial_balance_eur if self.initial_balance_eur is not None else (self.daily_start_balance or 0.0)
        return base + self.net_deposits_eur - self.net_withdrawals_eur

    def _adjusted_pnl_eur(self, current_balance):
        return current_balance - self._adjusted_reference_balance()

    # ------------------------------------------------------------------
    # Persistent cumulative P&L — survives restarts
    # ------------------------------------------------------------------

    def _pnl_state_path(self) -> Path:
        """Return path to the persistent P&L state file."""
        return Path(__file__).parent / "data" / "pnl_state.json"

    def _cooldown_state_path(self) -> Path:
        return Path(__file__).parent / "data" / "cooldown_state.json"

    def _save_cooldown_state(self) -> None:
        """Persist last_trade_at and last_global_trade_at so cooldowns survive restarts."""
        try:
            state = {
                "last_global_trade_at": self.last_global_trade_at,
                "last_trade_at": self.last_trade_at,
            }
            path = self._cooldown_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state))
        except Exception as exc:
            self.logger.warning(f"Could not save cooldown state: {exc}")

    def _load_cooldown_state(self) -> None:
        """Restore cooldown timestamps from the last run so we don't retrade immediately."""
        path = self._cooldown_state_path()
        try:
            if not path.exists():
                return
            state = json.loads(path.read_text())
            self.last_global_trade_at = float(state.get("last_global_trade_at", 0))
            saved_pair_times = state.get("last_trade_at", {})
            for pair, ts in saved_pair_times.items():
                self.last_trade_at[pair] = float(ts)
            self.logger.info(
                f"Restored cooldown state: global_last={self.last_global_trade_at:.0f}, "
                f"pairs={list(saved_pair_times.keys())}"
            )
        except Exception as exc:
            self.logger.warning(f"Could not load cooldown state: {exc}")

    def _load_cumulative_pnl_state(self, current_balance: float) -> None:
        """Load or initialise the persistent P&L baseline.

        On the very first run (no state file) the current balance is stored
        as the all-time start.  On subsequent runs the stored ``start_eur``
        value is restored so cumulative P&L is always relative to the very
        first time the bot ran.
        """
        path = self._pnl_state_path()
        try:
            if path.exists():
                state = json.loads(path.read_text())
                self.cumulative_start_eur: float = float(state.get("start_eur", current_balance))
                self.logger.info(
                    f"Loaded P&L baseline: {self.cumulative_start_eur:.2f} EUR "
                    f"(started {state.get('created_at', 'unknown')})"
                )
            else:
                self.cumulative_start_eur = current_balance
                state = {
                    "start_eur": current_balance,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(state, indent=2))
                self.logger.info(f"Created P&L baseline: {current_balance:.2f} EUR")
        except Exception as exc:
            self.logger.warning(f"Could not load P&L state: {exc}")
            self.cumulative_start_eur = current_balance

    def cumulative_pnl_eur(self, current_balance: float) -> float:
        """Return total P&L since the bot was first ever started."""
        return current_balance - getattr(self, "cumulative_start_eur", current_balance)

    def _count_open_positions(self) -> int:
        """Return the number of pairs where holdings exceed the minimum tradeable volume."""
        return sum(
            1 for pair in self.trade_pairs
            if self.holdings.get(pair, 0.0) >= self._get_min_volume(pair)
        )

    def _is_on_cooldown(self, pair):
        """Return True if the per-pair cooldown period has not yet elapsed since the last trade."""
        return (time.time() - self.last_trade_at.get(pair, 0)) < self.trade_cooldown_sec

    def _is_global_cooldown(self):
        """Return True if the global inter-trade cooldown has not yet elapsed."""
        return (time.time() - self.last_global_trade_at) < self.global_trade_cooldown_sec

    def _log_empty_sell_signal_throttled(self, pair):
        now_ts = time.time()
        last_ts = self._last_empty_sell_log_at.get(pair, 0)
        if (now_ts - last_ts) >= self.empty_sell_log_cooldown_sec:
            self.logger.info(f"SELL signal for {pair} but no holdings")
            self._last_empty_sell_log_at[pair] = now_ts

    def _profit_percent_from_entry(self, pair, current_price):
        entry = self.purchase_prices.get(pair, 0.0)
        if entry <= 0 or current_price <= 0:
            return None
        return ((current_price - entry) / entry) * 100.0

    def _compute_atr(self, pair, period=None):
        """Compute approximate ATR from stored price history (fallback to close diffs).
        Returns ATR in price units (EUR).
        """
        try:
            p = period if period is not None else self.atr_period
            history = list(self.analysis_tool.pair_price_history.get(pair, []))
            if not history or len(history) < 2:
                return None
            import numpy as _np
            prices = _np.array(history)
            # true range fallback: abs(diff of closes)
            tr = _np.abs(_np.diff(prices))
            if len(tr) < p:
                return float(_np.mean(tr)) if len(tr) > 0 else None
            return float(_np.mean(tr[-p:]))
        except Exception:
            return None

    def _required_take_profit_percent(self, pair):
        """Adaptive TP: in stronger momentum, demand a bit more profit before selling.
        When enable_atr_dynamic_tp is on, the TP floor is raised to atr_tp_multiplier × ATR%
        so the bot doesn't exit on small wiggles in volatile markets.
        """
        base_tp = self.take_profit_percent

        # ATR-based dynamic floor: require at least atr_tp_multiplier × ATR% profit
        if self.enable_atr_dynamic_tp:
            atr = self._compute_atr(pair)
            current_price = self.pair_prices.get(pair, 0)
            if atr and current_price > 0:
                atr_pct = (atr / current_price) * 100.0
                base_tp = max(base_tp, self.atr_tp_multiplier * atr_pct)

        if not self.adaptive_tp_enabled:
            fee_buffer = float(self.sell_fee_buffer_percent or 0.0)
            return min(self.max_tp_percent, base_tp + fee_buffer)

        score = abs(float(self.pair_scores.get(pair, 0.0)))
        # Map score band [20..50] -> +0..+4%
        bonus = 0.0
        if score > 20:
            bonus = min(4.0, (score - 20.0) / 30.0 * 4.0)

        # Add fee buffer so required TP covers fees (configurable)
        fee_buffer = float(self.sell_fee_buffer_percent or 0.0)
        return min(self.max_tp_percent, base_tp + bonus + fee_buffer)

    def _can_sell_profit_target(self, pair, current_price):
        """Only allow sell when current price is at/above configured take-profit threshold from entry.

        Applies a conservative 0.3% slippage buffer: we assume the actual fill
        will be slightly worse than the current mid-price (thin order books,
        taker fees on exit). This ensures the TP check is not fooled by a single
        tick that will never actually fill at that exact price.

        When enable_atr_dynamic_tp is active, the ATR-based TP floor is enforced
        even when the ATR trailing stop is running (prevents indicator exits before
        capturing the minimum expected move).
        """
        # With ATR trailing stop but WITHOUT dynamic TP: no indicator profit gate needed
        if self.enable_atr_stop and not self.enable_atr_dynamic_tp:
            return True  # Let winners run via trail; allow indicator-based exits without profit barrier
        # Conservative exit price accounting for slippage/spread
        slippage_pct = float(self.config.get('risk_management', {}).get('exit_slippage_buffer_pct', 0.3))
        conservative_exit_price = current_price * (1.0 - slippage_pct / 100.0)
        profit_pct = self._profit_percent_from_entry(pair, conservative_exit_price)
        if profit_pct is None:
            return False
        return profit_pct >= self._required_take_profit_percent(pair)

    def _update_trade_metrics(self, pair, pnl_eur):
        """Update per-pair win/loss counters and trigger loss-streak pause if needed.

        A winning trade (pnl_eur ≥ 0) resets the consecutive-loss counter and
        lifts any active loss-streak pause immediately.  After
        ``max_consecutive_losses`` losses the bot pauses new buys for
        ``pause_after_loss_streak_minutes`` minutes and recalculates the Kelly
        fraction for position sizing.
        """
        pnl_eur = float(pnl_eur)
        m = self.trade_metrics.setdefault(pair, {"closed": 0, "wins": 0, "losses": 0, "sum_pnl": 0.0})
        m["closed"] += 1
        m["sum_pnl"] += pnl_eur
        self.closed_trade_pnls.append(pnl_eur)
        if pnl_eur >= 0:
            m["wins"] += 1
            self.consecutive_losses = 0
            # A winning trade ends any active loss-streak pause immediately
            if self.trading_paused_until_ts > time.time():
                self.logger.info("Loss-streak pause lifted early after winning trade")
                self.trading_paused_until_ts = 0
        else:
            m["losses"] += 1
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.max_consecutive_losses:
                pause_sec = self.pause_after_loss_streak_minutes * 60
                self.trading_paused_until_ts = max(self.trading_paused_until_ts, int(time.time()) + pause_sec)
                self.logger.warning(
                    f"Loss-streak pause activated: {self.consecutive_losses} losses -> pause for {self.pause_after_loss_streak_minutes}m"
                )
                self.kelly_fraction = self._calculate_kelly_fraction()

    def _calculate_kelly_fraction(self):
        """Estimate Kelly fraction from realized closed trades (best-effort, bounded)."""
        try:
            pnls = list(self.closed_trade_pnls)
            if len(pnls) < 10:
                return 0.1

            wins = [p for p in pnls if p > 0]
            losses = [abs(p) for p in pnls if p < 0]
            if not wins or not losses:
                return 0.1

            win_rate = len(wins) / len(pnls)
            avg_win = sum(wins) / len(wins)
            avg_loss = sum(losses) / len(losses)
            if avg_win <= 0 or avg_loss <= 0:
                return 0.1

            b = avg_win / avg_loss
            kelly = win_rate - ((1 - win_rate) / b)
            return max(0.01, min(0.5, kelly))
        except Exception:
            return 0.1

    def check_take_profit_or_stop_loss(self):
        """Evaluate exits with TP first, then ATR stop, hard stop, time stop, then trailing stop."""
        for pair in self.trade_pairs:
            current_price = self.pair_prices.get(pair, 0)
            if current_price <= 0:
                continue

            # Long position exits
            holding = self.holdings.get(pair, 0)
            min_vol = self._get_min_volume(pair)
            if holding >= min_vol:
                prev_peak = self.peak_prices.get(pair, 0.0)
                self.peak_prices[pair] = max(prev_peak, current_price)

                change_percent = self._profit_percent_from_entry(pair, current_price)
                if change_percent is not None:
                    # ATR Trailing Stop Initialization & Update
                    if self.enable_atr_stop:
                        atr = self._compute_atr(pair)
                        if atr:
                            current_stop_info = self.stop_info.get(pair, {})
                            current_stop = current_stop_info.get('stop_price', 0)
                            
                            # Initialize if missing
                            if pair not in self.stop_info:
                                entry = self.purchase_prices.get(pair, current_price)
                                init_stop = max(0.0, entry - (atr * self.atr_multiplier))
                                self.stop_info[pair] = {'stop_price': init_stop, 'type': 'ATR'}
                                self.logger.info(f"Initialized ATR stop for {pair}: {init_stop:.4f} (atr={atr:.4f})")
                                current_stop = init_stop

                            # Ratchet up the stop: only move it UP
                            potential_stop = current_price - (atr * self.atr_trail_multiplier)
                            if potential_stop > current_stop:
                                self.stop_info[pair] = {'stop_price': potential_stop, 'type': 'ATR_TRAIL'}

                    # Exit Check 1: ATR/Trailing/Break-Even Stops
                    stop_data = self.stop_info.get(pair, {})
                    s_price = stop_data.get('stop_price')
                    if s_price is not None and current_price <= s_price:
                        return pair, stop_data.get('type', 'STOP'), change_percent

                    # Exit Check 2: Fixed Take Profit (ONLY if ATR trailing is NOT active)
                    if not self.enable_atr_stop:
                        req_tp = self._required_take_profit_percent(pair)
                        if self.take_profit_percent > 0 and change_percent >= req_tp:
                            return pair, "TAKE_PROFIT", change_percent

                    # Break-Even Stop-Loss logic (Manual activation if preferred)
                    if self.enable_break_even and change_percent >= self.break_even_trigger_pct:
                        entry_price = self.purchase_prices.get(pair, 0)
                        if entry_price > 0:
                            current_stop = self.stop_info.get(pair, {}).get('stop_price', 0)
                            if current_stop < entry_price:
                                self.stop_info[pair] = {'stop_price': entry_price, 'type': 'BREAK_EVEN'}
                                self.logger.info(f"BREAK-EVEN activated for {pair}: SL moved to entry ({entry_price:.4f})")

                    if self.enable_hard_stop_loss and change_percent <= -abs(self.hard_stop_loss_percent):
                        return pair, "HARD_STOP", change_percent

                    if self.enable_time_stop:
                        opened_at = self.entry_timestamps.get(pair)
                        if opened_at and (time.time() - opened_at) >= (self.time_stop_hours * 3600):
                            return pair, "TIME_STOP", change_percent

                    # Legacy simple Trailing Stop-Loss
                    if not self.enable_atr_stop and self.trailing_stop_percent > 0 and change_percent > 0:
                        drop_from_peak = ((self.peak_prices[pair] - current_price) / self.peak_prices[pair]) * 100.0
                        if drop_from_peak >= self.trailing_stop_percent:
                            return pair, "TRAILING_STOP", change_percent

            # Short position exits — use dedicated short TP/SL (lower than long TP)
            short_qty = self.short_qty.get(pair, 0.0)
            short_entry = self.short_entry_prices.get(pair, 0.0)
            if self.enable_live_shorts and short_qty > 0 and short_entry > 0:
                short_change_percent = ((short_entry - current_price) / short_entry) * 100.0
                if short_change_percent >= self.short_take_profit_percent:
                    return pair, "SHORT_TAKE_PROFIT", short_change_percent
                if short_change_percent <= -abs(self.short_stop_loss_percent):
                    return pair, "SHORT_HARD_STOP", short_change_percent
                if self.enable_time_stop:
                    opened_at = self.entry_timestamps.get(pair)
                    if opened_at and (time.time() - opened_at) >= (self.time_stop_hours * 3600):
                        return pair, "SHORT_TIME_STOP", short_change_percent

        return None, None, None

    def _warmup_pair_history(self, pair):
        """Seed price history from 60m OHLC candles when history is too sparse."""
        try:
            ohlc = self.api_client.get_ohlc_data(pair, interval=60)
            if not ohlc:
                return
            data_key = next((k for k in ohlc if k != 'last'), None)
            if not data_key:
                return
            closes = [float(row[4]) for row in ohlc[data_key]]
            self.analysis_tool.seed_from_ohlc(pair, closes)
        except Exception as e:
            self.logger.warning(f"OHLC warmup failed for {pair}: {e}")

    def analyze_all_pairs(self):
        """Fetch live prices, generate signals, and pick the best actionable pair.

        For every pair in ``self.trade_pairs``:
        1. Fetch current ticker price from Kraken.
        2. If price history is too sparse (<50 ticks), seed it from 60m OHLC candles.
        3. Update the flash-crash airbag history; trigger emergency sell if tripped.
        4. Call ``TechnicalAnalysis.generate_signal_with_score()`` to get
           a (signal, score) tuple.

        Returns (best_pair, best_signal, best_score) where *best_pair* is the
        pair with the highest |score| among actionable BUY/SELL signals.
        Returns (None, "HOLD", 0) when no pair has an actionable signal.
        """
        best_pair = None
        best_signal = "HOLD"
        best_score = 0

        for pair in self.trade_pairs:
            try:
                market_data = self.api_client.get_market_data(pair)
                if not market_data:
                    continue

                pair_key = list(market_data.keys())[0]
                current_price = float(market_data[pair_key]['c'][0])
                self.pair_prices[pair] = current_price

                # Seed history from 15m OHLC candles if not yet warmed up
                if len(self.analysis_tool._get_price_history(pair_key)) < self.analysis_tool.sma_long:
                    self._warmup_pair_history(pair)
                
                # Update airbag history and check for crash
                self._update_airbag_history(pair, current_price)
                if self._check_airbag_trigger(pair):
                    # Panic sell if holding
                    if self.holdings.get(pair, 0) >= self._get_min_volume(pair):
                        self.execute_sell_order(pair, current_price, require_profit_target=False, reason="CRASH_AIRBAG")

                signal, score = self.analysis_tool.generate_signal_with_score(market_data)
                self.pair_signals[pair] = signal
                self.pair_scores[pair] = score

                if signal in ["BUY", "SELL"] and abs(score) > abs(best_score):
                    best_pair = pair
                    best_signal = signal
                    best_score = score

                time.sleep(0.25)
            except Exception as e:
                self.logger.error(f"Error analyzing {pair}: {e}")

        return best_pair, best_signal, best_score

    def start_trading(self):
        """Run the main trading loop until the target balance is reached or Ctrl-C.

        Startup sequence:
        1. Fetch initial EUR balance and fix it as the performance baseline.
        2. Sync account state (holdings + entry prices from Kraken history).
        3. Reconcile any open orders left from a previous crash.
        4. Refresh deposit/withdrawal cashflows from the Kraken ledger.

        Each ~60-second cycle:
        - Resets daily PnL baseline at midnight.
        - Calls ``analyze_all_pairs()`` to find the best signal.
        - Evaluates take-profit / stop-loss exits (hard, ATR, trailing,
          break-even, time-stop) via ``check_take_profit_or_stop_loss()``.
        - Checks the bear shield (BTC 4h trend) and parks in FIAT if triggered.
        - Runs all BUY entry guards before calling ``execute_buy_order()``.
        - Checks for SELL signals and short opportunities.
        - Hot-reloads config every ``config_reload_interval`` seconds.

        Stops automatically when ``current_balance >= target_balance_eur``.
        """
        self.logger.info("=" * 60)
        self.logger.info("TRADING BOT STARTED - MULTI-PAIR MODE")
        self.logger.info(f"Watching: {', '.join(self.trade_pairs)}")
        self.logger.info(f"Target: {self.target_balance_eur} EUR")
        self.logger.info("=" * 60)

        print("=" * 60)
        print("KRAKEN TRADING BOT - MULTI-PAIR MODE")
        print(f"Watching {len(self.trade_pairs)} pairs: {', '.join(self.trade_pairs)}")
        print(f"Trade Amount: {self._get_trade_amount_eur()} EUR per trade")
        print(f"Target Balance: {self.target_balance_eur} EUR")
        print("Press Ctrl+C to stop")
        print("=" * 60)

        initial_balance = self.get_eur_balance()
        self.initial_balance_eur = initial_balance
        self.peak_balance = initial_balance
        self.daily_start_balance = initial_balance
        self._load_cumulative_pnl_state(initial_balance)
        self._sync_account_state(force_history=True)
        self._reconcile_open_orders()  # Detect orphaned orders from any previous crash
        self._refresh_cashflows_from_ledger(force=True)

        self.logger.info(f"Initial EUR Balance: {initial_balance:.2f} EUR")
        self.logger.debug("Performance baseline is fixed at startup; deposits/withdrawals are tracked separately")
        self.logger.info(f"Take-Profit: {self.take_profit_percent}% | Stop-Loss: {self.stop_loss_percent}%")

        # Log holdings and purchase prices for each pair so restarts are transparent
        for pair in self.trade_pairs:
            qty = self.holdings.get(pair, 0.0)
            avg = self.purchase_prices.get(pair, 0.0)
            min_v = self._get_min_volume(pair)
            if qty >= min_v:
                self.logger.info(
                    f"Startup position: {pair} qty={qty:.8f} avg_entry={avg:.4f} EUR"
                )
            else:
                self.logger.info(f"Startup position: {pair} — no holdings (qty={qty:.8f})")

        try:
            iteration = 0
            while True:
                iteration += 1
                try:
                    current_balance = self.get_eur_balance()

                    # Daily reset of daily_start_balance
                    now = datetime.now()
                    last_reset = datetime.fromtimestamp(self.last_daily_reset_ts)
                    if now.day != last_reset.day or now.month != last_reset.month or now.year != last_reset.year:
                        self.daily_start_balance = current_balance
                        self.last_daily_reset_ts = int(time.time())
                        self.logger.info(f"Daily start balance reset to {self.daily_start_balance:.2f} EUR")
                    if current_balance >= self.target_balance_eur:
                        self.logger.info(f"TARGET REACHED! Balance: {current_balance:.2f} EUR")
                        print(f"\nTARGET REACHED! Balance: {current_balance:.2f} EUR")
                        break

                    best_pair, best_signal, best_score = self.analyze_all_pairs()
                    self._sync_account_state()
                    
                    # Sentiment scan (opt-in)
                    self.sentiment_active = self._scan_news_sentiment() if self.enable_sentiment_guard else False

                    # Take profit / stop loss first
                    risk_pair, risk_type, change = self.check_take_profit_or_stop_loss()
                    if risk_pair:
                        price = self.pair_prices.get(risk_pair, 0)
                        print(f"\n[{risk_type}] {risk_pair} at {change:.2f}%")
                        if str(risk_type).startswith("SHORT_"):
                            self.execute_close_short_order(risk_pair, price)
                        else:
                            self.execute_sell_order(risk_pair, price)

                    self._refresh_cashflows_from_ledger()
                    adjusted_pnl = self._adjusted_pnl_eur(current_balance)
                    # update peak balance and compute portfolio drawdown
                    try:
                        self.peak_balance = max(getattr(self, 'peak_balance', current_balance), current_balance)
                        current_dd_pct = 0.0
                        if self.peak_balance > 0:
                            current_dd_pct = ((self.peak_balance - current_balance) / self.peak_balance) * 100.0
                            # enforce portfolio max drawdown circuit breaker
                            max_dd_cfg = float(self.config.get('risk_management', {}).get('max_drawdown_percent', 10.0))
                            if current_dd_pct >= max_dd_cfg:
                                pause_sec = int(self.pause_after_loss_streak_minutes * 60)
                                self.trading_paused_until_ts = max(self.trading_paused_until_ts, int(time.time()) + pause_sec)
                                self.logger.warning(f"Portfolio max-drawdown hit: {current_dd_pct:.2f}% >= {max_dd_cfg}%. Pausing buys for {self.pause_after_loss_streak_minutes} minutes.")
                    except Exception as e:
                        self.logger.debug(f"Drawdown calculation failed: {e}")
                        current_dd_pct = 0.0

                    regime_state = "RISK_ON" if self._is_risk_on_regime() else "RISK_OFF"
                    pause_state = "PAUSED" if self._is_temporarily_paused() else "ACTIVE"

                    label_map = {
                        "XBTEUR": "BTC", "XXBTZEUR": "BTC",
                        "ETHEUR": "ETH", "XETHZEUR": "ETH",
                        "SOLEUR": "SOL",
                        "ADAEUR": "ADA",
                        "DOTEUR": "DOT",
                        "XRPEUR": "XRP", "XXRPZEUR": "XRP",
                        "LINKEUR": "LINK",
                    }
                    pair_status = " ".join([
                        f"{label_map.get(p, p[:4])}:{self.pair_signals.get(p, '?')}" for p in self.trade_pairs
                    ])
                    status_msg = (
                        f"[{iteration}] {pair_status} | {regime_state}/{pause_state} | Best: {best_pair or 'NONE'} ({best_signal}) "
                        f"| Bal: {current_balance:.2f}EUR | Start: {self.initial_balance_eur:.2f}EUR "
                        f"| NetCF: +{self.net_deposits_eur:.2f}/-{self.net_withdrawals_eur:.2f}EUR "
                        f"| AdjPnL: {adjusted_pnl:+.2f}EUR | TotalPnL: {self.cumulative_pnl_eur(current_balance):+.2f}EUR | Trades: {self.trade_count}"
                    )
                    self.logger.info(status_msg)
                    print(f"\r{status_msg}", end="", flush=True)

                    if iteration % 10 == 0:
                        metric_parts = []
                        for p in self.trade_pairs:
                            m = self.trade_metrics.get(p, {})
                            closed = int(m.get("closed", 0))
                            if closed <= 0:
                                continue
                            winrate = (m.get("wins", 0) / closed) * 100.0
                            avg_pnl = m.get("sum_pnl", 0.0) / closed
                            metric_parts.append(f"{p}: WR {winrate:.0f}% avg {avg_pnl:.2f}EUR")
                        if metric_parts:
                            self.logger.info("METRICS | " + " | ".join(metric_parts))

                    # ── Bear Shield: check 4h trend and park in FIAT if confirmed downtrend ──
                    if self.enable_bear_shield:
                        bear_now = self._is_bear_market()
                        if bear_now and not self._bear_mode_active:
                            self.logger.warning(
                                f"BEAR SHIELD ACTIVATED: {self.bear_benchmark_pair} below EMA{self.bear_ema_period} "
                                f"on 4h for {self.bear_confirm_candles} candles — selling all positions, parking in EUR"
                            )
                            self._bear_mode_active = True
                            self._bear_shield_exit_all()
                        elif not bear_now and self._bear_mode_active:
                            self.logger.info("BEAR SHIELD DEACTIVATED: trend turned bullish — resuming normal trading")
                            self._bear_mode_active = False
                        elif bear_now:
                            now_ts = time.time()
                            if (now_ts - self._bear_last_log_ts) >= self.bear_log_interval_minutes * 60:
                                self.logger.info(
                                    f"BEAR SHIELD: still in bear mode ({self.bear_benchmark_pair} < EMA{self.bear_ema_period} on 4h)"
                                )
                                self._bear_last_log_ts = now_ts

                    if best_pair and best_signal != "HOLD" and not self._is_on_cooldown(best_pair) and not self._is_global_cooldown():
                        price = self.pair_prices.get(best_pair, 0)
                        if best_signal == "BUY":
                            score = float(self.pair_scores.get(best_pair, 0.0))
                            if self._is_temporarily_paused():
                                self.logger.warning("BUY paused: loss-streak cooling period active")
                                self.kelly_fraction = self._calculate_kelly_fraction()
                            elif self._daily_drawdown_hit():
                                self.logger.warning("BUY paused: daily loss limit reached")
                                self.kelly_fraction = self._calculate_kelly_fraction()
                            elif self._bear_mode_active:
                                self.logger.info("BUY skipped: BEAR SHIELD active (parked in FIAT)")
                            elif self.enable_regime_filter and not self._is_risk_on_regime():
                                self.logger.info("BUY skipped: regime filter is RISK_OFF")
                            elif score < self.min_buy_score:
                                self.logger.info(f"BUY skipped for {best_pair}: weak score {score:.2f} < min {self.min_buy_score:.2f}")
                            elif self.sentiment_active:
                                self.logger.info(f"BUY skipped for {best_pair}: sentiment guard active")
                            elif self._count_open_positions() >= self.max_open_positions:
                                self.logger.info("BUY skipped: max open positions reached")
                            elif not self._is_mtf_trend_bullish(best_pair):
                                self.logger.info(f"BUY skipped for {best_pair}: MTF trend (1h) is not bullish")
                            elif not self._is_trading_hours():
                                self.logger.info(
                                    f"BUY skipped: outside trading hours "
                                    f"({self.trading_hours_start_utc}:00-{self.trading_hours_end_utc}:00 UTC)"
                                )
                            elif not self._has_sufficient_volume(best_pair):
                                pass  # already logged inside _has_sufficient_volume
                            else:
                                self.execute_buy_order(best_pair, price)
                        elif best_signal == "SELL":
                            min_vol = self._get_min_volume(best_pair)
                            if self.holdings.get(best_pair, 0) >= min_vol:
                                if self._can_sell_profit_target(best_pair, price):
                                    self.execute_sell_order(best_pair, price)
                                else:
                                    pp = self._profit_percent_from_entry(best_pair, price)
                                    req = self._required_take_profit_percent(best_pair)
                                    pp_str = f"{pp:.2f}" if pp is not None else 'n/a'
                                    self.logger.info(
                                        f"SELL skipped for {best_pair}: profit target not reached ({pp_str}% < {req:.2f}%)"
                                    )
                            elif self.enable_live_shorts and self.short_qty.get(best_pair, 0.0) <= 0:
                                # Open short mostly in risk-off environments or very strong negative score
                                score = float(self.pair_scores.get(best_pair, 0.0))
                                if (not self._is_risk_on_regime()) or score <= -self.min_buy_score:
                                    self.execute_open_short_order(best_pair, price)
                                else:
                                    self.logger.info("SHORT skipped: regime not risk-off and sell score not strong enough")
                            elif self.enable_live_shorts and self.short_qty.get(best_pair, 0.0) > 0:
                                # If already short, consider close on reversal buy impulse
                                score = float(self.pair_scores.get(best_pair, 0.0))
                                if score >= self.min_buy_score:
                                    self.execute_close_short_order(best_pair, price)
                            else:
                                self._log_empty_sell_signal_throttled(best_pair)

                    time_since_reload = (datetime.now() - self.last_config_reload).total_seconds()
                    if time_since_reload >= self.config_reload_interval:
                        self.reload_config()

                except Exception as e:
                    self.logger.error(
                        f"Unhandled error in trading loop (iteration {iteration}): {e}",
                        exc_info=True,
                    )

                _sd_notify_watchdog()
                time.sleep(self.loop_interval_sec)

        except KeyboardInterrupt:
            final_balance = self.get_eur_balance()
            self.logger.info(f"Bot stopped by user. Final balance: {final_balance:.2f} EUR")
            print(f"\nTrading bot stopped. Final Balance: {final_balance:.2f} EUR")

    def _journal_trade(self, ttype, pair, volume, price, pnl_eur, reason, extra=None):
        try:
            import csv, os, datetime, json
            os.makedirs(os.path.dirname(self.journal_path), exist_ok=True)
            header = ['ts','type','pair','volume','price','pnl_eur','reason','extra']
            exists = os.path.exists(self.journal_path)
            with open(self.journal_path,'a',newline='') as fh:
                writer = csv.writer(fh)
                if not exists:
                    writer.writerow(header)
                row = [datetime.datetime.utcnow().isoformat(), ttype, pair, f"{volume:.8f}", f"{price:.6f}", f"{pnl_eur:.6f}", reason, str(extra or '')]
                writer.writerow(row)
            # also write structured JSON line for observability
            try:
                os.makedirs(os.path.dirname(self.json_journal_path), exist_ok=True)
                j = {
                    'ts': datetime.datetime.utcnow().isoformat(),
                    'type': ttype,
                    'pair': pair,
                    'volume': float(volume),
                    'price': float(price),
                    'pnl_eur': float(pnl_eur),
                    'reason': reason,
                    'extra': extra or {},
                    'balance_eur': float(self.get_eur_balance()),
                    'consecutive_losses': int(self.consecutive_losses),
                }
                # include current drawdown if available
                try:
                    peak = float(getattr(self, 'peak_balance', j['balance_eur']))
                    if peak > 0:
                        j['current_drawdown_pct'] = round(((peak - j['balance_eur']) / peak) * 100.0, 2)
                except Exception:
                    pass
                with open(self.json_journal_path, 'a') as jf:
                    jf.write(json.dumps(j) + "\n")
            except Exception as e:
                self.logger.error(f"Error writing JSON trade log: {e}")
        except Exception as e:
            self.logger.error(f"Error writing trade journal: {e}")

    def execute_buy_order(self, pair, price):
        """Place a post-only (maker) spot BUY order for *pair* at *price*.

        Position size is determined by ``_get_dynamic_trade_amount_eur()``
        (allocation % of available EUR, ATR-scaled, regime-adjusted).
        After a successful fill the ATR stop level is initialised and the
        trade is journalled to CSV and JSONL.  Rejects if available EUR is
        below ``min_trade_eur``.
        """
        try:
            available_eur = self._available_eur_for_buy()
            min_trade_eur = float(self.config.get('risk_management', {}).get('min_trade_eur', 10.0))
            planned_eur = self._get_dynamic_trade_amount_eur(pair, available_eur)
            if planned_eur < min_trade_eur:
                self.logger.info(f"BUY skipped for {pair}: insufficient free EUR ({available_eur:.2f})")
                return

            volume = self._calculate_volume(pair, price, available_eur=planned_eur)
            self.logger.info(f"Placing BUY order (MAKER/POST-ONLY): {volume:.6f} {pair} at {price:.2f} EUR")

            result = self.api_client.place_order(pair=pair, direction='buy', volume=volume, price=price, post_only=True)
            if result:
                self.trade_count += 1
                now_ts = time.time()
                self.last_trade_at[pair] = now_ts
                self.last_global_trade_at = now_ts
                self._save_cooldown_state()
                self.peak_prices[pair] = max(self.peak_prices.get(pair, 0.0), price)
                if self.entry_timestamps.get(pair) is None:
                    self.entry_timestamps[pair] = int(time.time())
                self._sync_account_state(force_history=True)
                self.logger.info(f"BUY ORDER SUCCESS: {result}")
                self.logger.info(f"BUY SUMMARY: {pair} {volume:.6f} (~{volume*price:.2f} EUR)")
                # initialize ATR stop if enabled
                if self.enable_atr_stop:
                    atr = self._compute_atr(pair)
                    if atr is not None:
                        init_stop = max(0.0, price - (atr * self.atr_multiplier))
                        self.stop_info[pair] = {'stop_price': init_stop, 'type': 'ATR'}
                        self.logger.info(f"Initialized ATR stop for {pair}: {init_stop:.4f} (atr={atr:.4f})")
                # journal buy
                self._journal_trade('BUY', pair, volume, price, 0.0, 'BUY_EXECUTED', extra={'result': result})
                print(f"\n[BUY] {volume:.6f} {pair} (~{volume*price:.2f} EUR) - Trade #{self.trade_count}")
                _notifier.send(
                    f"🟢 <b>BUY</b> #{self.trade_count}\n"
                    f"Pair: {pair}\n"
                    f"Volume: {volume:.6f}  (~{volume*price:.2f} EUR)\n"
                    f"Price: {price:.4f} EUR"
                )
            else:
                self.logger.error(f"BUY ORDER FAILED for {pair}")
        except Exception as e:
            self.logger.error(f"Error executing buy order: {e}", exc_info=True)

    def execute_sell_order(self, pair, price, require_profit_target=True, reason=None):
        """Place a post-only (maker) spot SELL order to close the long position.

        When ``require_profit_target=True`` (default), the sell is blocked
        unless ``_can_sell_profit_target()`` passes (i.e. profit ≥ required TP
        after slippage buffer).  Pass ``require_profit_target=False`` for
        emergency exits (airbag, bear shield, time-stop).

        Clears position state, updates trade metrics, and journals the trade.
        """
        try:
            volume = self.holdings.get(pair, 0)
            if volume < self._get_min_volume(pair):
                self.logger.info(f"SELL skipped for {pair}: no holdings")
                return

            if require_profit_target and not self._can_sell_profit_target(pair, price):
                pp = self._profit_percent_from_entry(pair, price)
                pp_str = f"{pp:.2f}" if pp is not None else 'n/a'
                self.logger.info(
                    f"SELL blocked for {pair}: target {self.take_profit_percent:.2f}% not reached ({pp_str}%)"
                )
                return

            avg_entry = self.purchase_prices.get(pair, 0.0)
            est_profit_pct = self._profit_percent_from_entry(pair, price)
            est_profit_eur = (price - avg_entry) * volume if avg_entry > 0 else 0.0

            self.logger.info(f"Placing SELL order (MAKER/POST-ONLY): {volume:.6f} {pair} at {price:.2f} EUR")
            result = self.api_client.place_order(pair=pair, direction='sell', volume=volume, price=price, post_only=True)
            if result:
                self.trade_count += 1
                now_ts = time.time()
                self.last_trade_at[pair] = now_ts
                self.last_global_trade_at = now_ts
                self._save_cooldown_state()
                # clear position state
                self.purchase_prices[pair] = 0.0
                self.peak_prices[pair] = 0.0
                self.entry_timestamps[pair] = None
                # clear stop info
                if pair in self.stop_info:
                    del self.stop_info[pair]
                self._sync_account_state(force_history=True)
                self.logger.info(f"SELL ORDER SUCCESS: {result}")
                self.logger.info(f"SELL SUMMARY: {pair} {volume:.6f} (~{volume*price:.2f} EUR)")
                self.logger.info(
                    f"SELL PNL ESTIMATE {pair}: {est_profit_eur:.2f} EUR ({est_profit_pct if est_profit_pct is not None else 0:.2f}%)"
                )
                self._update_trade_metrics(pair, est_profit_eur)
                # journal sell
                self._journal_trade('SELL', pair, volume, price, est_profit_eur, reason or 'SELL_EXECUTED')
                print(f"\n[SELL] {volume:.6f} {pair} (~{volume*price:.2f} EUR) - Trade #{self.trade_count}")
                pnl_sign = "🟢" if est_profit_eur >= 0 else "🔴"
                _notifier.send(
                    f"{pnl_sign} <b>SELL</b> #{self.trade_count}\n"
                    f"Pair: {pair}\n"
                    f"Volume: {volume:.6f}  (~{volume*price:.2f} EUR)\n"
                    f"Price: {price:.4f} EUR\n"
                    f"P&amp;L est.: {est_profit_eur:+.2f} EUR"
                )
            else:
                self.logger.error(f"SELL ORDER FAILED for {pair}")
        except Exception as e:
            self.logger.error(f"Error executing sell order: {e}", exc_info=True)

    def execute_open_short_order(self, pair, price):
        """Open a leveraged short position on *pair* at *price*.

        Uses the configured ``short_leverage`` (default 2×) via Kraken margin.
        Position notional is capped at ``max_short_notional_eur``.  Only placed
        when no short is already open for this pair.  Blocked when
        ``enable_live_shorts`` is False in config.
        """
        try:
            if not self.enable_live_shorts:
                return
            if self.short_qty.get(pair, 0.0) > 0:
                return

            notional = min(self.max_short_notional_eur, self._get_dynamic_trade_amount_eur(pair, self._available_eur_for_buy()))
            if notional <= 0 or price <= 0:
                return
            volume = max(self._get_min_volume(pair), notional / price)
            self.logger.info(
                f"Placing SHORT OPEN order: {volume:.6f} {pair} at ~{price:.2f} EUR (lev={self.short_leverage}x)"
            )
            result = self.api_client.place_order(pair=pair, direction='sell', volume=volume, leverage=self.short_leverage)
            if result:
                self.trade_count += 1
                now_ts = time.time()
                self.last_trade_at[pair] = now_ts
                self.last_global_trade_at = now_ts
                self._save_cooldown_state()
                self.short_qty[pair] = volume
                self.short_entry_prices[pair] = price
                self.entry_timestamps[pair] = int(now_ts)
                self.logger.info(f"SHORT OPEN SUCCESS: {result}")
                self.logger.info(f"SHORT OPEN SUMMARY: {pair} {volume:.6f} (~{notional:.2f} EUR)")
                print(f"\n[SHORT OPEN] {volume:.6f} {pair} (~{notional:.2f} EUR) - Trade #{self.trade_count}")
                _notifier.send(
                    f"🔻 <b>SHORT OPEN</b> #{self.trade_count}\n"
                    f"Pair: {pair}\n"
                    f"Volume: {volume:.6f}  (~{notional:.2f} EUR)\n"
                    f"Price: {price:.4f} EUR  |  Leverage: {self.short_leverage}x"
                )
            else:
                self.logger.error(f"SHORT OPEN FAILED for {pair}")
        except Exception as e:
            self.logger.error(f"Error opening short order: {e}", exc_info=True)

    def execute_close_short_order(self, pair, price):
        """Close an open leveraged short position on *pair* at *price*.

        Places a reduce-only BUY order with the same leverage as the original
        short.  Computes and records estimated P&L: profit when price fell from
        entry, loss when it rose.  Clears short state and journals the trade.
        """
        try:
            qty = self.short_qty.get(pair, 0.0)
            entry = self.short_entry_prices.get(pair, 0.0)
            if qty <= 0 or entry <= 0:
                return
            pnl_eur = (entry - price) * qty
            pnl_pct = ((entry - price) / entry) * 100.0
            self.logger.info(f"Placing SHORT CLOSE order: {qty:.6f} {pair} at ~{price:.2f} EUR")
            result = self.api_client.place_order(
                pair=pair,
                direction='buy',
                volume=qty,
                leverage=self.short_leverage,
                reduce_only=True,
            )
            if result:
                self.trade_count += 1
                now_ts = time.time()
                self.last_trade_at[pair] = now_ts
                self.last_global_trade_at = now_ts
                self._save_cooldown_state()
                self.short_qty[pair] = 0.0
                self.short_entry_prices[pair] = 0.0
                self.entry_timestamps[pair] = None
                self.logger.info(f"SHORT CLOSE SUCCESS: {result}")
                self.logger.info(f"SHORT CLOSE SUMMARY: {pair} {qty:.6f} (~{qty*price:.2f} EUR)")
                self.logger.info(f"SHORT PNL ESTIMATE {pair}: {pnl_eur:.2f} EUR ({pnl_pct:.2f}%)")
                self._update_trade_metrics(pair, pnl_eur)
                print(f"\n[SHORT CLOSE] {qty:.6f} {pair} - Trade #{self.trade_count}")
                pnl_sign = "🟢" if pnl_eur >= 0 else "🔴"
                _notifier.send(
                    f"{pnl_sign} <b>SHORT CLOSE</b> #{self.trade_count}\n"
                    f"Pair: {pair}\n"
                    f"Volume: {qty:.6f}  (~{qty*price:.2f} EUR)\n"
                    f"Entry: {entry:.4f} EUR  |  Exit: {price:.4f} EUR\n"
                    f"P&amp;L est.: {pnl_eur:+.2f} EUR ({pnl_pct:+.2f}%)"
                )
            else:
                self.logger.error(f"SHORT CLOSE FAILED for {pair}")
        except Exception as e:
            self.logger.error(f"Error closing short order: {e}", exc_info=True)


class Backtester:
    def __init__(self, api_client, config):
        self.api_client = api_client
        self.config = config
        self.logger = logging.getLogger(__name__)

    def run(self):
        import numpy as np
        from datetime import datetime

        print("Backtesting mode activated.")

        # Parameters
        pairs = self.config['bot_settings'].get('trade_pairs', ['XBTEUR'])
        start_date = datetime(2024, 1, 1)
        interval = 60
        initial_balance = 1000.0

        # Fetch OHLC data
        ohlc_data = {}
        for pair in pairs:
            data = self.api_client.get_ohlc_data(pair, interval, int(start_date.timestamp()))
            if data:
                ohlc_data[pair] = data
            else:
                self.logger.warning(f"No OHLC data for {pair}")
        self.kelly_fraction = self._calculate_kelly_fraction()

        if not ohlc_data:
            print("No data available for backtesting.")
            return

        # Simulate trading
        balance = initial_balance
        positions = {pair: 0.0 for pair in pairs}
        entry_prices = {pair: 0.0 for pair in pairs}
        pnls = []
        balances = [initial_balance]
        max_drawdown = 0.0
        peak_balance = initial_balance

        analysis = TechnicalAnalysis()

        for i in range(len(ohlc_data[pairs[0]])):
            price = float(ohlc_data[pairs[0]][i][4])  # close
            market_data = {pairs[0]: {'c': [price]}}

            signal, score = analysis.generate_signal_with_score(market_data)

            if signal == 'BUY' and positions[pairs[0]] == 0:
                volume = balance / price * 0.1
                positions[pairs[0]] = volume
                entry_prices[pairs[0]] = price
                balance -= volume * price
            elif signal == 'SELL' and positions[pairs[0]] > 0:
                pnl = (price - entry_prices[pairs[0]]) * positions[pairs[0]]
                balance += positions[pairs[0]] * price
                pnls.append(pnl)
                positions[pairs[0]] = 0

            current_balance = balance + sum(positions[p] * price for p in positions)
            balances.append(current_balance)
            peak_balance = max(peak_balance, current_balance)
            drawdown = (peak_balance - current_balance) / peak_balance
            max_drawdown = max(max_drawdown, drawdown)

        # Calculate metrics
        returns = np.diff(balances) / balances[:-1]
        total_return = (balances[-1] - initial_balance) / initial_balance
        sharpe = np.mean(returns) / np.std(returns) if np.std(returns) > 0 else 0
        downside_returns = returns[returns < 0]
        sortino = np.mean(returns) / np.std(downside_returns) if len(downside_returns) > 0 else 0

        print(f"Total Return: {total_return:.2%}")
        print(f"Sharpe Ratio: {sharpe:.2f}")
        print(f"Sortino Ratio: {sortino:.2f}")
        print(f"Max Drawdown: {max_drawdown:.2%}")
        print(f"Total Trades: {len(pnls)}")
        print(f"Win Rate: {sum(1 for p in pnls if p > 0) / len(pnls):.2%}" if pnls else "Win Rate: N/A")
