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
        current_stage = active_position.get('ladder_stage', 0)

        # Stage 0.5: Breakeven Shield (No-Loss Mode)
        # As soon as we hit ₹1,000 profit, move SL to cost + 5pts (generous buffer for taxes + wiggles)
        if current_stage < 0.5 and unrealized_pnl >= 1000:
            new_sl = entry_price + 5
            if new_sl > current_sl:
                logger.info(f"🛡️ Stage 0.5 Reached: Breakeven Shield Active ({symbol}) | SL: {new_sl}")
                self._apply_sl_update(strategy_name, active_position, new_sl, stage=0.5)
                current_stage = 0.5

        # Stage 1: The ₹2,000 Floor
        if current_stage < 1 and unrealized_pnl >= 2000:
            new_sl = entry_price + 18
            if new_sl > current_sl:
                logger.info(f"🛡️ Stage 1 Reached: Floor Locked ({symbol}) | SL: {new_sl}")
                self._apply_sl_update(strategy_name, active_position, new_sl, stage=1)
                current_stage = 1

        # Stage 2: The Buffer
        if current_stage < 2 and unrealized_pnl >= 3200:
            new_sl = entry_price + 35
            if new_sl > current_sl:
                logger.info(f"📈 Stage 2 Reached: Buffer Set ({symbol}) | SL: {new_sl}")
                self._apply_sl_update(strategy_name, active_position, new_sl, stage=2)
                current_stage = 2

        # Stage 3: The 3R Hunter (1m 21-EMA Trail)
        if current_stage < 3 and unrealized_pnl >= 4200:
            logger.info(f"🏃 Stage 3 Reached: Runner Mode (1m 21-EMA Trail) for {symbol}")
            active_position['ladder_stage'] = 3
            current_stage = 3

        if current_stage == 3:
            # Check 1m 21-EMA Trail
            now = time.time()
            if now - self._last_ema_check > 10: # Check every 10s
                self._last_ema_check = now
                ema21_1m = self._get_1m_ema21(token)
                if ema21_1m > 0:
                    # Trailing stop at EMA21
                    new_sl = round(ema21_1m, 1)
                    if new_sl > current_sl:
                        self._apply_sl_update(strategy_name, active_position, new_sl)
            
            # --- STAGE 4: MOONSHOT MODE (The X-Factor) ---
            # If profit is huge, sell 75% of lots and let 1 lot run for "100x" gains.
            if current_stage < 4 and unrealized_pnl >= 5000:
                total_qty = active_position.get('qty', 0)
                
                if total_qty > Config.NIFTY_LOT_SIZE:
                    total_lots = total_qty // Config.NIFTY_LOT_SIZE
                    lots_to_sell = max(1, int(total_lots * 0.75))
                    qty_to_sell = lots_to_sell * Config.NIFTY_LOT_SIZE
                    
                    logger.info(f"🚀 STAGE 4: MOONSHOT MODE! Selling {lots_to_sell} lots. Keeping 1 lot Runner.")
                    
                    # Execute partial sell
                    oid = self.order_manager.place_smart_limit(
                        symbol, token, qty_to_sell, ltp, "SELL",
                        strategy_name=strategy_name
                    )
                    
                    if oid:
                        # Update DB and Active Position
                        pnl_booked = (ltp - entry_price) * qty_to_sell
                        trade_repo.reduce_position(
                            active_position['id'], qty_to_sell, ltp, pnl_booked, "MOONSHOT_PARTIAL"
                        )
                        active_position['qty'] = total_qty - qty_to_sell
                else:
                    logger.info(f"🚀 STAGE 4: MOONSHOT MODE! (1-Lot Position) Moving SL to Safe Zone.")

                # ALWAYS update SL and Stage for the remaining runner (or the original 1 lot)
                active_position['ladder_stage'] = 4
                new_sl = entry_price + 50 # Secure 50 points (₹3,250) on the runner
                self._apply_sl_update(strategy_name, active_position, new_sl)

        # Exit Check
        if ltp <= active_position.get('sl_price', 0):
            logger.info(f"🛑 Laddered SL Hit! LTP: {ltp} <= SL: {active_position['sl_price']}")
            # Smart-Exit: If Stage 1 has been reached, use LIMIT order
            exit_type = "LIMIT" if current_stage >= 1 else "MARKET"
            return True, exit_type

        return False, None

    def _get_1m_ema21(self, token):
        try:
            df = self.data_fetcher.fetch_latest_candles(token, interval="ONE_MINUTE")
            if df is not None and not df.empty:
                # Simple EMA21 calculation for wide breathing room
                ema21 = df['close'].ewm(span=21, adjust=False).mean()
                return ema21.iloc[-1]
        except Exception as e:
            logger.error(f"Error fetching 1m EMA21: {e}")
        return 0

    def _apply_sl_update(self, strategy_name, active_position, new_sl, stage=None):
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
             qty = active_position['qty']
             
             # Fetch current LTP to see if we are already at the new SL
             current_ltp = self.data_fetcher.get_ltp(token, exchange="NFO")
             if current_ltp and current_ltp <= new_sl:
                 logger.warning(f"⚠️ DANGER: Modifying SL to {new_sl} while LTP is {current_ltp}! This will trigger immediate exit.")
             
             self.order_manager.modify_sl_order(sl_oid, new_sl, symbol, token, qty)



    def is_killswitch_time(self):
        """
        Regardless of profit, if the time is 15:10 (3:10 PM), execute a MARKET exit.
        """
        now = datetime.datetime.now().time()
        if now >= datetime.time(15, 10):
            return True
        return False
