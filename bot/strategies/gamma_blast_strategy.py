import time
import datetime
import pandas as pd
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.core.order_manager import OrderManager
from bot.core.oi_analyzer import OIAnalyzer
from bot.utils.logger import logger

class GammaBlastStrategy:
    """
    Gamma Blast (OTM Momentum) Strategy.
    Designed for "Hero-to-Zero" exponential returns during parabolic trends.
    Trigger: ADX > 35 + Strong OI Bias + VWAP/EMA Confluence.
    Target: 3:1 or 5:1 Risk/Reward using high-leverage OTM options.
    """
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.data_fetcher = DataFetcher(self.api)
        self.order_manager = OrderManager(self.api, dry_run=self.dry_run)
        self.oi_analyzer = OIAnalyzer(self.api, self.token_loader)
        self.running = True

    def execute(self, expiry, action="BUY"):
        logger.info(f"🚀 --- GAMMA BLAST OTM STRATEGY ACTIVATED ({expiry}) ---")
        
        mode = "PAPER" if self.dry_run else "LIVE"
        active_trade = trade_repo.get_active_trade(mode=mode, strategy="GAMMA_BLAST")

        # 1.5 Global Safety Guards
        if not self.gatekeeper.is_market_open():
            logger.warning("Gamma Blast: 🛑 Execution Aborted - Market is Closed.")
            return
        if self.gatekeeper.is_blackout_period():
            logger.info("Gamma Blast: ⏸️ Execution Suspended - Mid-day Blackout.")
            return
        if not self.gatekeeper.check_max_daily_loss(0.0):
            logger.critical("Gamma Blast: 🛑 Execution Blocked - Max Daily Loss reached.")
            return

        if active_trade:
            logger.info(f">>> [Resumption] Found Open Trade: {active_trade['symbol']}")
            self.monitor_position(
                active_trade['symbol'], 
                active_trade['token'], 
                active_trade['qty'],
                active_trade['sl_price'],
                active_trade['entry_price'],
                active_trade['id'],
                active_trade.get('sl_order_id'),
                active_trade.get('leg')
            )
            return

        # 2. Market analysis & Final Confirmation 
        # (Though DecisionEngine already checked, we double check local indicators)
        df = self.data_fetcher.fetch_latest_candles("99926000", interval="FIVE_MINUTE")
        if df is None or len(df) < 20:
            logger.error("Gamma Blast: Insufficient data for precision entry.")
            return

        ltp = df.iloc[-1]['close']
        adx = self.calculate_adx(df).iloc[-1]
        
        if adx < 30: # Threshold for Parabolic check
            logger.warning(f"Gamma Blast: Trend strength (ADX: {adx:.1f}) below threshold (30). Aborting.")
            return

        # 3. Determine Leg (Trend Direction)
        ema9 = df['close'].ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = df['close'].ewm(span=21, adjust=False).mean().iloc[-1]
        
        leg = "CE" if ema9 > ema21 else "PE"
        
        # 4. Strike Selection (OTM Logic)
        # Nifty moves in 50pt increments. OTM is 50-100 pts away.
        strike = round(ltp / 50) * 50
        if leg == "CE":
            strike += 50 # Buy 1 strikes OTM
        else:
            strike -= 50 # Buy 1 strikes OTM
            
        logger.info(f"🎯 Analysis: ADX={adx:.1f} | Leg={leg} | Selected OTM Strike={strike}")
        
        # 5. Position Sizing (Dedicated small capital for High Risk)
        # Use only 50% of standard compounded slots for "Hero" trade
        lots = max(1, int(self.gatekeeper.get_compounded_lots(margin_per_lot=5000) * 0.5))
        qty = lots * Config.NIFTY_LOT_SIZE
        
        self.place_entry(expiry, strike, leg, qty)

    def place_entry(self, expiry, strike, leg, qty):
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg)
        if not token:
            logger.error(f"Gamma Blast: Token not found for {strike} {leg}")
            return

        # Fetch Option LTP for early record and price estimate
        quote_ltp = self.data_fetcher.get_ltp(token, exchange="NFO") or 50.0
        
        # Place Smart-Limit Order with 5% buffer from LTP
        # This replaces raw LIMIT/MARKET to reduce slippage
        limit_price = round(quote_ltp * 1.05, 1)
        
        logger.info(f">>> [Trade] Entering {symbol} (Qty: {qty}) via Smart-Limit @ ₹{limit_price}")
        
        oid = self.order_manager.place_smart_limit(symbol, token, qty, limit_price, transaction_type="BUY")
        if not oid: return

        # 1. Early Record (Visibility in UI)
        # We save with status "PLACED" (if entry_price=0 or we can pass status explicitly if we update save_trade further, 
        # but my current update uses status="PLACED" if entry_price is passed as 0 or we use quote_ltp and it stays OPEN)
        # Let's use status="PLACED" by passing entry_price=0.0 initially.
        mode = "PAPER" if self.dry_run else "LIVE"
        trade_id = trade_repo.save_trade(symbol, token, leg, qty, 0.0, 0.0, mode=mode, strategy="GAMMA_BLAST")

        # 2. Wait for fill
        fill_result = self.wait_for_fill(oid)
        if fill_result['status'] != 'FILLED':
            logger.warning(f"Gamma Blast: Entry failed or timed out. Status: {fill_result['status']}")
            if fill_result['status'] == 'TIMEOUT':
                self.order_manager.cancel_order(oid, variety="NORMAL")
            return

        fill_price = fill_result['price']
        
        # 3. Update Trade with Actual Fill & Mark OPEN
        sl_price = round(fill_price * 0.80, 1)
        # 2. Update Entry (with Slippage Tracking)
        if trade_id:
            trade_repo.update_entry_price(trade_id, fill_price, expected_price=quote_ltp)
            trade_repo.update_sl(trade_id, sl_price)
        
        # Place Broker SL
        sl_oid = self.order_manager.place_sl_order(symbol, token, qty, sl_price, leg)
        
        self.monitor_position(symbol, token, qty, sl_price, fill_price, trade_id, sl_oid, leg)

    def monitor_position(self, symbol, token, qty, sl, entry_price, trade_id, sl_oid, leg):
        logger.info(f"Gamma Blast: Monitoring. Entry: {entry_price} | SL: {sl}")
        
        # Hero-to-Zero logic: We look for 3x ATR target or 3:1 RR
        target = round(entry_price + (abs(entry_price - sl) * 3), 1)
        breakeven_hit = False
        
        while self.running:
            try:
                time.sleep(0.5)
                ltp = self.data_fetcher.get_ltp(token, exchange="NFO")
                if not ltp: continue

                # Global Kill Switch
                unrealized_pnl = (ltp - entry_price) * qty
                if not self.gatekeeper.check_max_daily_loss(unrealized_pnl):
                    logger.critical("🛑 EMERGENCY: Account Daily Loss Limit Breach in Gamma Blast!")
                    self.exit_market(token, symbol, qty, "MAX_DAILY_LOSS", trade_id, sl_oid)
                    break

                # --- SENIOR TRADER ENHANCEMENT: Trend-Fade Check ---
                # Use centralized MarketService to avoid API rate limits
                from backend.market_service import market_service
                analysis = market_service.get_market_data().get('analysis', {})
                curr_adx = analysis.get('adx', 0)
                
                if curr_adx > 0 and curr_adx < 25:
                    logger.info(f"Gamma Blast: ⚠️ Trend Fading (ADX: {curr_adx:.1f}). Booking profits/cutting loss.")
                    if self.exit_market(token, symbol, qty, "TREND_FADE", trade_id, sl_oid):
                        break
                    else: continue

                # Trailing / Breakeven (Fast)
                if not breakeven_hit and ltp >= entry_price + abs(entry_price - sl):
                    logger.info(f"Gamma Blast: 🛡️ 1:1 Profit reached. Moving SL to Breakeven.")
                    if sl_oid and not self.dry_run:
                        self.order_manager.modify_sl_order(sl_oid, entry_price, symbol, token, qty)
                    sl = entry_price
                    breakeven_hit = True
                    trade_repo.update_sl(trade_id, sl)

                # Target Hit (The "Blast")
                if ltp >= target:
                    logger.info(f"💎 GAMMA BLAST HIT! Target {target} reached. Liquidating.")
                    if self.exit_market(token, symbol, qty, "TARGET", trade_id, sl_oid):
                        break
                    else: continue

                # SL Hit
                if ltp <= sl:
                    logger.info(f"Gamma Blast: SL Hit at {ltp}.")
                    trade_repo.close_trade(trade_id=trade_id, exit_price=ltp, exit_reason="SL_HIT")
                    # Self-cancel broker SL if we exit locally
                    if sl_oid: self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
                    break

                # Time Exit
                if datetime.datetime.now().time() >= datetime.time(15, 10):
                    if self.exit_market(token, symbol, qty, "TIME", trade_id, sl_oid):
                        break
                    else: continue

            except Exception as e:
                logger.error(f"Gamma Blast Monitor Error: {e}")
                time.sleep(2)

    def exit_market(self, token, symbol, qty, reason, trade_id, sl_oid):
        """Institutional Exit: Use buffered LIMIT instead of MARKET for OTM safety."""
        try:
            if sl_oid: self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
            
            ltp = self.data_fetcher.get_ltp(token) or 0
            # Set limit 2% below LTP to act as market but with a 'flash-crash' floor
            # 10% was too wide and triggered AB1007 LPP. 2% is the exchange sweet spot.
            limit_price = round(ltp * 0.98, 1) if ltp > 0 else 0
            
            orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", 
                "ordertype": "LIMIT" if limit_price > 0 else "MARKET",
                "price": limit_price,
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
            oid = self.order_manager.place_order(orderparams)
            
            # Use WebSocket to wait for final exit price for the ledger
            if oid:
                fill = self.wait_for_fill(oid)
                # Ensure the exit limit actually filled, so we don't abandon the order
                if fill['status'] == 'TIMEOUT':
                    logger.warning(f"Gamma Blast: Exit order {oid} TIMEOUT. Canceling and retrying monitor mode.")
                    self.order_manager.cancel_order(oid, variety="NORMAL")
                    return False
                
                exit_price = fill.get('price', ltp)
                trade_repo.close_trade(trade_id=trade_id, exit_price=exit_price, exit_reason=reason)
                return True
            else:
                trade_repo.close_trade(trade_id=trade_id, exit_reason=reason)
                return True

        except Exception as e:
            logger.error(f"Gamma Blast Exit Failed: {e}")

    def wait_for_fill(self, order_id):
        """Uses WebSocket Order Feed for sub-second fill detection."""
        if self.dry_run: return {'status': 'FILLED', 'price': 50.0}
        
        from bot.core.order_feed import order_feed
        logger.info(f">>> [Gamma Blast] Waiting for WebSocket Fill Event ({order_id})...")
        
        result = order_feed.wait_for_fill(order_id, timeout=10)
        
        if result['status'] == 'TIMEOUT':
             logger.warning(f"⚠️ Order {order_id} fill TIMEOUT via WebSocket.")
        
        return result

    def calculate_adx(self, df, period=14):
        # Local ADX calc or use analysis file
        try:
            df = df.copy()
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
            return df['dx'].ewm(alpha=1/period, adjust=False).mean()
        except: return pd.Series([0]*len(df))

    def stop(self):
        self.running = False
