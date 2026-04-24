import datetime
import time
import os
import json
from bot.utils.logger import logger
from bot.config.settings import Config
from bot.config.instruments import get_instrument

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

    def is_market_open(self):
        """
        Dynamically checks if market is open based on the active instrument.
        """
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        now = datetime.datetime.now().time()
        
        start_h, start_m = map(int, instr.market_start.split(":"))
        end_h, end_m = map(int, instr.market_end.split(":"))
        
        start = datetime.time(start_h, start_m)
        end = datetime.time(end_h, end_m)
        
        if start <= now <= end:
            return True
        logger.warning(f">>> [Gatekeeper] Market Closed for {instr.name}. Current Time: {now} (Window: {instr.market_start}-{instr.market_end})")
        return False

    def is_gamma_window(self):
        """
        Rule: Gamma Blast only active between 13:45 and 14:15 IST (Master Sheet).
        """
        now = datetime.datetime.now().time()
        capital = self.get_current_capital()
        tier = Config.get_tier(capital)
        
        start_h, start_m = map(int, tier.gamma_window_start.split(":"))
        end_h, end_m = map(int, tier.gamma_window_end.split(":"))
        
        start = datetime.time(start_h, start_m)
        end = datetime.time(end_h, end_m)
        
        return start <= now <= end

    def get_market_state(self):
        """
        Returns the current market state based on Time-of-Day (IST).
        SLEEP: 09:00-13:00 (Range-bound/Low Volume)
        WARM_UP: 13:00-17:00 (European Transition)
        AGGRESSIVE: 17:00-22:30 (US Session/High Volatility - THE GOLDEN WINDOW)
        COOL_DOWN: 22:30-Close (Exit Only/Low Liquidity)
        """
        now = datetime.datetime.now().time()
        
        if datetime.time(9, 0) <= now < datetime.time(13, 0):
            return "SLEEP"
        elif datetime.time(13, 0) <= now < datetime.time(17, 0):
            return "WARM_UP"
        elif datetime.time(17, 0) <= now < datetime.time(22, 30):
            return "AGGRESSIVE"
        else:
            return "COOL_DOWN"

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
        Rule: Available Cash > Required Margin * 1.2 (Margin Buffer)
        The 1.2x multiplier protects against MTM fluctuations causing auto-square-offs.
        """
        try:
            available_cash = self.get_current_capital()
            # Enforce 1.2x buffer across all commodity/high-risk trades
            required_total = required_margin_per_lot * 1.2

            if available_cash >= required_total:
                return True
            else:
                if not silent:
                    logger.warning(
                        f">>> [Gatekeeper] ❌ INSUFFICIENT FUNDS. "
                        f"Available: ₹{available_cash:,.2f}, Required (1.2x Buffer): ₹{required_total:,.2f}"
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
        """
        Fetches total realized P&L for today across ALL traded symbols.
        Multi-asset safety: each symbol has its own MongoDB (bot_nifty, bot_banknifty,
        etc.), so summing only the active symbol's DB would allow the daily loss limit
        to be breached independently on each asset.  This aggregates them all.
        """
        try:
            from bot.core.trade_repo import trade_repo
            mode = "PAPER" if self.dry_run else "LIVE"
            return trade_repo.get_all_symbols_daily_pnl(mode=mode)
        except Exception as e:
            logger.error(f"Error fetching cross-asset daily realized P&L: {e}")
            return 0.0

    def check_max_daily_loss(self, active_unrealized_pnl=0.0):
        """
        Rule: Stop trading if (Realized + Unrealized) loss exceeds tier daily loss limit.
        Limit is a percentage of current capital — scales automatically with account size.
        Returns:
          - True:  Safe to continue.
          - False: Limit reached. Kill trades.
        """
        from bot.config.settings import Config
        capital  = self.get_current_capital()
        tier     = Config.get_tier(capital)
        max_loss = -(capital * tier.max_daily_loss_pct)

        realized_pnl = self.get_daily_realized_pnl()
        total_pnl    = realized_pnl + active_unrealized_pnl

        if total_pnl <= max_loss:
            logger.critical(f">>> [Gatekeeper] 🛑 GLOBAL MAX DAILY LOSS HIT! [{tier.name} tier]")
            logger.critical(
                f"    Realized: ₹{realized_pnl:.2f} | Unrealized: ₹{active_unrealized_pnl:.2f} | "
                f"Total: ₹{total_pnl:.2f} | Limit: ₹{max_loss:.2f} ({tier.max_daily_loss_pct*100:.0f}%)"
            )
            logger.critical("    Halting Operations.")
            return False
        return True

    def is_blackout_period(self):
        """
        Rule: No new trades during defined blackout windows.
        Uses the Market State Machine to determine if trading is optimized.
        """
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        state = self.get_market_state()

        if instr.asset_type == "INDEX":
            now = datetime.datetime.now().time()
            start = datetime.time(11, 30)
            end = datetime.time(13, 0)
            if start <= now <= end:
                # Goldman Sachs Move: If ADX > 35, the trend is strong enough to ignore the mid-day dip.
                from backend.market_service import market_service
                market_data = market_service.get_market_data()
                adx = market_data.get('analysis', {}).get('adx', 0)
                if adx > 35:
                    logger.info(f">>> [Gatekeeper] 🚀 TRENDING MARKET (ADX={adx:.1f}): Bypassing mid-day blackout.")
                    return False
                logger.info(f">>> [Gatekeeper] ⏸️ Blackout Period ({start}-{end}). No new trades.")
                return True

        # Commodity: restrict entries based on liquidity/volatility windows
        if instr.asset_type == "COMMODITY":
            if self.is_in_delivery_period():
                logger.critical(">>> [Gatekeeper] 🛑 COMMODITY DELIVERY GUARD: Entry Blocked (Expiry Day).")
                return True
            
            # ── COMMODITY SLEEP WINDOW (9AM-1PM) ──
            # Re-implemented but with a "Global Volatility" bypass (VIX > 18).
            # Commodities often move on Asian session news, but low-volatility mornings can be choppy.
            if state == "SLEEP":
                self.get_vix_adjustment() # Ensure VIX is fetched
                if SafetyGatekeeper._last_vix > 18.0:
                    logger.info(f">>> [Gatekeeper] ⚡ HIGH VOLATILITY detected (VIX={SafetyGatekeeper._last_vix:.1f} > 18). Bypassing Commodity SLEEP window.")
                else:
                    logger.info(f">>> [Gatekeeper] ⏸️ Commodity SLEEP Window (9AM-1PM). No new entries.")
                    return True

            if state == "COOL_DOWN":
                logger.info(">>> [Gatekeeper] 🧊 MCX COOL_DOWN (Post-10:30 PM). No new entries.")
                return True
            if self.is_past_intraday_cutoff():
                logger.info(f">>> [Gatekeeper] ⏸️ MCX Intraday Cutoff ({self.get_intraday_cutoff()}). No new entries.")
                return True

        return False

    _last_vix = 15.0

    def get_vix_adjustment(self):
        """
        Rule: If India VIX > 25, reduce quantity by 50%.
        Reads VIX from shared market_analysis.json (written by backend every 3 min).
        Falls back to ltpData only if shared file is missing/stale. Result cached 60s.

        Note: India VIX is NIFTY-specific. For MCX commodity instruments we skip this
        adjustment entirely (return 1.0) for sizing, but we still fetch it to measure
        global market volatility for other safety gates.
        """
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        
        # 1. Return cached multiplier if still fresh (60s)
        if time.time() - SafetyGatekeeper._vix_cache_time < 60:
            if instr.asset_type == "COMMODITY":
                return 1.0
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

        if vix > 0:
            SafetyGatekeeper._last_vix = vix

        # 4. Apply rule and cache result — threshold and multiplier from capital tier
        capital    = self.get_current_capital()
        tier       = Config.get_tier(capital)
        multiplier = 1.0
        if vix > tier.vix_reduction_threshold:
            # Only log warning and apply reduction for non-commodities
            if instr.asset_type != "COMMODITY":
                logger.warning(
                    f">>> [Risk] ⚠️ High VIX ({vix:.1f} > {tier.vix_reduction_threshold}). "
                    f"Reducing Quantity by {int((1 - tier.vix_qty_multiplier)*100)}% [{tier.name} tier]."
                )
            multiplier = tier.vix_qty_multiplier

        SafetyGatekeeper._vix_cache_time = time.time()
        SafetyGatekeeper._vix_multiplier = multiplier

        if instr.asset_type == "COMMODITY":
            return 1.0
        return multiplier

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
            
            # Apply state-based scaling for Commodities
            instr = get_instrument(Config.ACTIVE_SYMBOL)
            state_multiplier = 1.0
            if instr.asset_type == "COMMODITY":
                current_state = self.get_market_state()
                if current_state == "WARM_UP":
                    state_multiplier = 0.5 # Play safe during transition
                    logger.info(f">>> [Gatekeeper] 🕯️ WARM_UP Sizing: Scaling lots by {state_multiplier}x")
                elif current_state == "AGGRESSIVE":
                    state_multiplier = 1.0 # Full sizing for Golden Window
                    logger.info(f">>> [Gatekeeper] 🔥 AGGRESSIVE Sizing: Scaling lots by {state_multiplier}x")

            # Apply final multipliers (VIX/Confidence + Market State)
            lots = int(base_lots * multiplier * state_multiplier)

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

    def get_atr_lots(self, risk_amount, atr, multiplier=1.0):
        """
        Master Sheet Sizing: Quantity = Risk / (ATR * Multiplier)
        risk_amount: ₹ amount willing to lose on this trade (from CapitalTier.risk_per_trade_pct)
        atr: current ATR of the underlying instrument
        multiplier: factor to adjust risk aggressiveness
        """
        try:
            if not atr or atr <= 0:
                return 0
                
            instr = get_instrument(Config.ACTIVE_SYMBOL)
            
            # Master Formula
            # We assume Multiplier defaults to 1.0 (Standard ATR sizing)
            # Quantity = ₹Risk / (ATR pts * ₹Value_per_point)
            # For Nifty, 1 pt = 1 * (lot_size / lot_size) = 1? 
            # No, Risk per trade is usually calculated on the entry premium.
            # But ATR sizing usually applies to the underlying.
            
            # institutional standard: 1 ATR move = 1 Risk Unit.
            # qty = Risk / (ATR * lot_size)
            # Actually, for options, Delta matters. But Master Sheet uses ATR as a proxy.
            
            qty = risk_amount / (atr * multiplier)
            lots = int(qty / instr.lot_size)
            
            # Apply tier-based caps
            capital = self.get_current_capital()
            tier = Config.get_tier(capital)
            
            if tier.max_lots > 0:
                lots = min(lots, tier.max_lots)
                
            return max(1, lots)
        except Exception as e:
            logger.error(f"ATR Sizing Error: {e}")
            return 1

    def get_intraday_cutoff(self) -> datetime.time:
        """
        Returns the hard time-exit cutoff for the active instrument.
        Positions must be closed before this time regardless of market hours.

        - INDEX (NIFTY, BANKNIFTY): 15:15 — well before NSE close
        - COMMODITY (CRUDEOIL, GOLD): 23:15 — captures the Golden Window
          and closes before the final pre-midnight illiquid tail.
        """
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        if instr.asset_type == "COMMODITY":
            return datetime.time(23, 15)
        return datetime.time(15, 15)

    def is_past_intraday_cutoff(self) -> bool:
        """Returns True when the active instrument's intraday cutoff has passed."""
        return datetime.datetime.now().time() >= self.get_intraday_cutoff()

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

    def is_in_delivery_period(self) -> bool:
        """
        Rule: For Commodity Options/Futures, block entries 2 days before expiry.
        This avoids the 'Devolve' into physical delivery and the associated margin spikes.
        """
        try:
            instr = get_instrument(Config.ACTIVE_SYMBOL)
            if instr.asset_type != "COMMODITY": return False
            
            # Use Option Expiry (MCX options expire ~2 days before Futures)
            target_day = instr.option_expiry_day_of_month or instr.expiry_day_of_month
            
            # Calculate next expiry date
            if instr.expiry_type == "MONTHLY":
                from bot.utils.expiry_calculator import get_next_monthly_expiry
                expiry_dt = get_next_monthly_expiry(expiry_day_of_month=target_day, raw_date=True)
            else:
                from bot.utils.expiry_calculator import get_next_weekly_expiry
                expiry_dt = get_next_weekly_expiry(target_weekday=instr.expiry_day, raw_date=True)
                
            today = datetime.date.today()
            days_to_expiry = (expiry_dt - today).days
            
            # Expiry Day Safety Window (T-0)
            # We allow trading on T-2 and T-1 for intraday, but block T-0 
            # to avoid thin liquidity and devolvement risk.
            if days_to_expiry < 1:
                return True
            return False
        except Exception as e:
            logger.error(f"Delivery Guard Error: {e}")
            return False
