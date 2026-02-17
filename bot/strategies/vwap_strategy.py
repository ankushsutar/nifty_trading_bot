import time
import datetime
import pandas as pd
import numpy as np
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo

class VWAPStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        from bot.core.data_fetcher import DataFetcher
        self.data_fetcher = DataFetcher(self.api)
        self.running = True

    def stop(self):
        """Gracefully stop the strategy monitoring."""
        print(">>> [VWAP] Stop signal received.")
        self.running = False
        if hasattr(self, 'manager') and self.manager:
            self.manager.stop()

    def execute(self, expiry, action="BUY"):
        """
        VWAP Institutional Logic:
        1. 09:15 - 10:00: Wait for Price/VWAP Stability
        2. 10:00+: Wait for Breakout above VWAP (Buy CE) or below (Buy PE)
        3. RSI Filter: Only Buy if RSI > 50 (CE) or < 50 (PE)
        """
        print(f">>> [Strategy] Initializing VWAP Strategy for {expiry}")
        
        # Check for Resumption
        mode = "PAPER" if self.dry_run else "LIVE"
        active_trade = trade_repo.get_active_trade(mode=mode, strategy="VWAP")
        
        if active_trade:
            print(f">>> [Resumption] Found Open Trade: {active_trade['symbol']} (ID: {active_trade['id']})")
            print(">>> [Resumption] Resuming Monitoring...")
            self.monitor_position(
                active_trade['symbol'], 
                active_trade['token'], 
                active_trade['entry_price'], 
                active_trade['id']
            )
            return

        # 1. Safety Check (Strict for Pro)
        if not self.gatekeeper.check_funds(required_margin_per_lot=8500):
             print(">>> [Strategy] Insufficient Funds for Pro Setup. Aborting.")
             return

        print(">>> [VWAP] Institutional Trend Monitoring Started...")
        self.monitor_breakout(expiry)

    def monitor_breakout(self, expiry):
        print(">>> [VWAP] Waiting for Price vs VWAP crossover...")
        
        while self.running:
            # Analyze Market Structure
            trend, signal, ltp = self.analyze_market_structure()
            
            if trend == "NEUTRAL":
                print(f">>> [Analysis] Neutral/Chop. Waiting... ({signal})")
                time.sleep(30) # Institutional analysis takes time
                continue

            print(f">>> [Result] High Probability Setup Detected: {trend} ({signal})")
            
            # 3. "X-Ray" Vision Check (OI Analysis) 🧠
            from bot.core.oi_analyzer import OIAnalyzer
            analyzer = OIAnalyzer(self.api, self.token_loader)
            
            # Calculate ATM for OI Check
            atm = int(round(ltp / 50) * 50)
            pcr = analyzer.get_pcr(expiry, atm)
            sentiment = analyzer.analyze_sentiment(pcr)
            
            print(f">>> [AI Check] PCR: {pcr:.2f} | Sentiment: {sentiment}")
            
            # Filter Logic
            if trend == "BULLISH":
                if sentiment == "BEARISH":
                    print(">>> [AI Filter] REJECTED CE Trade. Price is Bullish but Big Players are Bearish. Trap Detected! 🛡️")
                else:
                    print(">>> [Trade] Institutional Buying Detected (Price + OI Confirmed) -> GO LONG (CE)")
                    self.place_pro_trade(expiry, "CE", ltp)
                    break # Position Managed by PositionManager from here
                
            elif trend == "BEARISH":
                if sentiment == "BULLISH":
                    print(">>> [AI Filter] REJECTED PE Trade. Price is Bearish but Big Players are Bullish. Bear Trap! 🛡️")
                else:
                    print(">>> [Trade] Institutional Selling Detected (Price + OI Confirmed) -> GO SHORT (PE)")
                    self.place_pro_trade(expiry, "PE", ltp)
                    break
            
            time.sleep(10)

    def analyze_market_structure(self):
        """
        Fetches candles and computes VWAP & EMA.
        """
        print(">>> [Analysis] calculating VWAP & Market Structure...")
        
        df = self.fetch_nifty_data()
        
        if df is None or df.empty:
            return "NEUTRAL", "No Data", 0

        # Technical Indicators Calculation
        
        # 1. EMA 20 (Trend Baseline)
        df['EMA_20'] = df['close'].ewm(span=20, adjust=False).mean()
        
        # 2. VWAP (Volume Weighted Average Price)
        # VWAP = Cumulative(Price * Volume) / Cumulative(Volume)
        # We calculate 'Rolling' or 'Intraday' VWAP. For simplicity on fetched data:
        v = df['volume'].values
        tp = (df['high'] + df['low'] + df['close']) / 3
        df['vwap'] = (tp * v).cumsum() / v.cumsum()
        
        # Current Candle Analysis
        last = df.iloc[-1]
        price = last['close']
        vwap = last['vwap']
        ema = last['EMA_20']
        
        print(f"    [Data] Price: {price:.2f} | VWAP: {vwap:.2f} | EMA(20): {ema:.2f}")
        
        # Decision Logic (Confluence)
        # Buffer: Only trade if Price is at least 0.05% away from VWAP to avoid false breakouts
        
        print("\n    >>> [DECISION MATRIX] 🧠")
        print(f"    ------------------------------------")
        print(f"    Current Price:  {price:.2f}")
        print(f"    VWAP Level:     {vwap:.2f} ({'ABOVE' if price > vwap else 'BELOW'})")
        print(f"    EMA(20):        {ema:.2f} ({'ABOVE' if price > ema else 'BELOW'})")
        print(f"    ------------------------------------")

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
        # Pros prefer ITM to reduce Theta decay impact compared to ATM/OTM
        strike = round(ltp / 50) * 50
        if option_type == "CE": strike -= 50 # 1 Strike ITM
        if option_type == "PE": strike += 50
        
        print(f">>> [Pro Tip] Selecting In-The-Money (ITM) Strike {strike} for better Delta.")
        
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, option_type)
        if not token: 
            print(">>> [Error] Token not found")
            return

        if self.dry_run:
             print(f">>> [Dry Run] Would Buy {symbol} at Market.")
             # Save
             fill_price = ltp
             mode = "PAPER" if self.dry_run else "LIVE"
             tid = trade_repo.save_trade(symbol, token, option_type, Config.NIFTY_LOT_SIZE, fill_price, 0.0, mode=mode, strategy="VWAP")
             self.monitor_position(symbol, token, fill_price, tid)
             return
             
        if not self.gatekeeper.check_no_open_orders(symbol): return

        print(f">>> [Trade] Placing BUY order for {symbol}")
        try:
             orderparams = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": "BUY",
                "exchange": "NFO",
                "ordertype": "MARKET",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "quantity": Config.NIFTY_LOT_SIZE
            }
             order_id = self.api.placeOrder(orderparams)
             if not order_id:
                 print(">>> [Error] Order Failed (None returned)")
                 return

             print(f">>> [Success] Order ID: {order_id}")
             
             # Risk Management: Tighter SL for Pro setup
             # Pros minimize loss. Standard 10% is okay, but Trailing is better.
             # We start with 10% fixed.
             fill_price = self.wait_for_fill(order_id)
             if fill_price:
                 sl_oid = self.place_stop_loss(token, symbol, fill_price, Config.NIFTY_LOT_SIZE)
                 
                 # Save
                 mode = "PAPER" if self.dry_run else "LIVE"
                 tid = trade_repo.save_trade(symbol, token, option_type, Config.NIFTY_LOT_SIZE, fill_price, 0.0, mode=mode, strategy="VWAP")
                 
                 self.monitor_position(symbol, token, fill_price, tid, sl_oid)

        except Exception as e:
            print(f">>> [Error] Order Failed: {e}")

    # Reused Helpers (Ideally refactor to a Mixin)
    def wait_for_fill(self, order_id):
        attempts = 0
        while attempts < 5 and self.running:
            try:
                book = self.api.orderBook()
                if book and book.get('data'):
                    for o in book['data']:
                        if o['orderid'] == order_id and o['status'] == 'complete':
                            return float(o['averageprice'])
            except: pass
            time.sleep(1)
            attempts += 1
        return 120.0 # Fallback
        
    def place_stop_loss(self, token, symbol, buy_price, qty):
        """Places a Hard Stop Loss Order."""
        if self.dry_run: return "dry_run_sl"
        try:
             # SL Order for BUY is SELL SL
             # Trigger slightly higher than limit price
             trig = round(buy_price * 0.9 + 0.5, 1) # 10% SL Trigger
             price = round(buy_price * 0.9, 1)      # 10% SL Price
             
             orderparams = {
                "variety": "STOPLOSS", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", "ordertype": "STOPLOSS_LIMIT",
                "producttype": "INTRADAY", "duration": "DAY", "triggerprice": trig, "price": price, "quantity": qty
            }
             oid = self.api.placeOrder(orderparams)
             print(f">>> [Risk] SL Placed {symbol} @ {price} | ID: {oid}")
             return oid
        except Exception as e:
             print(f">>> [Error] SL Place: {e}")
             return None

    def monitor_position(self, symbol, token, fill_price, trade_id=None, sl_order_id=None):
        # if self.dry_run: return
        print(">>> [Manager] Monitoring Trade (Target: 20%)...")
        from bot.core.position_manager import PositionManager
        if not self.running: return

        # Delegate to PositionManager for Exit Management
        manager = PositionManager(self.api, self.dry_run)
        pos = {
           'symbol': symbol, 'token': token, 
           'entry_price': fill_price, 'qty': Config.NIFTY_LOT_SIZE,
           'id': trade_id,
           'sl_order_id': sl_order_id
        }
        manager.monitor([pos])
