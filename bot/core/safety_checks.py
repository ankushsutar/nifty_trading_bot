import datetime
import time
import os
import json
from bot.utils.logger import logger

class SafetyGatekeeper:
    # Class-level VIX cache — shared across all instances (all strategies same process)
    _vix_cache_time = 0
    _vix_multiplier = 1.0

    def __init__(self, api, dry_run=False):
        self.api = api
        self.dry_run = dry_run
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
        logger.warning(f">>> [Gatekeeper] Market Closed. Current Time: {now}")
        return False

    def check_data_freshness(self, tick_timestamp):
        """
        Rule: Data must be < 2 seconds old.
        tick_timestamp: datetime object of the tick
        """
        if not tick_timestamp:
            logger.error(">>> [Gatekeeper] Error: No Timestamp provided.")
            return False
            
        now = datetime.datetime.now()
        # Ensure timezone awareness compatibility if needed. Assuming both are naive or same TZ.
        diff = (now - tick_timestamp).total_seconds()
        
        if diff < 2.0:
            return True
        logger.warning(f">>> [Gatekeeper] Data Stale! Delay: {diff:.2f}s")
        return False

    def get_current_capital(self):
        """Returns the available trading capital (Net Margin)."""
        try:
            available_cash = 0.0
            
            # SIMULATION MODE CHECK
            if self.dry_run:
                from bot.config.settings import Config
                available_cash = Config.SIMULATION_CAPITAL
            else:
                # REAL MODE CHECK
                # Check cache (10 seconds validity)
                if time.time() - self.last_rms_time < 10 and self.cached_rms:
                    limit = self.cached_rms
                else:
                    # 0.5s delay to prevent burst rate limit
                    time.sleep(0.5) 
                    # SmartAPI rmsLimit fetch
                    from bot.utils.rate_limiter import rate_limiter
                    rate_limiter.wait()
                    limit = self.api.rmsLimit()
                    self.cached_rms = limit
                    self.last_rms_time = time.time()
                
                if limit and limit.get('status'):
                     available_cash = float(limit['data']['net'])
                else:
                     logger.error(">>> [Gatekeeper] Could not fetch RMS Data.")
                     return 0.0

            return available_cash
        except Exception as e:
            logger.error(f">>> [Gatekeeper] Capital Check Error: {e}")
            return 0.0

    def check_funds(self, required_margin_per_lot=150000, silent=False):
        """
        Rule: Available Cash > Required Margin * 1.1 (10% Buffer)
        Note: required_margin_per_lot is an estimate.
        """
        try:
            available_cash = self.get_current_capital()
            required_total = required_margin_per_lot * 1.1 # 10% Buffer
            
            if available_cash >= required_total:
                return True
            else:
                if not silent:
                    logger.warning(f">>> [Gatekeeper] ❌ LOW FUNDS. Available: ₹{available_cash:,.2f}, Required: ₹{required_total:,.2f}")
                return False

        except Exception as e:
            logger.error(f">>> [Gatekeeper] Fund Check Error: {e}")
            return False

    def check_trade_margin(self, estimated_cost, silent=False):
        """
        Rule: Available Cash > Estimated Cost (LTP * Qty)
        This is a hard check before placing an order.
        """
        try:
            available_cash = 0.0
            
            if self.dry_run:
                from bot.config.settings import Config
                available_cash = Config.SIMULATION_CAPITAL
            else:
                # Reuse cache if available and fresh
                if time.time() - self.last_rms_time < 10 and self.cached_rms:
                    limit = self.cached_rms
                else:
                    from bot.utils.rate_limiter import rate_limiter
                    rate_limiter.wait()
                    limit = self.api.rmsLimit()
                    self.cached_rms = limit
                    self.last_rms_time = time.time()

                if limit and limit.get('status'):
                    available_cash = float(limit['data']['net'])
                else:
                    logger.error(">>> [Gatekeeper] Error fetching RMS for Trade Check.")
                    return False

            if available_cash >= estimated_cost:
                logger.info(f">>> [Gatekeeper] Margin Check Passed: ₹{available_cash:,.2f} >= ₹{estimated_cost:,.2f}")
                return True
            else:
                if not silent:
                    logger.warning(f">>> [Gatekeeper] ❌ Insufficient Funds for Trade. Available: ₹{available_cash:,.2f}, Required: ₹{estimated_cost:,.2f}")
                return False

        except Exception as e:
            logger.error(f">>> [Gatekeeper] Trade Margin Check Error: {e}")
            return False

    def check_no_open_orders(self, symbol):
        """
        Rule: No PENDING orders for the same symbol to avoid duplicates.
        """
        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            book = self.api.orderBook()
            if book and book.get('status'):
                for order in book['data']:
                    if order['tradingsymbol'] == symbol and order['status'] in ['open', 'pending']:
                        logger.warning(f">>> [Gatekeeper] Active Order exists for {symbol}. Blocking duplicate.")
                        return False
            return True
        except Exception as e:
            logger.error(f">>> [Gatekeeper] OrderBook Check Error: {e}")
            return False

    def get_daily_realized_pnl(self):
        """Fetches total realized P&L for today from the repository."""
        try:
            from bot.core.trade_repo import trade_repo
            mode = "PAPER" if self.dry_run else "LIVE"
            today_trades = trade_repo.get_today_trades(mode=mode)
            # Sum PnL of all CLOSED trades
            return sum(t.get('pnl', 0.0) or 0.0 for t in today_trades if t.get('status') == 'CLOSED')
        except Exception as e:
            logger.error(f"Error fetching daily realized P&L: {e}")
            return 0.0

    def check_max_daily_loss(self, active_unrealized_pnl=0.0):
        """
        Rule: Stop trading if (Realized + Unrealized) loss exceeds MAX_DAILY_LOSS.
        Returns: 
          - True: Safe to continue.
          - False: Limit reached. Kill trades.
        """
        from bot.config.settings import Config
        max_loss = Config.MAX_DAILY_LOSS  # e.g. -800.0
        
        realized_pnl = self.get_daily_realized_pnl()
        total_pnl = realized_pnl + active_unrealized_pnl
        
        if total_pnl <= max_loss:
             logger.critical(f">>> [Gatekeeper] 🛑 GLOBAL MAX DAILY LOSS HIT!")
             logger.critical(f"    Realized: ₹{realized_pnl:.2f} | Unrealized: ₹{active_unrealized_pnl:.2f} | Total: ₹{total_pnl:.2f}")
             logger.critical(f"    Limit: ₹{max_loss:.2f}. Halting Operations.")
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
            logger.info(f">>> [Gatekeeper] ⏸️ Blackout Period ({start}-{end}). No new trades.")
            return True
        return False

    def get_vix_adjustment(self):
        """
        Rule: If India VIX > 25, reduce quantity by 50%.
        Reads VIX from shared market_analysis.json (written by backend every 3 min).
        Falls back to ltpData only if shared file is missing/stale. Result cached 60s.
        """
        # 1. Return cached multiplier if still fresh (60s)
        if time.time() - SafetyGatekeeper._vix_cache_time < 60:
            return SafetyGatekeeper._vix_multiplier

        vix = 0.0
        try:
            # 2. Primary: read from shared intelligence file (no API call)
            state_file = os.path.join(os.getcwd(), "data", "market_analysis.json")
            if os.path.exists(state_file):
                age = time.time() - os.path.getmtime(state_file)
                if age < 600:  # fresh enough (< 10 min)
                    with open(state_file, "r") as f:
                        shared = json.load(f)
                    vix = float(shared.get("vix", 0.0))
        except Exception as e:
            logger.warning(f">>> [Risk] VIX shared read error: {e}")

        # 3. Fallback: ltpData only if shared file gave nothing
        if vix == 0.0:
            try:
                from bot.utils.rate_limiter import rate_limiter
                if rate_limiter.check_circuit_breaker() == 0:
                    rate_limiter.wait()
                    response = self.api.ltpData("NSE", "INDIA VIX", "99926017")
                    if response and response.get('status'):
                        vix = float(response['data']['ltp'])
            except Exception as e:
                logger.error(f">>> [Risk] VIX ltpData fallback error: {e}")

        # 4. Apply rule and cache result
        multiplier = 1.0
        if vix > 25.0:
            logger.warning(f">>> [Risk] ⚠️ High VIX ({vix:.1f} > 25). Reducing Quantity by 50%.")
            multiplier = 0.5

        SafetyGatekeeper._vix_cache_time = time.time()
        SafetyGatekeeper._vix_multiplier = multiplier
        return multiplier

    def check_sentiment_risk(self, direction="LONG"):
        """
        Rule: 
        - Block LONG if Sentiment is BEARISH (<-0.2).
        - Block SHORT if Sentiment is BULLISH (>0.2).
        """
        try:
            from backend.news_service import news_service
            score = news_service.get_sentiment_score()
            
            if direction == "LONG" and score < -0.2:
                logger.warning(f">>> [Gatekeeper] 🛑 Trade Blocked. Sentiment is BEARISH ({score}).")
                return False
            
            if direction == "SHORT" and score > 0.2:
                 logger.warning(f">>> [Gatekeeper] 🛑 Trade Blocked. Sentiment is BULLISH ({score}).")
                 return False
            return True
        except Exception as e:
            logger.warning(f">>> [Gatekeeper] Sentiment Check Error: {e}")
            return True

    def get_compounded_lots(self, margin_per_lot):
        """
        Calculates lot size based on current capital.
        Tuned for ₹8,000: uses 10% buffer (not 20%) so 1 lot fits within capital.
        Formula: Lots = floor(Capital / (margin_per_lot * 1.1))
        """
        try:
            from bot.config.settings import Config
            capital = self.get_current_capital()
            if capital < Config.MIN_CAPITAL_THRESHOLD: 
                logger.warning(f">>> [Gatekeeper] Capital ₹{capital:.0f} below minimum ₹{Config.MIN_CAPITAL_THRESHOLD:.0f}. No lots.")
                return 0

            # Use 10% margin buffer (was 20% — too tight for ₹8k)
            lots = int(capital / (margin_per_lot * 1.1))

            # Floor at 1 lot for small accounts
            if capital >= Config.MIN_CAPITAL_THRESHOLD and lots < 1:
                lots = 1

            return lots
        except Exception as e:
            logger.error(f"Compounding Error: {e}")
            return 1

    def check_trade_viability(self, premium, qty):
        """
        Rule: Brokerage should not exceed 15% of the trade value.
        Brokerage is ~₹60 per round-trip (Buy+Sell).
        Threshold raised from 10% to 15% for ₹8,000 accounts where
        options may be cheaper (₹50–₹80 range).
        Min viable premium: ₹60 / (0.15 * 65) = ₹6.15 per share.
        """
        trade_value = premium * qty
        if trade_value <= 0: return False

        brokerage_ratio = 60.0 / trade_value
        if brokerage_ratio > 0.15:
            logger.warning(f">>> [Gatekeeper] ⚠️ Trade Viability Low: Brokerage is {brokerage_ratio*100:.1f}% of trade value (>{15}%). Skipping.")
            return False

        return True

