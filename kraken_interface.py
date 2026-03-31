# Kraken API Interface Wrapper

import krakenex
import logging
import time
import toml
import os
from order_lock import acquire_order_lock


class KrakenAPI:
    """Wrapper for Kraken API interactions."""

    def __init__(self, api_key, api_secret):
        self.api = krakenex.API(api_key, api_secret)
        self.logger = logging.getLogger(__name__)
        self.rate_limit_delay = 0.5  # seconds between API calls

    def _handle_error(self, response, action):
        if response.get('error'):
            self.logger.error(f"{action} - API Error: {response['error']}")
            return True
        return False

    def _is_rate_limit_error(self, response):
        """Return True if the Kraken response indicates a rate-limit error."""
        errs = response.get('error', [])
        if isinstance(errs, str):
            errs = [errs]
        return any('Rate limit' in str(e) or 'EAPI:Rate limit' in str(e) for e in errs)

    def _query_public_with_backoff(self, endpoint, params=None, retries=4):
        """Query a public Kraken endpoint with exponential backoff on rate-limit errors."""
        params = params or {}
        last_error = None
        for attempt in range(retries):
            delay = self.rate_limit_delay * (2 ** attempt)
            time.sleep(delay)
            try:
                response = self.api.query_public(endpoint, params)
                if self._is_rate_limit_error(response):
                    last_error = response.get('error')
                    self.logger.warning(
                        f"{endpoint} rate-limited (attempt {attempt + 1}/{retries}), "
                        f"backing off {delay:.1f}s …"
                    )
                    continue
                return response
            except Exception as e:
                self.logger.exception(f"Exception in {endpoint} attempt {attempt + 1}: {e}")
                return None
        self.logger.error(f"{endpoint} failed after {retries} retries due to rate limit: {last_error}")
        return None

    def get_account_balance(self):
        try:
            response = self.api.query_private('Balance')
            if self._handle_error(response, "Balance Query"):
                return None
            return response.get('result', {})
        except Exception as e:
            self.logger.exception(f"Error fetching account balance: {e}")
            return None

    def get_market_data(self, pair):
        try:
            response = self._query_public_with_backoff('Ticker', {'pair': pair})
            if response is None:
                return None
            if self._handle_error(response, f"Market Data for {pair}"):
                return None
            return response.get('result', {})
        except Exception as e:
            self.logger.exception(f"Error fetching market data for {pair}: {e}")
            return None

    def get_ohlc_data(self, pair, interval=60, since=None):
        """Fetch OHLC data from Kraken.
        Intervals: 1, 5, 15, 30, 60, 240, 1440, 10080, 21600
        """
        try:
            params = {'pair': pair, 'interval': interval}
            if since:
                params['since'] = since
            response = self._query_public_with_backoff('OHLC', params)
            if response is None:
                return None
            if self._handle_error(response, f"OHLC Data for {pair}"):
                return None
            return response.get('result', {})
        except Exception as e:
            self.logger.exception(f"Error fetching OHLC data for {pair}: {e}")
            return None

    def get_asset_pairs(self):
        """Fetch tradable asset pairs from Kraken."""
        try:
            response = self._query_public_with_backoff('AssetPairs')
            if response is None:
                return {}
            if self._handle_error(response, "AssetPairs Query"):
                return {}
            return response.get('result', {})
        except Exception as e:
            self.logger.exception(f"Error fetching asset pairs: {e}")
            return {}

    def place_order(self, pair, direction, volume, price=None, leverage=None, post_only=False, reduce_only=False):
        try:
            if direction not in ['buy', 'sell']:
                self.logger.error(f"Invalid direction: {direction}. Must be 'buy' or 'sell'")
                return None
            if float(volume) <= 0:
                self.logger.error(f"Invalid volume: {volume}. Must be positive")
                return None

            time.sleep(self.rate_limit_delay)

            # Load risk config (if available)
            cfg_path = os.path.join(os.path.dirname(__file__), 'config.toml')
            risk_cfg = {}
            try:
                if os.path.exists(cfg_path):
                    cfg = toml.load(cfg_path)
                    risk_cfg = cfg.get('risk_management', {})
            except Exception:
                self.logger.debug('Failed to load config for risk checks')

            enable_caps = risk_cfg.get('enable_parallel_caps', False)
            min_buffer = float(risk_cfg.get('min_free_margin_buffer', 50.0))
            max_notional_side = float(risk_cfg.get('max_notional_per_side', 200.0))
            max_pos_per_side = int(risk_cfg.get('max_open_positions_per_side', 10))
            min_auto_notional = float(risk_cfg.get('min_auto_scale_notional', 1.0))

            # Preflight checks and auto-scaling
            desired_price = None
            if price:
                desired_price = float(price)
            else:
                # fetch market price to estimate notional
                try:
                    m = self.get_market_data(pair)
                    # find first key
                    if isinstance(m, dict) and m:
                        first = next(iter(m.values()))
                        desired_price = float(first.get('c')[0])
                except Exception:
                    desired_price = None

            desired_notional = None
            try:
                if desired_price is not None:
                    desired_notional = desired_price * float(volume)
            except Exception:
                desired_notional = None

            op_result = {}
            if enable_caps or reduce_only:
                try:
                    time.sleep(self.rate_limit_delay)
                    op = self.api.query_private('OpenPositions')
                    if op.get('error'):
                        self.logger.debug(f"OpenPositions error during preflight: {op.get('error')}")
                    else:
                        op_result = op.get('result', {})
                except Exception as e:
                    self.logger.debug(f"Exception fetching open positions: {e}")

            # Reduce-only safety: never enlarge net exposure; clamp volume to closable amount.
            if reduce_only:
                target_type = 'sell' if direction == 'buy' else 'buy'
                closable_volume = 0.0
                for _, p in op_result.items():
                    try:
                        if str(p.get('pair', '')).upper() != str(pair).upper():
                            continue
                        if str(p.get('type', '')).lower() != target_type:
                            continue
                        closable_volume += float(p.get('vol', 0.0) or 0.0)
                    except Exception:
                        continue

                if closable_volume <= 0:
                    self.logger.info(f"Reduce-only block: no opposing open position to close for {pair}")
                    return None

                req_vol = float(volume)
                if req_vol > closable_volume:
                    self.logger.info(
                        f"Reduce-only clamp on {pair}: requested {req_vol:.8f} -> {closable_volume:.8f}"
                    )
                    volume = closable_volume

            # If caps enabled, evaluate current exposure (only for opening/increasing orders)
            if enable_caps and not reduce_only:
                exposure_long = 0.0
                exposure_short = 0.0
                count_long = 0
                count_short = 0
                for _, p in op_result.items():
                    try:
                        c = float(p.get('cost', 0))
                        if p.get('type') == 'sell':
                            exposure_short += c
                            count_short += 1
                        else:
                            exposure_long += c
                            count_long += 1
                    except Exception:
                        continue

                side_exposure = exposure_long if direction == 'buy' else exposure_short
                side_count = count_long if direction == 'buy' else count_short

                # dynamic allowed notional by side cap (consider equity-based dynamic cap)
                try:
                    tb_resp = self.api.query_private('TradeBalance')
                    if tb_resp.get('error'):
                        tb = {}
                    else:
                        tb = tb_resp.get('result', {})
                except Exception:
                    tb = {}

                # equity estimation ('e' or 'eb' or 'zeur')
                equity = 0.0
                for ek in ('e', 'eb', 'zeur'):
                    if ek in tb:
                        try:
                            equity = float(tb.get(ek, 0.0))
                            break
                        except Exception:
                            continue

                dyn_frac = float(risk_cfg.get('dynamic_notional_fraction', 0.4))
                configured_max = float(max_notional_side)
                dynamic_cap = max(50.0, equity * dyn_frac)
                allowed_by_side = min(configured_max, dynamic_cap) - side_exposure
                if allowed_by_side < 0:
                    allowed_by_side = 0.0

                # check free margin
                try:
                    time.sleep(self.rate_limit_delay)
                    tb2 = self.api.query_private('TradeBalance')
                    if tb2.get('error'):
                        self.logger.debug(f"TradeBalance error during preflight: {tb2.get('error')}")
                        mf = 0.0
                    else:
                        mf = float(tb2.get('result', {}).get('mf', 0.0))
                except Exception:
                    mf = 0.0

                # compute allowed by margin (simple estimate using leverage)
                lev = float(leverage) if leverage else 1.0
                allowed_by_margin = max(0.0, (mf - min_buffer) * lev)

                # final allowed notional
                final_allowed = min(allowed_by_side, allowed_by_margin)

                aggressive = bool(risk_cfg.get('aggressive_autoscale', False))

                if desired_notional is not None and desired_notional > final_allowed:
                    # scale down if aggressive, otherwise block
                    if final_allowed < min_auto_notional:
                        self.logger.info(
                            f"Blocking order: not enough allowed notional ({final_allowed:.2f} EUR) "
                            f"to place requested {desired_notional:.2f} EUR"
                        )
                        return None
                    scale = final_allowed / desired_notional
                    new_volume = float(volume) * scale
                    if aggressive:
                        self.logger.info(
                            f"Aggressive auto-scaling order volume from {volume} to {new_volume:.8f} "
                            f"due to risk caps (allowed {final_allowed:.2f} EUR)"
                        )
                        volume = new_volume
                    else:
                        self.logger.info(
                            f"Auto-scaling order volume from {volume} to {new_volume:.8f} "
                            f"due to risk caps (allowed {final_allowed:.2f} EUR)"
                        )
                        volume = new_volume
                # enforce max positions per side
                if side_count >= max_pos_per_side:
                    self.logger.info(
                        f"Blocking order: side already has {side_count} open positions (max {max_pos_per_side})"
                    )
                    return None

            # Use limit if price provided, otherwise market
            # If post_only is True, force limit order
            order_type = 'limit' if (price or post_only) else 'market'
            
            order_params = {
                'pair': pair,
                'type': direction,
                'ordertype': order_type,
                'volume': str(volume)
            }
            
            if price:
                order_params['price'] = str(price)
            
            if post_only:
                order_params['oflags'] = 'post'
                
            if leverage:
                order_params['leverage'] = str(leverage)

            with acquire_order_lock(timeout_seconds=5.0) as locked:
                if not locked:
                    self.logger.warning("Order lock busy; skipping AddOrder to avoid concurrent execution race")
                    return None
                response = self.api.query_private('AddOrder', order_params)
            if self._handle_error(response, f"Place {direction.upper()} Order"):
                return None
            result = response.get('result', {})
            self.logger.info(
                f"Order placed successfully: {direction} {volume} {pair} "
                f"({order_type}, post_only={post_only}, reduce_only={reduce_only})"
            )
            return result
        except Exception as e:
            self.logger.exception(f"Error placing order: {e}")
            return None

    def get_open_orders(self):
        try:
            time.sleep(self.rate_limit_delay)
            response = self.api.query_private('OpenOrders')
            if self._handle_error(response, "Open Orders Query"):
                return None
            return response.get('result', {})
        except Exception as e:
            self.logger.exception(f"Error fetching open orders: {e}")
            return None

    def cancel_order(self, order_id):
        try:
            time.sleep(self.rate_limit_delay)
            response = self.api.query_private('CancelOrder', {'txid': order_id})
            if self._handle_error(response, f"Cancel Order {order_id}"):
                return None
            result = response.get('result', {})
            self.logger.info(f"Order {order_id} cancelled successfully")
            return result
        except Exception as e:
            self.logger.exception(f"Error cancelling order {order_id}: {e}")
            return None

    def place_order_with_fallback(self, pair, direction, volume, price=None, leverage=None, post_only=False, reduce_only=False, timeout_sec=30):
        """Attempt a limit/post-only order first (if price provided), then fallback to market after timeout if not filled.

        This is a conservative fallback wrapper around place_order. It only takes effect when price is provided or post_only is True.
        """
        try:
            # If no price specified, just place market order
            if not price and not post_only:
                return self.place_order(pair, direction, volume, price=None, leverage=leverage, post_only=False, reduce_only=reduce_only)

            # place limit/post-only order
            order = self.place_order(pair, direction, volume, price=price, leverage=leverage, post_only=post_only, reduce_only=reduce_only)
            if not order:
                self.logger.debug("Initial limit order failed or was rejected; placing market instead")
                return self.place_order(pair, direction, volume, price=None, leverage=leverage, post_only=False, reduce_only=reduce_only)

            txid = None
            # Extract txid depending on API result structure
            if isinstance(order, dict):
                txs = order.get('txid') or order.get('tx') or None
                if isinstance(txs, list) and txs:
                    txid = txs[0]
                elif isinstance(txs, str):
                    txid = txs

            # If no txid, assume filled or cannot monitor -- return what we have
            if not txid:
                return order

            # Poll open orders until filled or timeout
            start = time.time()
            while time.time() - start < float(timeout_sec):
                time.sleep(self.rate_limit_delay)
                open_orders = self.get_open_orders() or {}
                # open_orders structure may contain txids under 'open'
                # check for presence of txid
                found = False
                try:
                    # open_orders may be a dict with 'open' key
                    if isinstance(open_orders, dict):
                        open_map = open_orders.get('open', open_orders)
                        if txid in open_map or any(str(txid) in k for k in open_map.keys()):
                            found = True
                except Exception:
                    found = True
                if not found:
                    # order not found among open orders -> likely filled
                    self.logger.info(f"Order {txid} no longer open (likely filled)")
                    return order
            # timeout reached, cancel and send market order
            self.logger.info(f"Timeout waiting for order {txid}; cancelling and placing market order")
            try:
                self.cancel_order(txid)
            except Exception:
                self.logger.debug("Cancel failed or no longer valid")
            return self.place_order(pair, direction, volume, price=None, leverage=leverage, post_only=False, reduce_only=reduce_only)
        except Exception as e:
            self.logger.exception(f"Error in place_order_with_fallback: {e}")
            return None

    def get_ledgers(self, asset=None, start=None, fetch_all=False, max_pages=200):
        """Fetch ledger entries (deposits/withdrawals/trades/etc)."""
        try:
            params = {}
            if asset:
                params['asset'] = asset
            if start:
                params['start'] = int(start)

            if not fetch_all:
                time.sleep(self.rate_limit_delay)
                response = self.api.query_private('Ledgers', params)
                if self._handle_error(response, "Ledgers Query"):
                    return None
                return response.get('result', {}).get('ledger', {})

            all_entries = {}
            ofs = 0
            page = 0
            total_count = None

            while page < max_pages:
                query_params = dict(params)
                query_params['ofs'] = ofs
                time.sleep(self.rate_limit_delay)
                response = self.api.query_private('Ledgers', query_params)
                if self._handle_error(response, f"Ledgers Query (ofs={ofs})"):
                    return all_entries if all_entries else None

                result = response.get('result', {})
                ledger = result.get('ledger', {}) or {}
                total_count = result.get('count', total_count)

                if not ledger:
                    break

                all_entries.update(ledger)
                batch_len = len(ledger)
                ofs += batch_len
                page += 1

                if total_count is not None and ofs >= int(total_count):
                    break

            return all_entries
        except Exception as e:
            self.logger.exception(f"Error fetching ledgers: {e}")
            return None

    def get_trade_history(self, start=None, fetch_all=False, max_pages=200):
        try:
            params = {}
            if start:
                params['start'] = int(start)

            # Default behavior: single page (Kraken default limit, usually 50)
            if not fetch_all:
                time.sleep(self.rate_limit_delay)
                response = self.api.query_private('TradesHistory', params)
                if self._handle_error(response, "Trade History Query"):
                    return None
                return response.get('result', {}).get('trades', {})

            # Paginated fetch: collect all pages from start timestamp
            all_trades = {}
            ofs = 0
            page = 0
            total_count = None

            while page < max_pages:
                query_params = dict(params)
                query_params['ofs'] = ofs
                time.sleep(self.rate_limit_delay)
                response = self.api.query_private('TradesHistory', query_params)
                if self._handle_error(response, f"Trade History Query (ofs={ofs})"):
                    return all_trades if all_trades else None

                result = response.get('result', {})
                trades = result.get('trades', {}) or {}
                total_count = result.get('count', total_count)

                if not trades:
                    break

                all_trades.update(trades)
                batch_len = len(trades)
                ofs += batch_len
                page += 1

                if total_count is not None and ofs >= int(total_count):
                    break

            return all_trades
        except Exception as e:
            self.logger.exception(f"Error fetching trade history: {e}")
            return None
