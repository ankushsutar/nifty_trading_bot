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
    MAX_ABSOLUTE_RISK = 1500.0  # Absolute rupee limit for this wild card ticket (capped for safety).
    FIXED_STOP_LOSS_PCT = 0.35  # Tightened stop loss at 35% loss (protects 65% of capital).

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

    def _is_contract_expired(self, symbol: str) -> bool:
        """
        Determines if a Zerodha NSE option symbol has already expired.

        Zerodha symbol format (NO year embedded):
          NIFTY26JUN23800PE  -> day=26, month=JUN, strike=23800, type=PE
          BANKNIFTY28JUN47000CE -> day=28, month=JUN, strike=47000, type=CE

        The year is inferred from today's date. If the candidate date with the current
        year is more than 180 days in the past, we assume it refers to next year's expiry.

        Returns True if expiry date < today. Returns False on any parse error (fail-safe).
        """
        import re
        _MONTHS = {
            'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4,
            'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8,
            'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
        }
        try:
            # Zerodha format: INDEX + DD + MMM + STRIKE + OPTTYPE  (no year in symbol)
            m = re.match(r'[A-Z]+?(\d{2})([A-Z]{3})\d+(CE|PE)', symbol.upper())
            if not m:
                return False

            day = int(m.group(1))
            mon = _MONTHS.get(m.group(2))
            if not mon:
                return False

            today = datetime.date.today()
            try:
                expiry = datetime.date(today.year, mon, day)
            except ValueError:
                return False  # Invalid calendar date

            # If result is more than 180 days in the past, it could be a next-year contract
            if (today - expiry).days > 180:
                try:
                    expiry = datetime.date(today.year + 1, mon, day)
                except ValueError:
                    pass

            return expiry < today
        except Exception as e:
            logger.warning(f"[Z2H] Expiry parse error for '{symbol}': {e}")
            return False  # Assume not expired on any error


    def sync_state(self):
        """Recover from server crash. Checks for past-expiry contracts before resuming."""
        mode = "PAPER" if self.dry_run else "LIVE"
        try:
            open_trades = trade_repo.get_open_trades(mode=mode, strategy=self.STRATEGY_NAME)
            if open_trades:
                t = open_trades[0]
                sym = t['symbol']

                # --- Expiry Guard: never resume monitoring an expired contract ---
                if self._is_contract_expired(sym):
                    logger.warning(
                        f"⚠️ [Z2H] Recovered trade {t['id']} ({sym}) is from a past expiry. "
                        f"Closing cleanly in DB — no monitoring will resume."
                    )
                    trade_repo.close_trade(
                        trade_id=t['id'],
                        exit_price=float(t.get('entry_price', 0)),
                        pnl=0.0,
                        exit_reason="EXPIRED_CONTRACT_ON_RECOVERY"
                    )
                    return

                self.active_position = {
                    'id': t['id'],
                    'symbol': sym,
                    'token': t['token'],
                    'qty': t['qty'],
                    'entry_price': t['entry_price'],
                    'sl_oid': t.get('sl_order_id'),
                    'partially_booked': t.get('partially_booked', False)
                }
                logger.info(f"♻️ [Z2H] Recovery active for {sym}")
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

    def _get_expiry_date_from_symbol(self, symbol: str):
        """Extracts expiry date from Zerodha symbol format."""
        import re
        m = re.match(r'[A-Z]+?(\d{2})([A-Z]{3})\d+(CE|PE)', symbol.upper())
        if not m:
            return None
        try:
            day = int(m.group(1))
            mon_str = m.group(2)
            _MONTHS = {
                'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4,
                'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8,
                'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
            }
            mon = _MONTHS.get(mon_str)
            if not mon: return None
            today = datetime.date.today()
            expiry = datetime.date(today.year, mon, day)
            if (today - expiry).days > 180:
                expiry = datetime.date(today.year + 1, mon, day)
            return expiry
        except:
            return None

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
            
            # Z2H specific ADX Gate - must be blazing (loosened during the Expiry Lotto Window)
            now_t = datetime.datetime.now().time()
            today_str = datetime.datetime.now().strftime("%d%b%Y").upper()
            is_expiry_day = (expiry == today_str)
            is_expiry_lotto_window = is_expiry_day and (datetime.time(14, 30) <= now_t <= datetime.time(15, 10))
            
            required_adx = 15.0 if is_expiry_lotto_window else 45.0
            if adx < required_adx:
                logger.info(f"Z2H Sleep: ADX {adx:.1f} too low for explosive lotto. Need {required_adx}+.")
                time.sleep(60)
                continue
            
            # 2. Direction logic
            # Simple EMA cloud alignment
            ema9 = analysis.get('ema9')
            ema21 = analysis.get('ema21')
            
            if not ema9 or not ema21:
                logger.info("Z2H Waiting: EMA9 or EMA21 indicators missing from market analysis.")
                time.sleep(30)
                continue
                
            leg = "CE" if nifty_ltp > ema9 > ema21 else ("PE" if nifty_ltp < ema9 < ema21 else None)
            if not leg:
                logger.info(f"Z2H Waiting: Nifty LTP ({nifty_ltp:.2f}) not aligned with EMA9 ({ema9:.2f}) and EMA21 ({ema21:.2f}) for trend direction.")
                time.sleep(30)
                continue

            # Sentiment/PCR alignment check to prevent counter-sentiment trading
            oi_data = md.get('oi_data', {})
            pcr = oi_data.get('pcr') if isinstance(oi_data, dict) else md.get('pcr', 1.0)
            if pcr is None:
                pcr = 1.0

            if leg == "PE" and pcr > 1.15:
                logger.info(f"Z2H Blocked: PE entry blocked because PCR ({pcr:.2f}) is bullish (> 1.15).")
                time.sleep(30)
                continue
            if leg == "CE" and pcr < 0.85:
                logger.info(f"Z2H Blocked: CE entry blocked because PCR ({pcr:.2f}) is bearish (< 0.85).")
                time.sleep(30)
                continue

                
            # 3. Locate the cheap rocket
            token, symbol, prem, strike = self._find_deep_otm_contract(leg, nifty_ltp, expiry)
            if not token or not prem:
                logger.warning(f"Z2H: Could not locate a liquid contract between ₹5 and ₹15.")
                time.sleep(60)
                continue
            
            # 4. Size capped at strict risk limit (Max 2 lots / ₹1,500 capital per wildcard ticket)
            current_capital = self.gatekeeper.get_current_capital()
            dynamic_risk_limit = min(self.MAX_ABSOLUTE_RISK, max(1000.0, current_capital * 0.03))
            max_allowed_qty = (dynamic_risk_limit / prem)
            raw_lots = int(max_allowed_qty // Config.NIFTY_LOT_SIZE)
            lots = min(2, max(1, raw_lots))  # Hard cap at max 2 lots for Zero-To-Hero
            qty = int(lots * Config.NIFTY_LOT_SIZE)
            
            total_deployed = qty * prem
            logger.info(f"🛸 Deploying Wild Card: Buying {qty} ({lots} lots) {symbol} @ ₹{prem}. Est Cost: ₹{total_deployed:.2f} (Risk Limit: ₹{dynamic_risk_limit:.2f})")
            
            # Dynamic Stop Loss based on VIX regime
            from backend.market_service import market_service
            vix = market_service.get_market_data().get('vix', 15.0)
            if vix < 12.0:
                sl_pct = 0.30  # Tight SL for quiet markets
            elif vix > 17.0:
                sl_pct = 0.45  # Extra room for high-volatility noise
            else:
                sl_pct = 0.35  # Standard SL
                
            logger.info(f"Z2H Dynamic Stop Loss: Selected {sl_pct*100:.1f}% based on VIX={vix:.2f}")

            # Execution
            mode = "PAPER" if self.dry_run else "LIVE"
            sl_init = round(round(prem * (1 - sl_pct) / 0.05) * 0.05, 2)
            
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
                sl_id = self.order_manager.place_sl_order(symbol, token, qty, sl_init, leg)
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
        
        half_booked = self.active_position.get('partially_booked', False)
        
        entry_time = time.time()
        
        while self.running:
            # --- Safety: abort immediately if the contract has expired ---
            if self._is_contract_expired(sym):
                logger.warning(f"⚠️ [Z2H] Contract {sym} expiry has passed. Closing trade #{tid}.")
                ltp_now = self.data_fetcher.get_ltp(token, "NFO") or entry
                pnl_val = (ltp_now - entry) * qty
                trade_repo.close_trade(trade_id=tid, exit_price=ltp_now, pnl=pnl_val, exit_reason="CONTRACT_EXPIRED")
                notifier.notify_trade_exit("ZERO_TO_HERO", sym, pnl_val, "CONTRACT_EXPIRED")
                self.active_position = None
                break

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
                     self.order_manager.cancel_order(self.active_position['sl_oid'], variety="STOPLOSS")
                     self.active_position['sl_oid'] = None

            # 1.5 Dynamic Stagnant Decay Exit (Theta Protection)
            from backend.market_service import market_service
            md = market_service.get_market_data()
            nifty_ltp = md.get('nifty', 0)
            analysis = md.get('analysis', {})
            ema9 = analysis.get('ema9')
            ema21 = analysis.get('ema21')
            vix_val = md.get('vix', 15.0)

            expiry_date = self._get_expiry_date_from_symbol(sym)
            is_expiry_day = (expiry_date == datetime.date.today())

            if is_expiry_day:
                stagnant_timeout = 25.0 if vix_val < 13.0 else 15.0
            else:
                stagnant_timeout = 45.0

            # Trend Cloud Check: extend timeout if trend is still in our favor
            is_trend_aligned = False
            if nifty_ltp > 0 and ema21 and ema21 > 0:
                leg = self.active_position.get('leg')
                if not leg:
                    leg = "CE" if sym.upper().endswith("CE") else ("PE" if sym.upper().endswith("PE") else None)
                
                if leg == "CE" and nifty_ltp >= ema21:
                    is_trend_aligned = True
                elif leg == "PE" and nifty_ltp <= ema21:
                    is_trend_aligned = True

            if is_trend_aligned:
                stagnant_timeout += 15.0

            elapsed_mins = (time.time() - entry_time) / 60.0
            if elapsed_mins >= stagnant_timeout and roi <= 5.0 and not half_booked:
                 logger.warning(
                     f"⏳ [Z2H] STAGNANT DECAY EXIT: Position open for {elapsed_mins:.1f}m (timeout: {stagnant_timeout}m) with ROI {roi:.1f}%. "
                     f"Exiting to prevent theta decay wipeout on Expiry afternoon."
                 )
                 sl_oid = self.active_position.get('sl_oid')
                 if sl_oid and not self.dry_run:
                     cancel_success = self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
                     if not cancel_success:
                         sl_status = self.order_manager.get_order_status(sl_oid)
                         if sl_status and sl_status.get('status') in ['FILLED', 'COMPLETE']:
                             logger.warning(f"🛡️ [Double-Exit Protection] Z2H SL Order {sl_oid} was already FILLED. Skipping market exit order.")
                             exit_price = sl_status.get('price', ltp)
                             pnl_val = (exit_price - entry) * qty
                             trade_repo.close_trade(trade_id=tid, exit_price=exit_price, pnl=pnl_val, exit_reason="SL_HIT")
                             notifier.notify_trade_exit("ZERO_TO_HERO", sym, pnl_val, "SL_HIT")
                             break
                 
                 exit_oid = self.order_manager.place_market(sym, token, qty, "SELL", self.STRATEGY_NAME)
                 if exit_oid:
                     trade_repo.update_exit_order_id(tid, exit_oid)
                 pnl_val = (ltp - entry) * qty
                 trade_repo.close_trade(trade_id=tid, exit_price=ltp, pnl=pnl_val, exit_reason="STAGNANT_THETA_EXIT")
                 notifier.notify_trade_exit("ZERO_TO_HERO", sym, pnl_val, "STAGNANT_THETA_EXIT")
                 break
                     
            # 2. Disaster Stop Hit Check
            if vix_val < 12.0:
                sl_pct_now = 0.30
            elif vix_val > 17.0:
                sl_pct_now = 0.45
            else:
                sl_pct_now = 0.35
            sl_price = round(round(entry * (1 - sl_pct_now) / 0.05) * 0.05, 2)
            if ltp <= sl_price and not half_booked:
                 logger.warning(f"💀 Wildcard Hard Floor hit at ₹{ltp} (SL Price: ₹{sl_price}). Cutting remaining.")
                 sl_oid = self.active_position.get('sl_oid')
                 if sl_oid and not self.dry_run:
                     logger.info(f"Cancelling pending stop-loss order {sl_oid} before hard floor liquidation")
                     cancel_success = self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
                     if not cancel_success:
                         sl_status = self.order_manager.get_order_status(sl_oid)
                         if sl_status and sl_status.get('status') in ['FILLED', 'COMPLETE']:
                             logger.warning(f"🛡️ [Double-Exit Protection] Z2H SL Order {sl_oid} was already FILLED. Skipping market exit order.")
                             exit_price = sl_status.get('price', ltp)
                             pnl_val = (exit_price - entry) * qty
                             trade_repo.close_trade(trade_id=tid, exit_price=exit_price, pnl=pnl_val, exit_reason="SL_HIT")
                             notifier.notify_trade_exit("ZERO_TO_HERO", sym, pnl_val, "SL_HIT")
                             break
                 
                 exit_oid = self.order_manager.place_market(sym, token, qty, "SELL", self.STRATEGY_NAME)
                 if exit_oid:
                     trade_repo.update_exit_order_id(tid, exit_oid)
                 
                 pnl_val = (ltp - entry) * qty
                 trade_repo.close_trade(trade_id=tid, exit_price=ltp, pnl=pnl_val, exit_reason="HARD_FLOOR")
                 
                 notifier.notify_trade_exit("ZERO_TO_HERO", sym, pnl_val, "HARD_FLOOR")
                 break
                 
            # 3. Final Time Exit at 15:10
            now = datetime.datetime.now().time()
            if now >= datetime.time(15, 10):
                 logger.info(f"⏰ End of Day. Liquidating Wildcard final runner at ₹{ltp}")
                 sl_oid = self.active_position.get('sl_oid')
                 if sl_oid and not self.dry_run:
                     logger.info(f"Cancelling pending stop-loss order {sl_oid} before EOD liquidation")
                     cancel_success = self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
                     if not cancel_success:
                         sl_status = self.order_manager.get_order_status(sl_oid)
                         if sl_status and sl_status.get('status') in ['FILLED', 'COMPLETE']:
                             logger.warning(f"🛡️ [Double-Exit Protection] Z2H SL Order {sl_oid} was already FILLED. Skipping market exit order.")
                             exit_price = sl_status.get('price', ltp)
                             pnl_val = (exit_price - entry) * qty
                             trade_repo.close_trade(trade_id=tid, exit_price=exit_price, pnl=pnl_val, exit_reason="SL_HIT")
                             notifier.notify_trade_exit("ZERO_TO_HERO", sym, pnl_val, "SL_HIT")
                             break
                 
                 exit_oid = self.order_manager.place_market(sym, token, qty, "SELL", self.STRATEGY_NAME)
                 if exit_oid:
                     trade_repo.update_exit_order_id(tid, exit_oid)
                 
                 pnl_val = (ltp - entry) * qty
                 trade_repo.close_trade(trade_id=tid, exit_price=ltp, pnl=pnl_val, exit_reason="EOD_LIQUIDATION")
                 
                 notifier.notify_trade_exit("ZERO_TO_HERO", sym, pnl_val, "EOD_LIQUIDATION")
                 break
                 
            time.sleep(5) # Low intensity polling
