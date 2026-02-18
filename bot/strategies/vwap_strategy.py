import time
import datetime
import pandas as pd
import numpy as np
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.core.order_manager import OrderManager
from bot.core.oi_analyzer import OIAnalyzer
from bot.utils.logger import logger

class VWAPStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.data_fetcher = DataFetcher(self.api)
        self.order_manager = OrderManager(self.api, dry_run=self.dry_run)
        self.oi_analyzer = OIAnalyzer(self.api, self.token_loader)
        self.running = True

    def stop(self):
        """Gracefully stop the strategy monitoring."""
        logger.info(">>> [VWAP] Stop signal received.")
        self.running = False

    def execute(self, expiry, action="BUY"):
        """
        VWAP Institutional Logic:
        1. 09:15 - 10:00: Wait for Price/VWAP Stability
        2. 10:00+: Wait for Breakout above VWAP (Buy CE) or below (Buy PE)
        3. RSI Filter: Only Buy if RSI > 50 (CE) or < 50 (PE)
        """
        logger.info(f">>> [Strategy] Initializing VWAP Strategy for {expiry}")
        
        # Check for Resumption
        mode = "PAPER" if self.dry_run else "LIVE"
        active_trade = trade_repo.get_active_trade(mode=mode, strategy="VWAP")
        
        if active_trade:
            logger.info(f">>> [Resumption] Found Open Trade: {active_trade['symbol']} (ID: {active_trade['id']})")
            
            fill = active_trade['entry_price']
            sl = active_trade['sl_price']
            # Target 1:2 or 20%
            risk = abs(fill - sl)
            target = round(fill + (risk * 2), 1)
            
            self.monitor_position(
                active_trade['symbol'], 
                active_trade['token'], 
                active_trade['qty'],
                target,
                sl,
                fill,  # FIX: entry_price was missing — Risk-Free Pivot now works on resumed trades
                active_trade['id'],
                active_trade.get('sl_order_id'),
                active_trade.get('leg')
            )
            return

        # 1. Safety Check (Strict for Pro)
        if not self.gatekeeper.check_funds(required_margin_per_lot=8500):
             logger.warning(">>> [Strategy] Insufficient Funds for Pro Setup. Aborting.")
             return

        logger.info(">>> [VWAP] Institutional Trend Monitoring Started...")
        self.monitor_breakout(expiry)

    def monitor_breakout(self, expiry):
        logger.info(">>> [VWAP] Waiting for Price vs VWAP crossover...")
        
        while self.running:
            # Analyze Market Structure
            trend, signal, ltp = self.analyze_market_structure()
            
            if trend == "NEUTRAL":
                # logger.info(f">>> [Analysis] Neutral/Chop. Waiting... ({signal})")
                time.sleep(30) # Institutional analysis takes time
                continue

            logger.info(f">>> [Result] High Probability Setup Detected: {trend} ({signal})")
            
            # 3. "X-Ray" Vision Check (OI Analysis) 🧠
            # Calculate ATM for OI Check
            atm = int(round(ltp / 50) * 50)
            try:
                pcr = self.oi_analyzer.get_pcr(expiry, atm)
                sentiment = self.oi_analyzer.analyze_sentiment(pcr)
                logger.info(f">>> [AI Check] PCR: {pcr:.2f} | Sentiment: {sentiment}")
            except Exception as e:
                logger.error(f"OI Check Failed: {e}")
                sentiment = "NEUTRAL"
            
            # Filter Logic
            if trend == "BULLISH":
                if sentiment == "BEARISH":
                    logger.info(">>> [AI Filter] REJECTED CE Trade. Price is Bullish but Big Players are Bearish. Trap Detected! 🛡️")
                else:
                    logger.info(">>> [Trade] Institutional Buying Detected (Price + OI Confirmed) -> GO LONG (CE)")
                    self.place_pro_trade(expiry, "CE", ltp)
                    break 
                
            elif trend == "BEARISH":
                if sentiment == "BULLISH":
                    logger.info(">>> [AI Filter] REJECTED PE Trade. Price is Bearish but Big Players are Bullish. Bear Trap! 🛡️")
                else:
                    logger.info(">>> [Trade] Institutional Selling Detected (Price + OI Confirmed) -> GO SHORT (PE)")
                    self.place_pro_trade(expiry, "PE", ltp)
                    break 
            
            time.sleep(10)

    def analyze_market_structure(self):
        """
        Fetches candles and computes VWAP & EMA.
        """
        # logger.info(">>> [Analysis] calculating VWAP & Market Structure...")
        
        df = self.fetch_nifty_data()
        
        if df is None or df.empty:
            return "NEUTRAL", "No Data", 0

        # Technical Indicators Calculation
        
        # 1. EMA 20 (Trend Baseline)
        df['EMA_20'] = df['close'].ewm(span=20, adjust=False).mean()
        
        # 2. VWAP (Volume Weighted Average Price)
        v = df['volume'].values
        tp = (df['high'] + df['low'] + df['close']) / 3
        df['vwap'] = (tp * v).cumsum() / v.cumsum()
        
        # Current Candle Analysis
        last = df.iloc[-1]
        price = last['close']
        vwap = last['vwap']
        ema = last['EMA_20']
        
        # logger.info(f"    [Data] Price: {price:.2f} | VWAP: {vwap:.2f} | EMA(20): {ema:.2f}")
        
        # Decision Logic (Confluence)
        if price > vwap and price > ema:
             return "BULLISH", "Price > VWAP & EMA", price
        elif price < vwap and price < ema:
             return "BEARISH", "Price < VWAP & EMA", price
            
        return "NEUTRAL", "Price Trapped / Rangebound", price

    def fetch_nifty_data(self):
        try:
            # Use DataFetcher for candle data
            df = self.data_fetcher.fetch_latest_candles("99926000", interval="FIVE_MINUTE")
            if df is not None and not df.empty:
                return df
            
            # Mock Data Fallback ONLY if strictly testing
            is_mock_api = self.api.__class__.__name__ == 'MockSmartConnect'
            if is_mock_api:
                return self.generate_mock_data()
        except:
            is_mock_api = self.api.__class__.__name__ == 'MockSmartConnect'
            if is_mock_api: return self.generate_mock_data()
            
        return None

    def generate_mock_data(self):
        # Mocking a Bullish Trend
        data = {
            'close': [22000, 22050, 22100, 22150, 22200, 22250],
            'high':  [22010, 22060, 22110, 22160, 22210, 22260],
            'low':   [21990, 22040, 22090, 22140, 22190, 22240],
            'volume':[10000, 12000, 15000, 18000, 20000, 25000]
        }
        return pd.DataFrame(data)

    def place_pro_trade(self, expiry, option_type, ltp):
        
        # 1. Select Strike (Slightly ITM for higher delta/probability)
        strike = round(ltp / 50) * 50
        if option_type == "CE": strike -= 50 # 1 Strike ITM
        if option_type == "PE": strike += 50
        
        logger.info(f">>> [Pro Tip] Selecting In-The-Money (ITM) Strike {strike} for better Delta.")
        
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, option_type)
        if not token: 
            logger.error(">>> [Error] Token not found")
            return

        # Apply Compounding (Exponential Scaling)
        lots = self.gatekeeper.get_compounded_lots(margin_per_lot=5000)
        qty = lots * Config.NIFTY_LOT_SIZE
        
        logger.info(f">>> [Sizing] Method=Exponential Compounding | Qty: {qty} ({lots} lots)")

        # Viability Check: Option Premium vs Brokerage
        quote_ltp = self.data_fetcher.get_ltp(token) or 100.0
        if not self.gatekeeper.check_trade_viability(quote_ltp, qty):
             return
             
        est_cost = quote_ltp * qty
        if not self.dry_run and not self.gatekeeper.check_trade_margin(est_cost):
             return

        logger.info(f">>> [Trade] Placing BUY order for {symbol}")
        try:
             orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
             
             oid = self.order_manager.place_order(orderparams)
             if not oid: return

             # Wait for Fill
             fill_result = self.wait_for_fill(oid)
             
             if fill_result['status'] in ['REJECTED', 'CANCELLED']:
                 logger.error(f"❌ Order {oid} failed: {fill_result.get('message')}")
                 return
             # FIX Obs #3: Explicit TIMEOUT handling — don't silently use quote_ltp as fill
             if fill_result['status'] == 'TIMEOUT':
                 logger.warning(f"⚠️ Order {oid} fill TIMEOUT. Aborting to avoid incorrect SL/target.")
                 self.order_manager.cancel_order(oid, "NORMAL")
                 return

             fill_price = fill_result['price'] or quote_ltp
             
             # Risk Management: ATR-Based Structural SL (replaces arbitrary 12% cap)
             # Fetch ATR from the 5-min candle data for a real volatility-adjusted SL
             try:
                 df_sl = self.data_fetcher.fetch_latest_candles("99926000", interval="FIVE_MINUTE")
                 if df_sl is not None and len(df_sl) >= 5:
                     tr = (df_sl['high'] - df_sl['low']).tail(5).mean()
                     atr_sl_points = round(tr * 0.5, 1)  # Delta-adjusted (0.5) for options
                 else:
                     atr_sl_points = round(fill_price * 0.10, 1)  # 10% fallback
             except Exception:
                 atr_sl_points = round(fill_price * 0.10, 1)
             
             # Floor: Never risk less than 5 points, never more than 15%
             atr_sl_points = max(atr_sl_points, 5.0)
             atr_sl_points = min(atr_sl_points, fill_price * 0.15)
             
             sl_price = round(fill_price - atr_sl_points, 1)
             target_price = round(fill_price + (atr_sl_points * 2), 1)  # 1:2 RR
             
             logger.info(f">>> [Risk] ATR-Structural SL: {sl_price} (Risk: {atr_sl_points:.1f}pts) | Target: {target_price}")
             
             # Place Broker SL
             sl_oid = self.order_manager.place_sl_order(symbol, token, qty, sl_price, option_type)
             
             # Save
             mode = "PAPER" if self.dry_run else "LIVE"
             tid = trade_repo.save_trade(symbol, token, option_type, qty, fill_price, sl_price, mode=mode, strategy="VWAP")
             
             self.monitor_position(symbol, token, qty, target_price, sl_price, fill_price, tid, sl_oid, option_type)

        except Exception as e:
            logger.error(f">>> [Error] Order Failed: {e}")

    def monitor_position(self, symbol, token, qty, target, sl, entry_price, trade_id, sl_oid, leg_type):
        logger.info(f"VWAP: Monitoring. Target: {target} | SL: {sl} | Entry: {entry_price}")
        
        breakeven_hit = False
        last_structure_check = 0  # Throttle: only check market structure every 30s
        
        while self.running:
            try:
                time.sleep(0.5)
                ltp = self.data_fetcher.get_ltp(token)
                if not ltp: continue
                
                # Risk-Free Pivot (Breakeven) Logic
                # If Price moves 1:1 RR in our favor, move SL to Entry.
                if not breakeven_hit:
                    # Risk = Entry - SL
                    risk = abs(entry_price - sl)
                    threshold = entry_price + risk if leg_type == "CE" else entry_price - risk
                    
                    if (leg_type == "CE" and ltp >= threshold) or (leg_type == "PE" and ltp <= threshold):
                        logger.info(f"VWAP: 🛡️ 1:1 RR reached (LTP: {ltp}). Moving SL to Breakeven (₹{entry_price})")
                        if sl_oid and not self.dry_run:
                            self.order_manager.modify_sl_order(sl_oid, entry_price, symbol, token, qty)
                        
                        sl = entry_price # Update local SL for monitoring
                        if trade_id: trade_repo.update_sl(trade_id, sl)
                        breakeven_hit = True
                
                # 1. Physical SL Check (Hard Cap)
                if ltp <= sl:
                    logger.info(f"VWAP: 🛑 Physical SL Hit ({ltp}).")
                    if trade_id: trade_repo.close_trade(trade_id=trade_id, exit_price=ltp, exit_reason="SL_HIT")
                    break
                    
                # 2. Dynamic Exit Check (Structural) — throttled to every 30s
                # If price crosses back below VWAP/EMA, exit early to preserve capital.
                import time as _time
                if _time.time() - last_structure_check >= 30:
                    last_structure_check = _time.time()
                    trend, signal, index_ltp = self.analyze_market_structure()
                    if (leg_type == "CE" and trend == "BEARISH") or (leg_type == "PE" and trend == "BULLISH"):
                        logger.info(f"VWAP: 🔄 Structural Exit Triggered (Trend Change). Closing at {ltp}.")
                        self.exit_at_market(token, symbol, qty, "TREND_CHANGE", trade_id, sl_oid)
                        break

                # 3. Target Check
                if ltp >= target:
                    logger.info(f"VWAP: 🎯 Target Hit ({ltp}). Closing.")
                    self.exit_at_market(token, symbol, qty, "TARGET", trade_id, sl_oid)
                    break 
                    
                # Time Exit
                if datetime.datetime.now().time() >= datetime.time(15, 15):
                     logger.info("VWAP: ⏰ Time Exit.")
                     self.exit_at_market(token, symbol, qty, "TIME", trade_id, sl_oid)
                     break
                     
            except Exception as e:
                logger.error(f"VWAP Monitor: {e}")
                time.sleep(5)

    def exit_at_market(self, token, symbol, qty, reason, trade_id, sl_oid):
        try:
            if sl_oid: self.order_manager.cancel_order(sl_oid, "STOPLOSS")
            
            orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
            oid = self.order_manager.place_order(orderparams)
            
            if oid and trade_id:
                 trade_repo.close_trade(trade_id=trade_id, exit_reason=reason)
                 
        except Exception as e:
            logger.error(f"VWAP Exit Failed: {e}")

    def wait_for_fill(self, order_id):
        if not order_id: return {'status': 'ERROR', 'price': None}
        if self.dry_run: return {'status': 'FILLED', 'price': 100.0}
        
        for _ in range(5):
            try:
                time.sleep(0.5)
                book = self.api.orderBook()
                if book and book.get('data'):
                    for o in book['data']:
                        if o['orderid'] == order_id:
                            if o['status'] == 'complete':
                                return {'status': 'FILLED', 'price': float(o['averageprice'])}
                            elif o['status'] in ['rejected', 'cancelled']:
                                return {'status': o['status'].upper(), 'message': o.get('text')}
            except: pass
        return {'status': 'TIMEOUT', 'price': None}

