import time
import datetime
import pandas as pd
import random
import json
import os

from bot.config.settings import Config
from bot.core.session import get_session
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.data_fetcher import DataFetcher
from bot.utils.logger import logger
from bot.config.instruments import get_instrument
from bot.utils.expiry_calculator import get_next_weekly_expiry
from bot.core.trade_repo import trade_repo
from bot.core.order_manager import OrderManager
from bot.core.position_manager import LadderedTrailingManager
from bot.core.oi_analyzer import OIAnalyzer

class PullbackStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.data_fetcher = DataFetcher(self.api)
        self.order_manager = OrderManager(self.api, dry_run=self.dry_run)
        self.trailing_manager = LadderedTrailingManager(self.order_manager, self.data_fetcher)
        self.oi_analyzer = OIAnalyzer(self.api, self.token_loader)
        
        self.active_position = None
        self.running = True  # Flag for graceful shutdown
        self.last_sync_time = 0
        self.last_trailing_check = 0
        self.last_analysis = {}
        self.risk_multiplier = 1.0
        self._last_status_log = 0
        self._last_sl_hit_time = 0
        
        self.sync_state()

    def export_state(self):
        """Exports current strategy state to JSON for UI consumption."""
        try:
            data_dir = "data"
            os.makedirs(data_dir, exist_ok=True)
            state = {
                "timestamp": datetime.datetime.now().isoformat(),
                "analysis": self.last_analysis,
                "active_position": self.active_position,
                "dry_run": self.dry_run
            }
            with open(os.path.join(data_dir, "strategy_state.json"), "w") as f:
                json.dump(state, f, indent=4, default=str)
        except Exception as e:
            logger.error(f"Failed to export strategy state: {e}")

    def sync_state(self):
        """Synchronizes local state with broker positions."""
        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            
            resp = self.api.position()
            found_active = None
            
            if resp and resp.get('status') and resp.get('data'):
                positions = resp['data']
                for pos in positions:
                    # Check for open option buying position (qty > 0)
                    if int(pos.get('netqty', 0)) > 0 and pos.get('symbolname') == Config.ACTIVE_SYMBOL:
                        token = pos.get('symboltoken')
                        symbol = pos.get('tradingsymbol')
                        qty = int(pos['netqty'])
                        entry_price = float(pos.get('avgprice', 0.0))

                        # 📅 EXPIRY GUARD: never resume monitoring a contract past its expiry date
                        if trade_repo._is_symbol_expired(symbol):
                            logger.warning(f"⚠️ [Pullback] Skipping past-expiry contract from broker: {symbol}")
                            continue
                        
                        found_active = {
                            'leg': 'CE' if pos.get('optiontype') == 'CE' else 'PE',
                            'symbol': symbol,
                            'qty': qty,
                            'token': token,
                            'entry_price': entry_price,
                            'sl_price': entry_price * 0.70, # Fallback 30% SL
                            'target_price': entry_price * 1.5,
                            'ladder_stage': 0
                        }
                        
                        # Sync with database
                        mode = "PAPER" if self.dry_run else "LIVE"
                        existing_trade = trade_repo.get_active_trade(mode=mode, symbol=symbol)
                        if existing_trade and existing_trade.get('strategy') == "PULLBACK":
                            found_active['id'] = existing_trade['id']
                            found_active['sl_price'] = existing_trade.get('sl_price', found_active['sl_price'])
                            found_active['sl_order_id'] = existing_trade.get('sl_order_id')
                        
                        break
            
            if found_active:
                if self.active_position is None:
                    self.active_position = found_active
                    logger.info(f"♻️ RECOVERY: Found Active Pullback Trade on Broker! {found_active['symbol']}")
            elif self.active_position is not None:
                # Check for broker-side SL hit
                sl_oid = self.active_position.get('sl_order_id')
                if sl_oid and not self.dry_run:
                    status_info = self.order_manager.get_order_status(sl_oid)
                    if status_info and status_info.get('status') == 'COMPLETE':
                        fill_price = status_info.get('price', self.active_position['sl_price'])
                        logger.info(f"🛡️ SYNC: Broker-Side SL Hit detected for {self.active_position['symbol']} @ ₹{fill_price}")
                        self._last_sl_hit_time = time.time()
                        
                        trade_id = self.active_position.get('id')
                        entry_p = self.active_position['entry_price']
                        qty = self.active_position['qty']
                        pnl = (fill_price - entry_p) * qty
                        
                        trade_repo.close_trade(trade_id=trade_id, symbol=self.active_position['symbol'], exit_price=fill_price, pnl=round(pnl, 2), exit_reason="BROKER_SL_HIT")
                        self.active_position = None
                        return

                logger.warning("⚠️ SYNC: Active Position closed externally! Resetting State.")
                trade_repo.close_trade(symbol=self.active_position['symbol'], exit_reason="EXTERNAL_SYNC_RESET")
                self.active_position = None
                
        except Exception as e:
            logger.error(f"Sync State Error: {e}")

    def calculate_indicators(self, df):
        """Calculates 20-EMA and VWAP on the candles DataFrame."""
        df = df.copy()
        n = len(df)
        df['EMA20'] = df['close'].ewm(span=min(20, n), adjust=False).mean()
        
        # Calculate VWAP
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        volume = df['volume'].fillna(0)
        if (volume == 0).all():
            volume = pd.Series(1.0, index=df.index)
            
        df['date'] = df['timestamp'].dt.date
        df['tp_vol'] = typical_price * volume
        df['volume_adj'] = volume
        
        df['cum_tp_vol'] = df.groupby('date')['tp_vol'].cumsum()
        df['cum_vol'] = df.groupby('date')['volume_adj'].cumsum()
        df['VWAP'] = df['cum_tp_vol'] / df['cum_vol'].replace(0, 1e-10)
        
        return df

    def check_pullback_signal(self, df):
        """Scans the last completed candles for touch/rejection signals."""
        if len(df) < 22:
            return None
            
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        
        # Trend check: Close must align above or below EMA20 and VWAP
        is_bullish_trend = last_row['close'] > last_row['EMA20'] and last_row['close'] > last_row['VWAP']
        is_bearish_trend = last_row['close'] < last_row['EMA20'] and last_row['close'] < last_row['VWAP']
        
        if not is_bullish_trend and not is_bearish_trend:
            return None
            
        if is_bullish_trend:
            support1 = prev_row['EMA20']
            support2 = prev_row['VWAP']
            
            # Touched or came within 0.05% proximity of either line
            touched_ema = prev_row['low'] <= support1 * 1.0005
            touched_vwap = prev_row['low'] <= support2 * 1.0005
            
            # Closed above the line (rejection)
            rejected_ema = touched_ema and prev_row['close'] >= support1
            rejected_vwap = touched_vwap and prev_row['close'] >= support2
            
            is_bullish_candle = prev_row['close'] >= prev_row['open']
            
            if (rejected_ema or rejected_vwap) and is_bullish_candle:
                return "CE"
                
        elif is_bearish_trend:
            resistance1 = prev_row['EMA20']
            resistance2 = prev_row['VWAP']
            
            touched_ema = prev_row['high'] >= resistance1 * 0.9995
            touched_vwap = prev_row['high'] >= resistance2 * 0.9995
            
            rejected_ema = touched_ema and prev_row['close'] <= resistance1
            rejected_vwap = touched_vwap and prev_row['close'] <= resistance2
            
            is_bearish_candle = prev_row['close'] <= prev_row['open']
            
            if (rejected_ema or rejected_vwap) and is_bearish_candle:
                return "PE"
                
        return None

    def execute(self, expiry):
        """Runs the strategy monitoring and execution loop."""
        logger.info(f"--- VWAP & 20-EMA PULLBACK STRATEGY ({expiry}) ---")
        self.sync_state()

        if not self.gatekeeper.is_market_open():
            logger.warning("Pullback: 🛑 Execution Aborted - Market is Closed.")
            return
        if self.gatekeeper.is_blackout_period():
            logger.info("Pullback: ⏸️ Mid-day Blackout - Suspended.")
            return
        if not self.gatekeeper.check_max_daily_loss(0.0):
            logger.critical("Pullback: 🛑 Max Daily Loss reached. Aborting.")
            return

        now = datetime.datetime.now()
        minute = now.minute
        remainder = minute % 5
        minutes_to_add = 5 - remainder
        next_check = now + datetime.timedelta(minutes=minutes_to_add)
        next_check = next_check.replace(second=5, microsecond=0)

        logger.info("📡 Performing Initial Market Analysis Pulse...")
        spot_tok = get_instrument(Config.ACTIVE_SYMBOL).analysis_token
        df = self.data_fetcher.fetch_latest_candles(spot_tok)
        
        if df is not None and not df.empty:
            df = self.calculate_indicators(df)
            last = df.iloc[-1]
            self.last_analysis = {
                "ema20": round(last['EMA20'], 2),
                "vwap": round(last['VWAP'], 2),
                "close": round(last['close'], 2),
                "regime": "TRENDING" if (last['close'] > last['EMA20']) else "SIDEWAYS"
            }
            self.export_state()
            logger.info(f"✅ Initial Pulse Complete. Spot: {last['close']:.1f} | EMA20: {last['EMA20']:.1f} | VWAP: {last['VWAP']:.1f}")

        while self.running:
            if not self.running:
                break
                
            try:
                # Fast Loop Check
                if not self.dry_run and time.time() - self.last_sync_time > 15:
                    self.sync_state()
                    self.last_sync_time = time.time()
                    
                if self.active_position:
                    # Trailing Stop check
                    if time.time() - self.last_trailing_check > 3:
                        self.check_trailing_stop()
                        self.last_trailing_check = time.time()
                        
                    # Emergency Exit Check
                    token = self.active_position['token']
                    qty = self.active_position['qty']
                    entry_p = self.active_position['entry_price']
                    curr_ltp = self.data_fetcher.get_ltp(token, exchange="NFO")
                    
                    if curr_ltp and curr_ltp > 0:
                        curr_unrealized = (curr_ltp - entry_p) * qty
                        if not self.gatekeeper.check_max_daily_loss(curr_unrealized):
                            logger.critical("🛑 EMERGENCY EXIT: Daily Loss Limit Breached.")
                            self.close_position("MAX_DAILY_LOSS")
                            break
                            
                # Time exit checks
                now_time = datetime.datetime.now().time()
                if self.trailing_manager.is_killswitch_time():
                    logger.info("⏰ Time Killswitch (15:10) triggered. Closing positions.")
                    if self.active_position:
                        self.close_position("TIME_KILLSWITCH")
                    break
                    
                if not self.dry_run and now_time >= datetime.time(*Config.STRATEGY_EXIT_TIME):
                    logger.info("Market Closed. Stopping strategy.")
                    if self.active_position:
                        self.close_position("TIME_EXIT")
                    break
                    
                # Slow Loop checks (every 5-min candle close)
                if datetime.datetime.now() >= next_check:
                    _now = datetime.datetime.now()
                    next_check = _now + datetime.timedelta(minutes=(5 - (_now.minute % 5)))
                    next_check = next_check.replace(second=5, microsecond=0)
                    
                    if not self.gatekeeper.check_max_daily_loss(0.0):
                        logger.critical("Pullback: 🛑 Skipped trend check — Max Daily Loss limit reached.")
                        time.sleep(10)
                        continue
                        
                    if not self.active_position:
                        # Fetch candles and scan for pullback signals
                        df_check = self.data_fetcher.fetch_latest_candles(spot_tok)
                        if df_check is not None and not df_check.empty:
                            df_check = self.calculate_indicators(df_check)
                            signal = self.check_pullback_signal(df_check)
                            
                            if signal:
                                logger.info(f"⚡ PULLBACK SIGNAL DETECTED: {signal}")
                                self.enter_position(expiry, signal)

                time.sleep(1)
            except Exception as e:
                logger.error(f"Strategy Loop Error: {e}")
                time.sleep(1)

    def enter_position(self, expiry, leg):
        """Enters an option buying position."""
        if not self.gatekeeper.check_max_daily_loss(0.0):
            return
            
        if self._last_sl_hit_time > 0 and time.time() - self._last_sl_hit_time < 300:
            logger.info("⏳ Post-SL cooldown active. Skipping entry.")
            return

        nifty_ltp = self.data_fetcher.get_ltp(get_instrument(Config.ACTIVE_SYMBOL).analysis_token, exchange="NSE")
        if not nifty_ltp:
            logger.error("Could not fetch Nifty LTP for Entry.")
            return

        # Select ATM option strike
        active_sym = Config.ACTIVE_SYMBOL
        instr = get_instrument(active_sym)
        strike_diff = instr.strike_step
        strike = round(nifty_ltp / strike_diff) * strike_diff
        
        token, symbol = self.token_loader.get_token(instr.name, expiry, strike, leg, instrument_type=instr.instrument_type, exchange=instr.option_exchange)
        if not token:
            logger.error(f"Token not found for strike {strike} {leg}")
            return

        # Get option price
        quote_ltp = 0.0
        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            q_resp = self.api.ltpData("NFO", symbol, token)
            if q_resp and q_resp.get('status'):
                quote_ltp = float(q_resp['data']['ltp'])
        except Exception as e:
            logger.warning(f"Could not fetch option LTP for entry: {e}")
            return

        capital = self.gatekeeper.get_current_capital()
        if capital <= 0: capital = Config.SIMULATION_CAPITAL
        
        tier = Config.get_tier(capital)
        
        # Pullback Stop Loss is set to 25% of premium
        sl_points = quote_ltp * 0.25
        
        # Lot calculation
        margin_per_lot = quote_ltp * Config.NIFTY_LOT_SIZE
        lots = self.gatekeeper.get_compounded_lots(margin_per_lot=margin_per_lot, multiplier=self.risk_multiplier)
        qty = lots * Config.NIFTY_LOT_SIZE

        if qty <= 0:
            logger.warning("Sizing returned 0 lots. Skipping trade.")
            return

        if not self.gatekeeper.check_trade_viability(quote_ltp, qty):
            return

        estimated_cost = quote_ltp * qty
        if not self.dry_run and not self.gatekeeper.check_trade_margin(estimated_cost):
            logger.warning("Insufficient funds for trade.")
            return

        if not self.gatekeeper.check_instrument_cooldown(symbol):
            return

        logger.info(f">>> [Pullback] Entry Confirmed for {symbol} @ ₹{quote_ltp} | Qty: {qty}")
        sl_price = max(0.1, round(round((quote_ltp - sl_points) / 0.05) * 0.05, 2))
        target_price = quote_ltp + (sl_points * 1.5) # 1.5:1 reward to risk

        trade_context = {
            "strategy": "PULLBACK",
            "entry_price": quote_ltp,
            "lots": lots,
            "sl_price": sl_price,
            "target_price": target_price
        }

        if self.dry_run:
            self.active_position = {
                'leg': leg, 'symbol': symbol, 'qty': qty, 'token': token,
                'entry_price': quote_ltp,
                'sl_price': sl_price,
                'target_price': target_price,
                'ladder_stage': 0,
                'context': trade_context
            }
            tid = self.order_manager.update_trade_fill(symbol, "PULLBACK", quote_ltp)
            if tid:
                self.active_position['id'] = tid
                trade_repo.update_trade_context(tid, trade_context)
            return

        # Place Order
        try:
            _raw_limit = quote_ltp * (1.0 + tier.entry_slippage_pct)
            limit_price = round(round(_raw_limit / 0.05) * 0.05, 2)
            
            oid = self.order_manager.place_smart_limit(
                symbol, token, qty, limit_price,
                transaction_type="BUY",
                strategy_name="PULLBACK"
            )
            if not oid:
                return
                
            fill_result = self.wait_for_fill(oid)
            if fill_result['status'] in ['REJECTED', 'CANCELLED']:
                return
                
            fill_price = fill_result['price'] or quote_ltp
            actual_sl = max(0.1, round(round((fill_price - sl_points) / 0.05) * 0.05, 2))
            
            # Place initial broker SL order
            sl_oid = self.order_manager.place_sl_order(symbol, token, qty, actual_sl, leg)
            
            self.active_position = {
                'leg': leg, 'symbol': symbol, 'qty': qty, 'token': token,
                'entry_price': fill_price,
                'sl_price': actual_sl,
                'target_price': fill_price + (sl_points * 1.5),
                'sl_order_id': sl_oid,
                'ladder_stage': 0,
                'context': trade_context
            }
            
            tid = trade_repo.save_trade(
                symbol=symbol, qty=qty, entry_price=fill_price, leg=leg,
                sl_price=actual_sl, strategy="PULLBACK", mode="LIVE"
            )
            if tid:
                self.active_position['id'] = tid
                if sl_oid:
                    trade_repo.update_sl_order(tid, sl_oid)
                    
        except Exception as e:
            logger.error(f"Pullback Order Error: {e}")

    def check_trailing_stop(self):
        """Trails position using position manager stage-gates."""
        if not self.active_position:
            return False
            
        token = self.active_position['token']
        symbol = self.active_position['symbol']
        entry_price = self.active_position['entry_price']
        
        ltp = self.data_fetcher.get_ltp(token, exchange="NFO")
        if not ltp or ltp == 0:
            return False
            
        if time.time() - self._last_status_log > 30:
            self._last_status_log = time.time()
            qty = self.active_position.get('qty', 0)
            pnl = round((ltp - entry_price) * qty, 2)
            pnl_pct = round((pnl / (entry_price * qty)) * 100, 2) if entry_price > 0 else 0
            logger.info(
                f"Pullback: 📊 MONITOR | LTP=₹{ltp:.1f} | Entry=₹{entry_price:.1f} | "
                f"SL=₹{self.active_position.get('sl_price', 0.0):.1f} | P&L=₹{pnl:+,.0f} ({pnl_pct:+.1f}%)"
            )
            
        should_close, exit_type = self.trailing_manager.update_trailing_sl("PULLBACK", self.active_position, ltp)
        if should_close:
            logger.info(f"🛑 Pullback Trailing Exit Triggered ({exit_type})")
            self.close_position(f"LADDERED_SL_{exit_type}", exit_type=exit_type)
            self._last_sl_hit_time = time.time()
            return True
            
        return False

    def close_position(self, reason, exit_type="MARKET"):
        """Closes open position and cancels any pending stop-loss orders."""
        if not self.active_position:
            return
            
        symbol = self.active_position['symbol']
        token = self.active_position['token']
        qty = self.active_position['qty']
        
        logger.info(f"Exit: Closing Pullback position of {symbol} (Reason: {reason})")
        
        exit_price = 0.0
        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            q_resp = self.api.ltpData("NFO", symbol, token)
            if q_resp and q_resp.get('status'):
                exit_price = float(q_resp['data']['ltp'])
        except:
            pass
            
        if exit_price == 0.0:
            exit_price = self.active_position.get('entry_price', 0.0)

        # Cancel broker SL
        sl_oid = self.active_position.get('sl_order_id')
        if sl_oid and not self.dry_run:
            self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")

        if not self.dry_run:
            try:
                orderparams = {
                    "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                    "transactiontype": "SELL", "exchange": "NFO", 
                    "ordertype": "MARKET", "producttype": "INTRADAY", 
                    "duration": "DAY", "quantity": qty
                }
                oid = self.order_manager.place_order(orderparams)
                if oid:
                    fill_res = self.wait_for_fill(oid)
                    if fill_res['status'] == 'FILLED':
                        exit_price = fill_res['price'] or exit_price
            except Exception as e:
                logger.error(f"Error placing exit order: {e}")

        mode = "PAPER" if self.dry_run else "LIVE"
        pnl = (exit_price - self.active_position['entry_price']) * qty
        
        trade_repo.close_trade(
            trade_id=self.active_position.get('id'), symbol=symbol,
            exit_price=exit_price, pnl=round(pnl, 2), exit_reason=reason
        )
        
        self.active_position = None

    def wait_for_fill(self, order_id, timeout_seconds=15):
        """Blocks until the order is filled, cancelled, or rejected."""
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            status_info = self.order_manager.get_order_status(order_id)
            if status_info:
                status = status_info.get('status')
                if status in ['COMPLETE', 'FILLED']:
                    return {'status': 'FILLED', 'price': float(status_info.get('averageprice', 0))}
                elif status in ['REJECTED', 'CANCELLED']:
                    return {'status': status, 'message': status_info.get('text', 'Cancelled')}
            time.sleep(0.5)
        
        # Timeout - attempt cancellation
        logger.warning(f"Order {order_id} timed out. Attempting cancellation...")
        self.order_manager.cancel_order(order_id)
        return {'status': 'CANCELLED', 'message': 'Timeout'}

    def stop(self):
        """Requests the strategy to stop and exits any open positions."""
        self.running = False
        if self.active_position:
            self.close_position("USER_STOPPED")
