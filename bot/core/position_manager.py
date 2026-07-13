import time
import datetime
from bot.utils.logger import logger
from bot.config.settings import Config
from bot.core.trade_repo import trade_repo

class LadderedTrailingManager:
    """
    Implements a Stage-Gate 'Profit-Floor & Runner' trailing system.
    Phase 1: Initial Risk (1x ATR or 20pts)
    Stage 1: Floor Locked (PnL >= 1500 -> SL = Entry + 16)
    Stage 2: Buffer (PnL >= 2600 -> SL = Entry + 30)
    Stage 3: 3R Hunter (PnL >= 3900 -> 1m 9-EMA Trail)
    Stage 4: Moonshot Mode (PnL >= 5000 -> Book 75% profit, 1 Lot Runner)
    """

    def __init__(self, order_manager, data_fetcher):
        self.order_manager = order_manager
        self.data_fetcher = data_fetcher
        self._last_ema_check = 0

    def update_trailing_sl(self, strategy_name, active_position, ltp):
        """
        Updates SL based on profit gates.
        Returns (should_close, exit_type).
        """
        if not active_position:
            return False, None

        entry_price = active_position.get('entry_price', 0)
        qty = active_position.get('qty', 0)
        current_sl = active_position.get('sl_price', 0)
        token = active_position.get('token')
        symbol = active_position.get('symbol')
        
        if entry_price == 0 or qty == 0:
            return False, None

        unrealized_pnl = (ltp - entry_price) * qty
        points_up = ltp - entry_price
        current_stage = active_position.get('ladder_stage', 0)

        # --- DYNAMIC SCALING FIX ---
        # Standard gates (15, 25, 40, 55) are for expensive options.
        # For cheap/OTM options (Gamma), 15 pts could be a 100%+ move.
        # We cap required points at multiples of the ACTUAL strategy risk.
        # Fallback to 20.0 standard risk if not provided.
        risk_unit = float(active_position.get('initial_risk', 20.0))
        
        # Compute dynamic gate floors based on ATR Multiples (Institutional Standard)
        # We replace fixed points with ATR-based room.
        atr = float(active_position.get('atr', risk_unit))
        threshold_0_5 = round(1.0 * atr, 1) # Breakeven at 1 ATR move
        threshold_1_0 = round(1.5 * atr, 1) # Stage 1 at 1.5 ATR move
        threshold_2_0 = round(2.5 * atr, 1) # Stage 2 at 2.5 ATR move
        threshold_3_0 = round(3.5 * atr, 1) # Stage 3 at 3.5 ATR move
        threshold_4_0 = round(5.0 * atr, 1) # Stage 4 at 5.0 ATR move 🚀

        # Stage 0.5: Breakeven Shield (No-Loss Mode)
        if current_stage < 0.5 and points_up >= threshold_0_5:
            new_sl = entry_price + 2.0 # Minimum lock to cover charges
            if new_sl > current_sl:
                logger.info(f"🛡️ Stage 0.5: Breakeven Shield Active (+{points_up:.1f}pts) | SL: {new_sl}")
                self._apply_sl_update(strategy_name, active_position, new_sl, stage=0.5)
                current_stage = 0.5

        # Stage 1: The Base Floor (More generous)
        if current_stage < 1 and points_up >= threshold_1_0:
            # Move SL to Entry + 0.3 ATR (Just enough to cover costs and minor profit)
            new_sl = entry_price + round(0.3 * atr, 1)
            if new_sl > current_sl:
                logger.info(f"🛡️ Stage 1: Floor Locked at 0.3 ATR (+{points_up:.1f}pts) | SL: {new_sl}")
                self._apply_sl_update(strategy_name, active_position, new_sl, stage=1)
                current_stage = 1

        # Stage 2: The Buffer & PARTIAL BOOKING
        if current_stage < 2 and points_up >= threshold_2_0:
            # Maintain 1.5 ATR room for the runner
            new_sl = ltp - round(1.5 * atr, 1)
            if new_sl > current_sl:
                logger.info(f"📈 Stage 2: Adaptive Buffer (1.5 ATR Room) | LTP: {ltp} | SL: {new_sl}")
                
                # --- EARLY PARTIAL BOOKING ---
                total_qty = active_position.get('qty', 0)
                if total_qty > Config.NIFTY_LOT_SIZE:
                    lots_to_sell = max(1, (total_qty // Config.NIFTY_LOT_SIZE) // 2)
                    qty_to_sell = lots_to_sell * Config.NIFTY_LOT_SIZE
                    remaining_sl_qty = total_qty - qty_to_sell
                    
                    logger.info(f"⏳ Updating Broker SL Order and reducing quantity from {total_qty} to {remaining_sl_qty} to free up margin for partial booking...")
                    sl_updated_ok = self._apply_sl_update(strategy_name, active_position, new_sl, stage=2, override_qty=remaining_sl_qty)
                    
                    if not sl_updated_ok:
                        logger.error(f"❌ Failed to update Stop-Loss price/quantity on broker. Aborting partial booking.")
                    else:
                        logger.info(f"💰 PARTIAL BOOKING: Securing {lots_to_sell} lots. Letting the rest RUN.")
                        oid = self.order_manager.place_smart_limit(symbol, token, qty_to_sell, ltp, "SELL", strategy_name=strategy_name)
                        if oid:
                            pnl_booked = (ltp - entry_price) * qty_to_sell
                            trade_repo.reduce_position(active_position['id'], qty_to_sell, ltp, pnl_booked, "STAGE_2_PARTIAL")
                            active_position['qty'] = total_qty - qty_to_sell
                        else:
                            # Rollback SL quantity to original if booking failed/cancelled
                            sl_oid = active_position.get('sl_order_id')
                            if sl_oid and not self.order_manager.dry_run:
                                logger.warning(f"⚠️ Partial booking order failed/cancelled. Restoring Broker SL Order {sl_oid} quantity to {total_qty}...")
                                self.order_manager.modify_sl_order(
                                    sl_oid, active_position['sl_price'], symbol, token, total_qty
                                )
                else:
                    self._apply_sl_update(strategy_name, active_position, new_sl, stage=2)
                
                current_stage = 2

        # Stage 3: The Runner (1m 21-EMA Trail with Volatility Buffer)
        if current_stage < 3 and points_up >= threshold_3_0:
            logger.info(f"🏃 Stage 3: Runner Mode Active. Transitioning to 1m 21-EMA Trail.")
            active_position['ladder_stage'] = 3
            current_stage = 3

        # Stage 3.5: ROI Stop Gate (Lock in profit at 100% ROI)
        roi = points_up / entry_price
        if current_stage < 3.5 and roi >= 1.0:
            new_sl = entry_price * 1.5
            if new_sl > current_sl:
                # Dynamic scale out size: 25% on Expiries or Super-Parabolic trends to protect moonshots, 50% otherwise
                from bot.utils.expiry_calculator import get_next_weekly_expiry
                current_time = self._get_current_time()
                today_str = current_time.strftime("%d%b%Y").upper()
                is_expiry_day = (get_next_weekly_expiry(today=current_time.date()) == today_str)
                adx_val = self._get_current_adx()
                is_super_parabolic = (adx_val >= 40.0)
                
                if is_expiry_day or is_super_parabolic:
                    book_ratio = 0.25
                    logger.info(f"🏆 ROI Stop Gate: Expiry/Super-Parabolic active (ADX: {adx_val:.1f} | Expiry: {is_expiry_day}). Scale-out = 25% (preserving 75% runners) | SL: {new_sl}")
                else:
                    book_ratio = 0.50
                    logger.info(f"🏆 ROI Stop Gate: Normal regime active (ADX: {adx_val:.1f}). Scale-out = 50% (preserving 50% runners) | SL: {new_sl}")
                
                total_qty = active_position.get('qty', 0)
                if total_qty > Config.NIFTY_LOT_SIZE:
                    lots_total = total_qty // Config.NIFTY_LOT_SIZE
                    lots_to_sell = max(1, int(lots_total * book_ratio))
                    qty_to_sell = lots_to_sell * Config.NIFTY_LOT_SIZE
                    remaining_sl_qty = total_qty - qty_to_sell
                    
                    logger.info(f"⏳ Updating Broker SL for ROI Stop: Reducing quantity to {remaining_sl_qty}...")
                    sl_updated_ok = self._apply_sl_update(strategy_name, active_position, new_sl, stage=3.5, override_qty=remaining_sl_qty)
                    
                    if not sl_updated_ok:
                        logger.error(f"❌ Failed to update Stop-Loss price/quantity on broker for ROI Stop. Aborting partial booking.")
                    else:
                        logger.info(f"💰 PARTIAL BOOKING (ROI Stop): Securing {lots_to_sell} lots. Letting the rest RUN.")
                        oid = self.order_manager.place_smart_limit(symbol, token, qty_to_sell, ltp, "SELL", strategy_name=strategy_name)
                        if oid:
                            pnl_booked = (ltp - entry_price) * qty_to_sell
                            trade_repo.reduce_position(active_position['id'], qty_to_sell, ltp, pnl_booked, "STAGE_3_5_PARTIAL")
                            active_position['qty'] = total_qty - qty_to_sell
                        else:
                            # Rollback SL quantity to original if booking failed/cancelled
                            sl_oid = active_position.get('sl_order_id')
                            if sl_oid and not self.order_manager.dry_run:
                                logger.warning(f"⚠️ Partial booking order failed/cancelled. Restoring Broker SL Order {sl_oid} quantity to {total_qty}...")
                                self.order_manager.modify_sl_order(
                                    sl_oid, active_position['sl_price'], symbol, token, total_qty
                                )
                else:
                    self._apply_sl_update(strategy_name, active_position, new_sl, stage=3.5)
                current_stage = 3.5

        if current_stage >= 3:
            now = time.time()
            if now - self._last_ema_check > 10: 
                self._last_ema_check = now
                # Use 1m 21-EMA as the anchor
                ema_val = self._get_1m_ema(token, period=21)
                if ema_val > 0:
                    # Anchor at EMA but ensure 1.2 ATR of room from LTP
                    ema_sl = round(ema_val - (0.3 * atr), 1)
                    hard_room_sl = round(ltp - (1.8 * atr), 1) # Fallback room
                    new_sl = max(ema_sl, hard_room_sl)
                    
                    if new_sl > current_sl and new_sl < ltp - (0.5 * atr):
                        self._apply_sl_update(strategy_name, active_position, new_sl)
            
            # --- STAGE 4: MOONSHOT MODE ---
            if current_stage < 4 and points_up >= threshold_4_0:
                logger.info(f"🚀 STAGE 4: MOONSHOT MODE! Activating Tighter 1m 9-EMA Trail.")
                active_position['ladder_stage'] = 4
                current_stage = 4

        # Target Price Exit Check (Absolute Take-Profit)
        target_price = active_position.get('target_price', 0.0)
        if target_price > 0 and ltp >= target_price:
            logger.info(f"🎯 Take-Profit Target Hit! LTP: {ltp} >= Target: {target_price}")
            return True, "TARGET"

        # Exit Check
        if ltp <= active_position.get('sl_price', 0):
            logger.info(f"🛑 Laddered SL Hit! LTP: {ltp} <= SL: {active_position['sl_price']}")
            return True, "MARKET"

        return False, None

    def _get_1m_ema(self, token, period=21):
        try:
            df = self.data_fetcher.fetch_latest_candles(token, interval="ONE_MINUTE", exchange="NFO")
            if df is not None and not df.empty:
                # Dynamic EMA based on period (9 or 21)
                ema = df['close'].ewm(span=period, adjust=False).mean()
                return ema.iloc[-1]
        except Exception as e:
            logger.error(f"Error fetching 1m EMA{period}: {e}")
        return 0

    def _apply_sl_update(self, strategy_name, active_position, new_sl, stage=None, override_qty=None):
        active_position['sl_price'] = new_sl
        if stage is not None:
            active_position['ladder_stage'] = stage
        
        # Update DB
        if 'id' in active_position:
            trade_repo.update_sl(active_position['id'], new_sl)
            if stage is not None:
                 trade_repo.collection.update_one({"id": active_position['id']}, {"$set": {"ladder_stage": stage}})

        # Update Broker SL
        sl_oid = active_position.get('sl_order_id')
        if sl_oid and not self.order_manager.dry_run: # Modification permitted in Live mode
             # Note: In momentum_strategy.py, it checked not self.dry_run
             # We'll use Config.LIVE_TRADE_ENABLED as a proxy or just rely on order_manager
             symbol = active_position['symbol']
             token = active_position['token']
             qty = override_qty if override_qty is not None else active_position['qty']
             
             # Fetch current LTP to see if we are already at the new SL
             current_ltp = self.data_fetcher.get_ltp(token, exchange="NFO")
             if current_ltp and current_ltp <= new_sl:
                 logger.warning(f"⚠️ DANGER: Modifying SL to {new_sl} while LTP is {current_ltp}! This will trigger immediate exit.")
             
             return self.order_manager.modify_sl_order(sl_oid, new_sl, symbol, token, qty)
        
        return True

    def _get_current_time(self):
        return datetime.datetime.now()
        
    def _get_current_adx(self):
        try:
            from bot.config.instruments import get_instrument
            import pandas as pd
            instr = get_instrument(Config.ACTIVE_SYMBOL)
            spot_tok = instr.analysis_token
            df5 = self.data_fetcher.fetch_latest_candles(spot_tok, interval="FIVE_MINUTE", exchange=instr.exchange)
            if df5 is not None and len(df5) >= 20:
                df = df5.copy()
                period = 14
                df['up'] = df['high'] - df['high'].shift(1)
                df['dn'] = df['low'].shift(1) - df['low']
                df['pdm'] = df['up'].where((df['up'] > df['dn']) & (df['up'] > 0), 0)
                df['ndm'] = df['dn'].where((df['dn'] > df['up']) & (df['dn'] > 0), 0)
                df['tr1'] = df['high'] - df['low']
                df['tr2'] = abs(df['high'] - df['close'].shift(1))
                df['tr3'] = abs(df['low'] - df['close'].shift(1))
                df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
                df['atr'] = df['tr'].ewm(alpha=1/period, adjust=False).mean()
                df['pdi'] = 100 * (df['pdm'].ewm(alpha=1/period, adjust=False).mean() / df['atr'])
                df['ndi'] = 100 * (df['ndm'].ewm(alpha=1/period, adjust=False).mean() / df['atr'])
                df['dx'] = 100 * abs(df['pdi'] - df['ndi']) / (df['pdi'] + df['ndi'])
                adx_series = df['dx'].ewm(alpha=1/period, adjust=False).mean()
                return adx_series.iloc[-1]
        except Exception as e:
            logger.warning(f"Error calculating ADX in trailing manager: {e}")
        return 0.0

    def is_killswitch_time(self):
        """
        Regardless of profit, if the time is 15:10 (3:10 PM), execute a MARKET exit.
        """
        now = self._get_current_time().time()
        if now >= datetime.time(15, 10):
            return True
        return False
