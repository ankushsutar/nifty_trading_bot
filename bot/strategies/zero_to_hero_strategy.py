import time
import datetime
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.core.order_manager import OrderManager
from bot.core.regime_classifier import RegimeClassifier
from bot.utils.logger import logger
from bot.utils.notifier import notifier

class ZeroToHeroStrategy:
    """
    Zero-To-Hero (Z2H) Wild Card Strategy 🛸🚀
    Deep OTM Tail-Risk Harvester. Runs in parallel to core portfolio.
    Uses absolute minimal capital (₹2-3k) to buy cheap ₹5-₹10 options
    during super-high confluence periods.
    No aggressive trailing. Holds for explosive 5x-10x breakouts.
    """

    STRATEGY_NAME = "ZERO_TO_HERO"
    MIN_TARGET_PREMIUM = 5.0
    MAX_TARGET_PREMIUM = 15.0
    MAX_ABSOLUTE_RISK = 3000.0  # Absolute rupee limit for this wild card ticket.
    FIXED_STOP_LOSS_PCT = 0.60  # Hard safety floor at 60% loss (keeps final 40% of capital).

    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(api, dry_run=dry_run)
        self.order_manager = OrderManager(api, dry_run=dry_run)
        self.data_fetcher = DataFetcher(api)
        self.classifier = RegimeClassifier()
        self.running = True
        
        self.active_position = None 

    def stop(self):
        self.running = False
        if self.active_position:
             logger.warning("Z2H: Stop signal received. Active wild card remains open. Closing logic suspended for manual handling.")

    def sync_state(self):
        """Recover from server crash"""
        mode = "PAPER" if self.dry_run else "LIVE"
        try:
            open_trades = trade_repo.get_open_trades(mode=mode, strategy=self.STRATEGY_NAME)
            if open_trades:
                t = open_trades[0]
                self.active_position = {
                    'id': t['id'],
                    'symbol': t['symbol'],
                    'token': t['token'],
                    'qty': t['qty'],
                    'entry_price': t['entry_price'],
                    'sl_oid': t.get('sl_order_id')
                }
                logger.info(f"♻️ [Z2H] Recovery active for {t['symbol']}")
        except Exception as e:
            logger.error(f"Z2H recovery fail: {e}")

    def _find_deep_otm_contract(self, leg, ltp, expiry):
        """
        Uses Delta-based strike selection to find a deep OTM contract (Target Delta: 0.20).
        """
        target_delta = 0.20
        from bot.config.settings import Config
        from bot.config.instruments import get_instrument
        active_sym = Config.ACTIVE_SYMBOL
        instr = get_instrument(active_sym)
        strike_diff = instr.strike_step

        try:
            from backend.market_service import market_service
            vix = market_service.get_market_data().get('vix', 15.0)
            if vix <= 0: vix = 15.0
            
            from bot.utils.greeks import select_strike_by_delta
            strike, token, symbol = select_strike_by_delta(self.token_loader, ltp, expiry, vix, leg, target_delta, active_sym)
            
            if strike and token:
                prem = self.data_fetcher.get_ltp(token, "NFO")
                if not prem:
                    prem = 10.0
                logger.info(f"🎯 Found Z2H Contract by Delta: {symbol} @ ₹{prem} (Strike: {strike})")
                return token, symbol, prem, strike
        except Exception as e:
            logger.warning(f"Z2H Delta selection error: {e}")

        logger.warning("⚠️ Z2H Delta-based strike selection failed or bypassed. Falling back to price scanner.")
        atm_strike = round(ltp / strike_diff) * strike_diff
        direction = 1 if leg == "CE" else -1
        
        # Start scanning at 4 strikes OTM (Deep) up to 12 strikes (Extremely Deep)
        for depth in range(3, 12):
            target_strike = atm_strike + (depth * strike_diff * direction)
            token, symbol = self.token_loader.get_token(active_sym, expiry, target_strike, leg, instrument_type=instr.instrument_type, exchange=instr.option_exchange)
            if not token: continue
            
            prem = self.data_fetcher.get_ltp(token, "NFO")
            if prem and self.MIN_TARGET_PREMIUM <= prem <= self.MAX_TARGET_PREMIUM:
                logger.info(f"🎯 Found Z2H Contract: {symbol} @ ₹{prem} (Depth: {depth})")
                return token, symbol, prem, target_strike
            
            # If premium is too expensive, go deeper next iteration.
            # If it is already cheaper than MIN, we've gone too deep! Walk back one step.
            if prem and prem < self.MIN_TARGET_PREMIUM:
                # Walk back to previous depth if valid
                prev_strike = atm_strike + ((depth - 1) * strike_diff * direction)
                t_prev, s_prev = self.token_loader.get_token(active_sym, expiry, prev_strike, leg, instrument_type=instr.instrument_type, exchange=instr.option_exchange)
                p_prev = self.data_fetcher.get_ltp(t_prev, "NFO")
                logger.warning(f"Z2H Warning: Went too deep (₹{prem}). Retracting one step to {s_prev} @ ₹{p_prev}")
                return t_prev, s_prev, p_prev, prev_strike
                
        return None, None, None, None


    def execute(self, expiry, action="BUY"):
        logger.info(f"⚡ --- ZERO-TO-HERO WILDCARD INITIATED ({expiry}) --- 🚀")
        self.sync_state()
        
        while self.running:
            # Exit check
            if not self.gatekeeper.is_market_open(): break
            
            if self.active_position:
                self._monitor_wildcard()
                break # Monitor loop handles until closure
            
            # 1. Get Nifty Core Trigger
            from backend.market_service import market_service
            md = market_service.get_market_data()
            nifty_ltp = md.get('nifty', 0)
            analysis = md.get('analysis', {})
            adx = analysis.get('adx', 0)
            
            if nifty_ltp == 0: 
                time.sleep(10)
                continue
            
            # Z2H specific ADX Gate - must be blazing
            if adx < 45:
                logger.info(f"Z2H Sleep: ADX {adx:.1f} too low for explosive lotto. Need 45+.")
                time.sleep(60)
                continue
            
            # 2. Direction logic
            # Simple EMA cloud alignment
            ema9 = analysis.get('ema9')
            ema21 = analysis.get('ema21')
            
            if not ema9 or not ema21:
                time.sleep(30)
                continue
                
            leg = "CE" if nifty_ltp > ema9 > ema21 else ("PE" if nifty_ltp < ema9 < ema21 else None)
            if not leg:
                time.sleep(30)
                continue
                
            # 3. Locate the cheap rocket
            token, symbol, prem, strike = self._find_deep_otm_contract(leg, nifty_ltp, expiry)
            if not token or not prem:
                logger.warning(f"Z2H: Could not locate a liquid contract between ₹5 and ₹15.")
                time.sleep(60)
                continue
            
            # 4. Size fixed at risk limit
            # Max ₹3000 = roughly 2-3 lots at ₹10 premium
            max_allowed_qty = (self.MAX_ABSOLUTE_RISK / prem)
            lots = int(max_allowed_qty // Config.NIFTY_LOT_SIZE)
            qty = int(max(1, lots) * Config.NIFTY_LOT_SIZE)
            
            total_deployed = qty * prem
            logger.info(f"🛸 Deploying Wild Card: Buying {qty} {symbol} @ ₹{prem}. Est Cost: ₹{total_deployed:.2f}")
            
            # Execution
            mode = "PAPER" if self.dry_run else "LIVE"
            sl_init = round(prem * (1 - self.FIXED_STOP_LOSS_PCT), 2)
            
            trade_id = trade_repo.save_trade(
                symbol=symbol, token=token, leg=leg, qty=qty, 
                entry_price=prem, sl_price=sl_init, mode=mode, strategy=self.STRATEGY_NAME
            )
            
            order_id = self.order_manager.place_smart_limit(
                symbol=symbol, token=token, qty=qty, initial_price=prem, 
                transaction_type="BUY", strategy_name=self.STRATEGY_NAME, mode=mode
            )
            if order_id:
                self.active_position = {
                    'id': trade_id, 'symbol': symbol, 'token': token, 'qty': qty, 'entry_price': prem
                }
                notifier.notify_trade_entry("ZERO_TO_HERO", symbol, "BUY", qty, prem, sl=sl_init, target="3x Jackpot (Scale 50% at +200%)")
                
                # Drop Emergency Broker-Side SL
                sl_id = self.order_manager.place_stoploss(symbol, token, qty, sl_init, self.STRATEGY_NAME)
                self.active_position['sl_oid'] = sl_id
                trade_repo.update_sl_order_id(trade_id, sl_id)
            else:
                logger.error("Z2H failed entry execution.")
                trade_repo.collection.delete_one({"id": trade_id})
                time.sleep(60)
                
    def _monitor_wildcard(self):
        """Simplified monitor: No progressives. Sell half at +200%, Hold rest till end of day."""
        entry = self.active_position['entry_price']
        token = self.active_position['token']
        qty = self.active_position['qty']
        sym = self.active_position['symbol']
        tid = self.active_position['id']
        
        logger.info(f"🛸 WILDCARD MONITORING ACTIVE for {sym} @ ₹{entry}")
        
        half_booked = False
        
        while self.running:
            ltp = self.data_fetcher.get_ltp(token, "NFO")
            if not ltp:
                time.sleep(1)
                continue
                
            roi = ((ltp - entry) / entry) * 100
            
            # 1. Scale Out Half at 200% gain (3x value)
            if not half_booked and roi >= 200.0 and qty > Config.NIFTY_LOT_SIZE:
                 sell_qty = int((qty // 2) // Config.NIFTY_LOT_SIZE * Config.NIFTY_LOT_SIZE)
                 logger.info(f"🔥 JACKPOT PART 1: Wildcard hit 200% gain (₹{ltp}). Booking HALF.")
                 mode = "PAPER" if self.dry_run else "LIVE"
                 self.order_manager.place_smart_limit(
                     symbol=sym, token=token, qty=sell_qty, initial_price=ltp, 
                     transaction_type="SELL", strategy_name=self.STRATEGY_NAME, mode=mode
                 )
                 
                 segment_pnl = (ltp - entry) * sell_qty
                 trade_repo.reduce_position(tid, sell_qty, ltp, segment_pnl, "JACKPOT_SCALE")
                 
                 scale_msg = (
                     f"🔥 <b>SCALE OUT: ZERO_TO_HERO</b>\n"
                     f"Symbol: <code>{sym}</code> | Qty: {sell_qty}\n"
                     f"Exit Price: <b>₹{ltp:.2f}</b> | P&L: <b>₹{segment_pnl:.2f}</b> 💰"
                 )
                 notifier.send_message(scale_msg)
                 
                 qty = qty - sell_qty
                 self.active_position['qty'] = qty
                 half_booked = True
                 # Cancel old SL, let final runner have NO stop-loss! Ultimate freedom.
                 if self.active_position.get('sl_oid'):
                     self.order_manager.cancel_order(self.active_position['sl_oid'])
                     
            # 2. Disaster Stop Hit Check
            sl_price = entry * (1 - self.FIXED_STOP_LOSS_PCT)
            if ltp <= sl_price and not half_booked:
                 logger.warning(f"💀 Wildcard Hard Floor hit at ₹{ltp}. Cutting remaining.")
                 self.order_manager.place_market(sym, token, qty, "SELL", self.STRATEGY_NAME)
                 
                 pnl_val = (ltp - entry) * qty
                 trade_repo.close_trade(trade_id=tid, exit_price=ltp, pnl=pnl_val, exit_reason="HARD_FLOOR")
                 
                 notifier.notify_trade_exit("ZERO_TO_HERO", sym, pnl_val, "HARD_FLOOR")
                 break
                 
            # 3. Final Time Exit at 15:10
            now = datetime.datetime.now().time()
            if now >= datetime.time(15, 10):
                 logger.info(f"⏰ End of Day. Liquidating Wildcard final runner at ₹{ltp}")
                 self.order_manager.place_market(sym, token, qty, "SELL", self.STRATEGY_NAME)
                 
                 pnl_val = (ltp - entry) * qty
                 trade_repo.close_trade(trade_id=tid, exit_price=ltp, pnl=pnl_val, exit_reason="EOD_LIQUIDATION")
                 
                 notifier.notify_trade_exit("ZERO_TO_HERO", sym, pnl_val, "EOD_LIQUIDATION")
                 break
                 
            time.sleep(5) # Low intensity polling
