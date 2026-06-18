import datetime
import time
import os
import json
from bot.utils.logger import logger

class SafetyGatekeeper:
    # Class-level VIX cache — shared across all instances (all strategies same process)
    _vix_cache_time = 0
    _vix_multiplier = 1.0
    # Class-level IV Rank cache (0 = cheapest options in 30 days, 1 = most expensive)
    _iv_rank = 0.5

    def __init__(self, api, dry_run=False):
        self.api = api
        self.dry_run = dry_run
        self.cached_rms = None
        self.last_rms_time = 0
        self.last_breaker_triggered = None

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
        Rule: Available Cash > Required Margin * (1 + tier.margin_buffer_pct)
        Buffer scales with capital tier — tighter for small accounts, looser for large.
        """
        try:
            from bot.config.settings import Config
            available_cash = self.get_current_capital()
            tier = Config.get_tier(available_cash)
            required_total = required_margin_per_lot * (1 + tier.margin_buffer_pct)

            if available_cash >= required_total:
                return True
            else:
                if not silent:
                    logger.warning(
                        f">>> [Gatekeeper] ❌ LOW FUNDS [{tier.name}]. "
                        f"Available: ₹{available_cash:,.2f}, Required: ₹{required_total:,.2f}"
                    )
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

    def get_broker_realized_pnl(self):
        """
        Fetches positions from the broker API and calculates realized P&L directly
        to serve as an absolute database-independent safety gatekeeper fallback.
        """
        if self.dry_run:
            return self.get_daily_realized_pnl()

        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            
            if not self.api:
                logger.warning(">>> [Gatekeeper] Broker API is not initialized. Falling back to DB realized PnL.")
                return self.get_daily_realized_pnl()

            pos_resp = None
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    from bot.utils.rate_limiter import rate_limiter
                    rate_limiter.wait()
                    pos_resp = self.api.position()
                    if pos_resp and pos_resp.get('status'):
                        break
                except Exception as ex:
                    if attempt == max_retries - 1:
                        logger.warning(f">>> [Gatekeeper] Broker position API error: {ex}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)

            if not pos_resp or not pos_resp.get('status'):
                logger.warning(">>> [Gatekeeper] Broker position fetch failed. Falling back to DB-based PnL.")
                return self.get_daily_realized_pnl()

            positions = pos_resp.get('data')
            if not positions:
                # Successful response but no positions are active/closed yet (normal before trading)
                return 0.0

            total_realized_pnl = 0.0
            for pos in pos_resp.get('data', []):
                try:
                    buy_qty = int(pos.get('buyqty', 0) or 0)
                    sell_qty = int(pos.get('sellqty', 0) or 0)
                    buy_avg = float(pos.get('buyavgprice', 0) or 0)
                    sell_avg = float(pos.get('sellavgprice', 0) or 0)
                    
                    matched_qty = min(buy_qty, sell_qty)
                    realized_pnl = matched_qty * (sell_avg - buy_avg)
                    
                    realised_field = pos.get('realisedprice') or pos.get('realisedpnl')
                    if realised_field is not None:
                        try:
                            realized_pnl = float(realised_field)
                        except ValueError:
                            pass
                            
                    total_realized_pnl += realized_pnl
                except Exception as pos_err:
                    logger.warning(f">>> [Gatekeeper] Error parsing position {pos.get('tradingsymbol')}: {pos_err}")
                    
            logger.info(f">>> [Gatekeeper] Broker-verified Daily Realized PnL: ₹{total_realized_pnl:.2f}")
            return total_realized_pnl

        except Exception as e:
            logger.error(f">>> [Gatekeeper] Error calculating broker realized PnL: {e}")
            return self.get_daily_realized_pnl()

    def track_peak_profit(self, current_total_pnl):
        """Tracks the highest realized+unrealized profit reached today."""
        try:
            stats_file = os.path.join(os.getcwd(), "data", "session_stats.json")
            today = datetime.date.today().isoformat()
            stats = {}
            if os.path.exists(stats_file):
                with open(stats_file, "r") as f:
                    stats = json.load(f)
            
            day_stats = stats.setdefault(today, {})
            mode_key = "paper" if self.dry_run else "live"
            
            # Migration check: if top-level keys exist in day_stats, migrate them
            if "starting_capital" in day_stats or "peak_profit" in day_stats:
                old_cap = day_stats.pop("starting_capital", 0.0)
                old_peak = day_stats.pop("peak_profit", 0.0)
                old_lu = day_stats.pop("last_updated", 0)
                day_stats[mode_key] = {
                    "starting_capital": old_cap,
                    "peak_profit": old_peak,
                    "last_updated": old_lu
                }
                stats[today] = day_stats
                os.makedirs(os.path.dirname(stats_file), exist_ok=True)
                with open(stats_file, "w") as f:
                    json.dump(stats, f)
                logger.info(f">>> [Gatekeeper] Migrated session stats to mode-isolated structure (track_peak_profit).")

            if mode_key not in day_stats or not isinstance(day_stats[mode_key], dict):
                day_stats[mode_key] = {
                    "starting_capital": 0.0,
                    "peak_profit": 0.0,
                    "last_updated": 0
                }
            
            mode_stats = day_stats[mode_key]
            
            if current_total_pnl > mode_stats["peak_profit"]:
                mode_stats["peak_profit"] = round(current_total_pnl, 2)
                mode_stats["last_updated"] = time.time()
                day_stats[mode_key] = mode_stats
                stats[today] = day_stats
                
                os.makedirs(os.path.dirname(stats_file), exist_ok=True)
                with open(stats_file, "w") as f:
                    json.dump(stats, f)
                logger.info(f">>> [Gatekeeper] 🏆 New Peak Profit Reached ({mode_key}): ₹{mode_stats['peak_profit']:.2f}")
            
            return mode_stats["peak_profit"]
        except Exception as e:
            logger.error(f"Error tracking peak profit: {e}")
            return 0.0

    def check_profit_protection(self, active_unrealized_pnl=0.0):
        """
        Elite Rule: Protects realized profits from being wiped out.
        If Peak Profit > ₹1,000, and current PnL falls below 50% of peak, stop for the day.
        """
        from bot.config.settings import Config
        
        # --- FIX: Startup/Recovery Shield ---
        # If there's an active trade in DB, but the caller passed 0.0 unrealized,
        # skip the check to avoid false-positive lockouts before the strategy
        # resolves the actual current LTP of the running position.
        if active_unrealized_pnl == 0.0:
            try:
                from bot.core.trade_repo import trade_repo
                mode = "PAPER" if self.dry_run else "LIVE"
                open_trades = trade_repo.get_open_trades(mode=mode)
                if open_trades:
                    # We have an open trade, don't compute safety until its real value is supplied.
                    return True
            except Exception:
                pass

        if not Config.PROFIT_PROTECTION_ENABLED:
            return True

        realized_pnl = self.get_daily_realized_pnl()
        total_pnl = realized_pnl + active_unrealized_pnl
        
        peak = self.track_peak_profit(total_pnl)
        
        # Only activate protection if peak was significant
        PROTECTION_THRESHOLD = Config.PROFIT_PROTECTION_THRESHOLD
        DRAWDOWN_ALLOWED = Config.PROFIT_PROTECTION_DRAWDOWN_PCT
        
        if peak >= PROTECTION_THRESHOLD:
            min_allowed_pnl = peak * (1 - DRAWDOWN_ALLOWED)
            if total_pnl < min_allowed_pnl:
                logger.critical(f">>> [Gatekeeper] 🛡️ PROFIT PROTECTION TRIGGERED!")
                logger.critical(f"    Peak Profit: ₹{peak:.2f} | Current: ₹{total_pnl:.2f} | Floor: ₹{min_allowed_pnl:.2f}")
                logger.critical("    Stopping to preserve remaining gains. Pro-Trader Mode: Locked.")
                self.last_breaker_triggered = "PROFIT_PROTECTION"
                return False
        return True

    def get_starting_capital(self):
        """
        Retrieves or initializes the starting capital for the trading day.
        Persisted in data/session_stats.json to prevent the 'Rubber Band' effect
        when capital/margin changes dynamically during the session.
        """
        try:
            stats_file = os.path.join(os.getcwd(), "data", "session_stats.json")
            today = datetime.date.today().isoformat()
            stats = {}
            if os.path.exists(stats_file):
                with open(stats_file, "r") as f:
                    stats = json.load(f)
            
            day_stats = stats.setdefault(today, {})
            mode_key = "paper" if self.dry_run else "live"
            
            # Migration check: if top-level keys exist in day_stats, migrate them
            if "starting_capital" in day_stats or "peak_profit" in day_stats:
                old_cap = day_stats.pop("starting_capital", 0.0)
                old_peak = day_stats.pop("peak_profit", 0.0)
                old_lu = day_stats.pop("last_updated", 0)
                day_stats[mode_key] = {
                    "starting_capital": old_cap,
                    "peak_profit": old_peak,
                    "last_updated": old_lu
                }
                stats[today] = day_stats
                os.makedirs(os.path.dirname(stats_file), exist_ok=True)
                with open(stats_file, "w") as f:
                    json.dump(stats, f)
                logger.info(f">>> [Gatekeeper] Migrated session stats to mode-isolated structure (get_starting_capital).")

            if mode_key not in day_stats or not isinstance(day_stats[mode_key], dict):
                day_stats[mode_key] = {
                    "starting_capital": 0.0,
                    "peak_profit": 0.0,
                    "last_updated": 0
                }
            
            mode_stats = day_stats[mode_key]
            starting_capital = mode_stats.get("starting_capital", 0.0)
            
            if starting_capital <= 0.0:
                # First time running today — fetch current capital as starting capital
                current_cap = self.get_current_capital()
                if current_cap > 0.0:
                    starting_capital = round(current_cap, 2)
                    mode_stats["starting_capital"] = starting_capital
                    day_stats[mode_key] = mode_stats
                    stats[today] = day_stats
                    
                    # Ensure directory exists
                    os.makedirs(os.path.dirname(stats_file), exist_ok=True)
                    with open(stats_file, "w") as f:
                        json.dump(stats, f)
                    logger.info(f">>> [Gatekeeper] 📈 Initialized Starting Capital for today ({mode_key}): ₹{starting_capital:,.2f}")
                else:
                    # Fallback to simulation/default capital if fetch fails
                    from bot.config.settings import Config
                    starting_capital = float(Config.SIMULATION_CAPITAL)
                    logger.warning(f">>> [Gatekeeper] Fetch failed. Using simulation fallback for starting capital ({mode_key}): ₹{starting_capital:,.2f}")
            
            return starting_capital
        except Exception as e:
            logger.error(f"Error in get_starting_capital: {e}")
            from bot.config.settings import Config
            return float(Config.SIMULATION_CAPITAL)

    def check_max_daily_loss(self, active_unrealized_pnl=0.0, worst_case_new_loss=0.0):
        """
        Rule: Stop trading if (Realized + Unrealized) loss exceeds tier daily loss limit.
        Limit is a percentage of starting capital — scales automatically with account size.
        """
        self.last_breaker_triggered = None
        from bot.config.settings import Config
        starting_capital = self.get_starting_capital()
        tier             = Config.get_tier(starting_capital)
        max_loss         = -(starting_capital * tier.max_daily_loss_pct)

        realized_pnl = self.get_broker_realized_pnl()
        total_pnl    = realized_pnl + active_unrealized_pnl

        # First check profit protection
        if not self.check_profit_protection(active_unrealized_pnl):
            # self.last_breaker_triggered is set inside check_profit_protection
            return False

        if total_pnl <= max_loss:
            logger.critical(f">>> [Gatekeeper] 🛑 GLOBAL MAX DAILY LOSS HIT! [{tier.name} tier]")
            logger.critical(
                f"    Realized: ₹{realized_pnl:.2f} | Unrealized: ₹{active_unrealized_pnl:.2f} | "
                f"Total: ₹{total_pnl:.2f} | Limit: ₹{max_loss:.2f} ({tier.max_daily_loss_pct*100:.0f}%)"
            )
            logger.critical("    Halting Operations.")
            self.last_breaker_triggered = "MAX_DAILY_LOSS"
            return False

        # Worst-case loss projection block
        if worst_case_new_loss > 0.0:
            projected_pnl = total_pnl - worst_case_new_loss
            if projected_pnl <= max_loss:
                logger.critical(f">>> [Gatekeeper] 🛑 ENTRY BLOCKED: Projected worst-case loss of ₹{worst_case_new_loss:.2f} would breach daily limit.")
                logger.critical(
                    f"    Current PnL: ₹{total_pnl:.2f} | Projected: ₹{projected_pnl:.2f} | Limit: ₹{max_loss:.2f}"
                )
                self.last_breaker_triggered = "MAX_DAILY_LOSS"
                return False

        return True


    def check_instrument_cooldown(self, symbol):
        """
        Rule: If an instrument was just closed with a loss, wait 60 minutes before re-entry.
        Prevents "Revenge Trading" or "Averaging Down" on a losing contract.
        """
        try:
            from bot.core.trade_repo import trade_repo
            mode = "PAPER" if self.dry_run else "LIVE"
            today_trades = trade_repo.get_today_trades(mode=mode)
            
            # Find the last closed trade for this symbol
            # Note: get_today_trades returns trades sorted by ID DESCENDING, so index 0 is the most recent.
            symbol_trades = [t for t in today_trades if t.get('symbol') == symbol and t.get('status') == 'CLOSED']
            if not symbol_trades:
                return True
                
            last_trade = symbol_trades[0]
            pnl_val = last_trade.get('pnl', 0.0) or 0.0
            if pnl_val < 0.0:
                # Check time since close
                exit_time = last_trade.get('closed_at')
                if not exit_time:
                    # Fallback to exit_time string
                    exit_time_str = last_trade.get('exit_time')
                    if exit_time_str:
                        exit_time = datetime.datetime.fromisoformat(exit_time_str)
                
                if not exit_time:
                    return True
                
                # Make naive local for comparison if it is timezone-aware
                if exit_time.tzinfo is not None:
                    exit_time = exit_time.astimezone().replace(tzinfo=None)
                    
                diff = (datetime.datetime.now() - exit_time).total_seconds()
                
                if diff < 3600: # 60 minutes
                    logger.warning(
                        f">>> [Gatekeeper] 🛡️ INSTRUMENT COOLDOWN: {symbol} just lost. "
                        f"Wait {int((3600 - diff)/60)}m more to avoid revenge trading."
                    )
                    return False
            return True
        except Exception as e:
            logger.error(f"Instrument Cooldown Error: {e}")
            return True

    def is_blackout_period(self):

        """
        Rule: No new trades between 11:30 AM - 01:00 PM (Configurable).
        """
        from bot.config.settings import Config
        if Config.TRADE_FULL_DAY:
            return False

        now = datetime.datetime.now().time()
        start = datetime.time(*Config.BLACKOUT_START_TIME)
        end = datetime.time(*Config.BLACKOUT_END_TIME)
        
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

        # 4. Apply rule and cache result — dynamic VIX scaling
        if vix <= 0:
            vix = 15.0  # default neutral VIX

        multiplier = 1.0
        if vix > 18.0:
            multiplier = min(1.0, 15.0 / vix)
            logger.warning(
                f">>> [Risk] ⚠️ Dynamic VIX Scaling Active (VIX={vix:.1f} > 18.0). "
                f"Sizing Multiplier: {multiplier:.2f}x"
            )

        SafetyGatekeeper._vix_cache_time = time.time()
        SafetyGatekeeper._vix_multiplier = multiplier

        # 5. Track rolling 30-day VIX range → compute IV Rank
        # IV Rank tells strategies whether options are cheap or expensive right now.
        if vix > 0:
            try:
                vix_hist_file = os.path.join(os.getcwd(), "data", "vix_history.json")
                today_str = datetime.date.today().isoformat()
                vix_hist = {}
                if os.path.exists(vix_hist_file):
                    with open(vix_hist_file, "r") as f:
                        vix_hist = json.load(f)
                vix_hist[today_str] = round(vix, 2)
                # Prune to last 30 calendar days
                cutoff = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
                vix_hist = {k: v for k, v in vix_hist.items() if k >= cutoff}
                with open(vix_hist_file, "w") as f:
                    json.dump(vix_hist, f)
                if len(vix_hist) >= 5:
                    vals = list(vix_hist.values())
                    vix_lo, vix_hi = min(vals), max(vals)
                    iv_rank = (vix - vix_lo) / (vix_hi - vix_lo) if (vix_hi - vix_lo) > 0 else 0.5
                    SafetyGatekeeper._iv_rank = round(iv_rank, 3)
                    if iv_rank > 0.70:
                        logger.warning(
                            f">>> [Risk] 📈 IV Rank={iv_rank:.0%} "
                            f"(VIX {vix:.1f}, 30d range {vix_lo:.1f}–{vix_hi:.1f}): "
                            "Options are EXPENSIVE. Strategies will prefer near-ATM strikes."
                        )
            except Exception as e:
                logger.warning(f">>> [Risk] IV Rank update error: {e}")

        return multiplier

    def get_iv_rank(self) -> float:
        """
        Returns the current IV Rank (0.0–1.0) based on the rolling 30-day VIX range.
          0.0 = cheapest options seen in 30 days (buy OTM freely)
          1.0 = most expensive options seen in 30 days (prefer ATM, reduce OTM depth)
        Returns 0.5 (neutral) when insufficient history exists (<5 trading days).
        Updated automatically each time get_vix_adjustment() is called (every 60s).
        """
        return SafetyGatekeeper._iv_rank

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

    def get_compounded_lots(self, margin_per_lot, multiplier=1.0):
        """
        Calculates lot size based on current capital and the active capital tier.
        Formula: Lots = floor(Capital / (margin_per_lot * (1 + tier.margin_buffer_pct))) * multiplier
        Hard-capped at tier.max_lots (0 = unlimited for LARGE accounts).
        """
        try:
            from bot.config.settings import Config
            capital = self.get_current_capital()
            tier    = Config.get_tier(capital)

            if capital < tier.min_capital_threshold:
                logger.warning(
                    f">>> [Gatekeeper] Capital ₹{capital:.0f} below "
                    f"[{tier.name}] minimum ₹{tier.min_capital_threshold:.0f}. No lots."
                )
                return 0

            # Base count based on bare affordability
            base_lots = int(capital / (margin_per_lot * (1 + tier.margin_buffer_pct)))
            
            # Apply multiplier (Scaling up/down based on confidence)
            lots = int(base_lots * multiplier)

            # Floor at 1 lot only if capital can actually cover bare margin
            if lots < 1:
                if capital >= margin_per_lot:
                    lots = 1
                else:
                    logger.warning(
                        f">>> [Gatekeeper] ❌ Cannot afford 1 lot [{tier.name}]. "
                        f"Capital: ₹{capital:,.0f}, Margin needed: ₹{margin_per_lot:,.0f}. Returning 0 lots."
                    )
                    return 0

            # Cap at tier maximum (0 = no cap for LARGE tier)
            if tier.max_lots > 0:
                lots = min(lots, tier.max_lots)

            # --- RISK-BASED CAPPING (Safety Enhancement) ---
            # Ensure that a single trade hit (at sl_pct) doesn't wipe out the daily loss limit.
            daily_loss_limit = capital * tier.max_daily_loss_pct
            risk_per_lot = margin_per_lot * tier.sl_pct  # Max % loss per lot
            
            if lots * risk_per_lot > daily_loss_limit:
                max_safe_lots = int(daily_loss_limit / risk_per_lot)
                if max_safe_lots < lots:
                    logger.warning(
                        f">>> [Gatekeeper] 🛡️ Risk Cap: Reducing lots from {lots} to {max_safe_lots} "
                        f"to protect Daily Loss Limit (₹{daily_loss_limit:.0f})."
                    )
                    lots = max(1, max_safe_lots)

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

