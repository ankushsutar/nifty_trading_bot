import datetime
import time

class SafetyGatekeeper:
    def __init__(self, api):
        self.api = api
        self.cached_rms = None
        self.last_rms_time = 0

    def is_market_open(self):
        """
        Hard rule: 09:15 to 15:29 IST
        """
        now = datetime.datetime.now().time()
        start = datetime.time(9, 15)
        end = datetime.time(15, 29)
        
        if start <= now <= end:
            return True
        print(f">>> [Gatekeeper] Market Closed. Current Time: {now}")
        return False

    def check_data_freshness(self, tick_timestamp):
        """
        Rule: Data must be < 2 seconds old.
        tick_timestamp: datetime object of the tick
        """
        if not tick_timestamp:
            print(">>> [Gatekeeper] Error: No Timestamp provided.")
            return False
            
        now = datetime.datetime.now()
        # Ensure timezone awareness compatibility if needed. Assuming both are naive or same TZ.
        diff = (now - tick_timestamp).total_seconds()
        
        if diff < 2.0:
            return True
        print(f">>> [Gatekeeper] Data Stale! Delay: {diff:.2f}s")
        return False

    def check_funds(self, required_margin_per_lot=150000):
        """
        Rule: Available Cash > Required Margin * 1.1 (10% Buffer)
        Note: required_margin_per_lot is an estimate.
        """
        try:
            # Check cache (10 seconds validity)
            if time.time() - self.last_rms_time < 10 and self.cached_rms:
                limit = self.cached_rms
            else:
                # 0.5s delay to prevent burst rate limit
                time.sleep(0.5) 
                # SmartAPI rmsLimit fetch
                limit = self.api.rmsLimit()
                self.cached_rms = limit
                self.last_rms_time = time.time()
            
            if limit and limit.get('status'):
                # 'net' or 'availableCash' depending on response structure
                # Typically data['net'] is the net available margin
                available_cash = float(limit['data']['net'])
                
                required_total = required_margin_per_lot * 1.1 # 10% Buffer
                
                if available_cash >= required_total:
                    print(f"\n    ------------------------------------")
                    print(f"    [ GATEKEEPER ] ACCOUNT HEALTH 🛡️")
                    print(f"    ------------------------------------")
                    print(f"    Available Cash:   ₹ {available_cash:,.2f}")
                    print(f"    Required Margin:  ₹ {required_total:,.2f}")
                    print(f"    Buffer Status:    ✅ ADEQUATE")
                    print(f"    ------------------------------------\n")
                    return True
                else:
                    print(f"\n    ------------------------------------")
                    print(f"    [ GATEKEEPER ] ACCOUNT HEALTH 🛡️")
                    print(f"    ------------------------------------")
                    print(f"    Available Cash:   ₹ {available_cash:,.2f}")
                    print(f"    Required Margin:  ₹ {required_total:,.2f}")
                    print(f"    Buffer Status:    ❌ LOW FUNDS")
                    print(f"    ------------------------------------\n")
                    # print(f">>> [Gatekeeper] Insufficient Funds! Available: ₹{available_cash}, Required (with buffer): ₹{required_total}")
                    return False
            else:
                print(">>> [Gatekeeper] Could not fetch RMS Data.")
                return False
        except Exception as e:
            print(f">>> [Gatekeeper] Fund Check Error: {e}")
            # Fail safe: If we can't verify funds, we arguably should stop.
            # But during Mock/Test, this might differ.
            return False

    def check_trade_margin(self, estimated_cost):
        """
        Rule: Available Cash > Estimated Cost (LTP * Qty)
        This is a hard check before placing an order.
        """
        try:
            # Reuse cache if available and fresh
            if time.time() - self.last_rms_time < 10 and self.cached_rms:
                limit = self.cached_rms
            else:
                # Small delay
                time.sleep(0.5)
                limit = self.api.rmsLimit()
                self.cached_rms = limit
                self.last_rms_time = time.time()

            if limit and limit.get('status'):
                available_cash = float(limit['data']['net'])
                
                if available_cash >= estimated_cost:
                    print(f">>> [Gatekeeper] Margin Check Passed: ₹{available_cash:,.2f} >= ₹{estimated_cost:,.2f}")
                    return True
                else:
                    print(f">>> [Gatekeeper] ❌ Insufficient Funds for Trade. Available: ₹{available_cash:,.2f}, Required: ₹{estimated_cost:,.2f}")
                    return False
            else:
                print(">>> [Gatekeeper] Error fetching RMS for Trade Check.")
                return False
        except Exception as e:
            print(f">>> [Gatekeeper] Trade Margin Check Error: {e}")
            return False

    def check_no_open_orders(self, symbol):
        """
        Rule: No PENDING orders for the same symbol to avoid duplicates.
        """
        try:
            book = self.api.orderBook()
            if book and book.get('status'):
                for order in book['data']:
                    if order['tradingsymbol'] == symbol and order['status'] in ['open', 'pending']:
                        print(f">>> [Gatekeeper] Active Order exists for {symbol}. Blocking duplicate.")
                        return False
            return True
        except Exception as e:
            print(f">>> [Gatekeeper] OrderBook Check Error: {e}")
            return False

    def check_max_daily_loss(self, current_pnl):
        """
        Rule: Stop trading if loss > ₹2,000 (per lot).
        Note: current_pnl should be negative for loss.
        """
        MAX_LOSS = -2000
        if current_pnl <= MAX_LOSS:
             print(f">>> [Gatekeeper] 🛑 MAX DAILY LOSS HIT ({current_pnl}). Halting Trading.")
             return False
        return True

    def is_blackout_period(self):
        """
        Rule: No new trades between 11:30 AM - 01:00 PM.
        """
        now = datetime.datetime.now().time()
        start = datetime.time(11, 30)
        end = datetime.time(13, 0)
        
        if start <= now <= end:
            print(f">>> [Gatekeeper] ⏸️ Blackout Period ({start}-{end}). No new trades.")
            return True
        return False

    def get_vix_adjustment(self):
        """
        Rule: If India VIX > 25, reduce quantity by 50%.
        Returns multiplier (1.0 or 0.5).
        """
        try:
            # Try to fetch INDIA VIX. Token for INDIA VIX on NSE is usually 26009 or similar, 
            # but depends on broker mapping. 
            # Angel One token for 'INDIA VIX' is '99926009' (check if valid) or we search scrip.
            # For now, we will try a standard token or mock it if it fails.
            
            # Assuming 99926009 is India VIX based on Nifty being 99926000
            vix_token = "99926017" 
            
            response = self.api.ltpData("NSE", "INDIA VIX", vix_token)
            if response and response.get('status'):
                vix = float(response['data']['ltp'])
                # print(f">>> [Market] India VIX: {vix}")
                
                if vix > 25.0:
                    print(f">>> [Risk] ⚠️ High VIX ({vix} > 25). Reducing Quantity by 50%.")
                    return 0.5
            else:
                # print(">>> [Risk] Could not fetch VIX. Assuming Normal.")
                pass
                
        except Exception as e:
            # print(f">>> [Risk] VIX Check Error: {e}")
            pass
            
        return 1.0

    def check_sentiment_risk(self, direction="LONG"):
        """
        Rule: 
        - Block LONG if Sentiment is VERY BEARISH (<-0.5).
        - Block SHORT if Sentiment is VERY BULLISH (>0.5).
        """
        try:
            from backend.news_service import news_service
            score = news_service.get_sentiment_score()
            
            if direction == "LONG" and score < -0.5:
                print(f">>> [Gatekeeper] 🛑 Trade Blocked. Sentiment is VERY BEARISH ({score}).")
                return False
            
            if direction == "SHORT" and score > 0.5:
                 print(f">>> [Gatekeeper] 🛑 Trade Blocked. Sentiment is VERY BULLISH ({score}).")
                 return False
                 
            # print(f">>> [Gatekeeper] Sentiment Check Passed ({score}).")
            return True
            
        except Exception as e:
            # print(f">>> [Gatekeeper] Sentiment Check Error: {e}")
            return True
