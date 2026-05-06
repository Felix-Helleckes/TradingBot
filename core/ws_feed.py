"""Kraken WebSocket v2 price feed — background thread, zero REST API calls.

Maintains a thread-safe dict of current last-prices by subscribing to the
public ``ticker`` channel on ``wss://ws.kraken.com/v2``.

Usage
-----
    from core.ws_feed import KrakenWSFeed
    feed = KrakenWSFeed(["XXBTZEUR", "XETHZEUR", "SOLEUR", "XXRPZEUR"])
    feed.start()
    # ... in the trading loop ...
    price = feed.get_price("XXBTZEUR")   # None if not yet received / stale
    feed.stop()

Requirements
------------
    pip install "websockets>=10.0"

Fallback
--------
If ``websockets`` is not installed or the connection drops, ``get_price()``
returns ``None`` and the caller falls back to REST polling transparently.
The bot never blocks waiting for the WebSocket; it is a pure optimisation.
"""

import asyncio
import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    logger.debug(
        "websockets library not installed — WebSocket feed disabled. "
        "Run: pip install 'websockets>=10.0'"
    )

# ── Pair alias tables ─────────────────────────────────────────────────────────
# Kraken REST altname  →  WS v2 symbol (slash notation)
_REST_TO_WS = {
    "XXBTZEUR": "BTC/EUR",
    "XBTEUR":   "BTC/EUR",
    "XETHZEUR": "ETH/EUR",
    "ETHEUR":   "ETH/EUR",
    "SOLEUR":   "SOL/EUR",
    "XXRPZEUR": "XRP/EUR",
    "XRPEUR":   "XRP/EUR",
    "ADAEUR":   "ADA/EUR",
    "DOTEUR":   "DOT/EUR",
    "LINKEUR":  "LINK/EUR",
    "MATICEUR": "MATIC/EUR",
    "POLEUR":   "POL/EUR",
}

_WS_URL = "wss://ws.kraken.com/v2"
_PRICE_STALE_SEC = 90.0  # treat cached price as unavailable after this age


class KrakenWSFeed:
    """Background WebSocket price feed for Kraken spot pairs.

    Thread-safe: ``get_price()`` can be called from any thread at any time.
    The WS listener runs in a dedicated daemon thread with its own asyncio
    event loop.  Auto-reconnects with exponential back-off (5 s … 60 s).
    """

    def __init__(self, pairs):
        self._pairs = [p.upper() for p in pairs]
        # Unique WS symbols we can subscribe to
        seen = set()
        self._ws_symbols = []
        for p in self._pairs:
            sym = _REST_TO_WS.get(p)
            if sym and sym not in seen:
                self._ws_symbols.append(sym)
                seen.add(sym)

        # ws-symbol → (price: float, timestamp: float)
        self._prices = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._loop = None
        self.connected = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        """Start the background feed thread.  No-op if websockets unavailable."""
        if not _WS_AVAILABLE:
            logger.warning(
                "KrakenWSFeed: websockets not installed — falling back to REST polling. "
                "pip install 'websockets>=10.0'"
            )
            return
        if not self._ws_symbols:
            logger.warning("KrakenWSFeed: no mappable pairs — feed not started")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_thread, daemon=True, name="KrakenWSFeed"
        )
        self._thread.start()
        logger.info(f"KrakenWSFeed started for: {self._ws_symbols}")

    def stop(self):
        """Signal the background thread to stop (best-effort)."""
        self._running = False
        if self._loop and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

    def get_price(self, pair):
        """Return the latest price for *pair* (REST altname), or ``None`` if stale/unknown.

        Returns ``None`` when:
        - ``websockets`` is not installed
        - no message received yet for this pair
        - last message is older than ``_PRICE_STALE_SEC`` (90 s)
        """
        ws_sym = _REST_TO_WS.get(pair.upper())
        if not ws_sym:
            return None
        with self._lock:
            entry = self._prices.get(ws_sym)
        if entry is None:
            return None
        price, ts = entry
        if (time.time() - ts) > _PRICE_STALE_SEC:
            return None
        return price

    def is_healthy(self):
        """Return True when the feed is connected and has received recent prices."""
        if not self.connected:
            return False
        with self._lock:
            if not self._prices:
                return False
            oldest = min(ts for _, ts in self._prices.values())
        return (time.time() - oldest) < _PRICE_STALE_SEC

    # ── Background thread ──────────────────────────────────────────────────────

    def _run_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._feed_loop())
        except Exception as e:
            logger.error(f"KrakenWSFeed thread terminated unexpectedly: {e}")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _feed_loop(self):
        backoff = 5
        while self._running:
            try:
                async with websockets.connect(
                    _WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self.connected = True
                    backoff = 5  # reset back-off on successful connection
                    logger.info("KrakenWSFeed: WebSocket connected")

                    # Subscribe to public ticker channel
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "params": {
                            "channel": "ticker",
                            "symbol": self._ws_symbols,
                        },
                    }))

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            self._handle_message(json.loads(raw))
                        except Exception:
                            pass

                self.connected = False

            except Exception as e:
                self.connected = False
                if self._running:
                    logger.warning(
                        f"KrakenWSFeed: disconnected ({type(e).__name__}: {e}). "
                        f"Reconnecting in {backoff}s…"
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(60, backoff * 2)

    def _handle_message(self, msg):
        """Parse a Kraken WS v2 ticker message and cache the last-price."""
        if not isinstance(msg, dict) or msg.get("channel") != "ticker":
            return
        now = time.time()
        for tick in msg.get("data", []):
            sym = tick.get("symbol")
            last = tick.get("last")
            if sym and last is not None:
                with self._lock:
                    self._prices[sym] = (float(last), now)
