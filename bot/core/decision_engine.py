from bot.core.safety_checks import SafetyGatekeeper
from backend.market_service import market_service
import datetime
from bot.utils.logger import logger


class DecisionEngine:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.dry_run = dry_run
        self.loader = token_loader
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.MAX_TRADES_PER_DAY = 2    # Hard cap to prevent brokerage drain
        # NOTE: No in-memory counter — we read from DB so the cap survives process restarts

    def analyze_and_select(self):
        """
        Analyzes Funds, Time, and VIX to select the best strategy.
        Returns: (Strategy Name, Risk Multiplier) or (None, 1.0)
        """
        logger.info("\n>>> [Brain] 🧠 Analyzing Market Conditions...")

        # 0. Daily Trade Limit Check (DB-backed — survives process restarts)
        try:
            from bot.core.trade_repo import trade_repo
            mode = "PAPER" if self.dry_run else "LIVE"
            today_trades = trade_repo.get_today_trades(mode=mode)
            trades_today = len(today_trades)
        except Exception:
            today_trades = []
            trades_today = 0  # Fail open — don't block trading on DB error

        if trades_today >= self.MAX_TRADES_PER_DAY:
            logger.warning(f">>> [Brain] 🛑 Daily trade limit reached ({trades_today}/{self.MAX_TRADES_PER_DAY}). No new entries.")
            return None, 1.0

        # 0b. Consecutive Loss Circuit Breaker
        # Halt after 2 consecutive losses — prevents compounding in a bad session
        MAX_CONSECUTIVE_LOSSES = 2
        try:
            closed_today = [t for t in today_trades if t.get('status') == 'CLOSED']
            if len(closed_today) >= MAX_CONSECUTIVE_LOSSES:
                recent = closed_today[-MAX_CONSECUTIVE_LOSSES:]
                all_losses = all(t.get('pnl', 0) < 0 for t in recent)
                if all_losses:
                    total_loss = sum(t.get('pnl', 0) for t in recent)
                    logger.critical(
                        f">>> [Brain] 🛑 CONSECUTIVE LOSS BREAKER: {MAX_CONSECUTIVE_LOSSES} losses in a row "
                        f"(₹{total_loss:.0f}). Halting for the day. Manual reset required."
                    )
                    return None, 1.0
        except Exception:
            pass  # Fail open — don't block on DB error

        # 1. Check Capital & Mode
        available_cash = self.gatekeeper.get_current_capital()
        logger.info(f">>> [Brain] Current available capital: ₹{available_cash:,.2f}")
        is_small_account = available_cash < 15000
        
        if is_small_account:
            logger.info(">>> [Brain] 🍼 SMALL ACCOUNT MODE ACTIVE (Focus on A+ Setups)")

        funds_for_straddle = self.gatekeeper.check_funds(required_margin_per_lot=150000, silent=True)
        funds_for_buying = self.gatekeeper.check_funds(required_margin_per_lot=5000, silent=True)

        if not funds_for_buying:
            logger.warning(f">>> [Brain] ❌ Insufficient Capital for ANY strategy. Available: ₹{available_cash:,.2f} (Need: ~₹5.5k for 1 lot buys).")
            return None, 1.0

        # 2. Check Time
        now = datetime.datetime.now().time()
        logger.info(f">>> [Brain] Current Time: {now}")

        # Rule A: Market Opening (09:15 - 09:20) -> OHL Scalp
        if datetime.time(9, 15) <= now < datetime.time(9, 20):
            logger.info(">>> [Brain] 🌅 Market Opening Phase. Selected: OHL Scalp")
            return "OHL", 1.0

        # 3. Market Regime Analysis
        logger.info(">>> [Brain] 📊 Fetching Market Data from Service Layer...")
        market_data = market_service.get_market_data()
        regime_data = market_data.get('analysis', {})
        regime = regime_data.get('regime', 'UNKNOWN')
        trend = regime_data.get('trend', 'NEUTRAL')
        
        logger.info(f">>> [Brain] Detected Regime: {regime} | Trend: {trend} | ADX: {regime_data.get('adx', 0)}")

        # 4. Sentiment Analysis (OI/PCR)
        sentiment = market_data.get('oi_data', {})
        bias = sentiment.get('bias', 'NEUTRAL')
        logger.info(f">>> [Brain] Option Chain Bias: {bias} (PCR: {sentiment.get('pcr', 0)})")

        # 5. Volatility Scaling (Alpha Optimization)
        risk_multiplier = self.gatekeeper.get_vix_adjustment()
        
        confidence_high = False
        if (trend == "BULLISH" and bias == "BULLISH") or (trend == "BEARISH" and bias == "BEARISH"):
             if regime == "TRENDING":
                 logger.info(">>> [Brain] 💎 High Confidence: Trend & Sentiment Align.")
                 confidence_high = True
                 risk_multiplier *= 1.2

        # 6. Small Account "A+ Filter"
        if is_small_account:
            # Rule: Only take trades if Regime is TRENDING and (Trend aligns with Sentiment OR Trend is Strong)
            adx = regime_data.get('adx', 0)
            if not confidence_high and adx <= 25:
                reason = "Trend-Bias Misalignment" if not confidence_high else "Weak Trend (ADX < 25)"
                logger.warning(f">>> [Brain] ⏸️ Skipping Trade Loop: {reason}. Waiting for A+ Setup.")
                return None, 1.0
            elif not confidence_high and adx > 25:
                logger.info(f">>> [Brain] 🚀 Strong Trend detected (ADX: {adx:.1f}). Overriding Bias misalignment.")

        # 6. Smart Selection Matrix
        selected_strategy = "MOMENTUM" # Default Fallback

        if regime == "VOLATILE":
            logger.warning(">>> [Brain] ⚠️ Market is VOLATILE. Staying in CASH to avoid whipsaws.")
            return None, 1.0

        # Scenario: Trending Market
        if regime == "TRENDING":
            adx = regime_data.get('adx', 0)
            
            # A. ULTRA-HIGH CONFIDENCE (Gamma Blast) -> OTM Exponential Profits
            if adx > 45:
                logger.info(f">>> [Brain] 🚀 PARABOLIC TREND (ADX: {adx:.1f}). Selected: Gamma Blast (OTM Leverage) 💎")
                selected_strategy = "GAMMA_BLAST"

            # B. High Momentum (Super Trend) -> Reactive EMA Crossover
            elif adx > 30:
                logger.info(f">>> [Brain] ⚡ Strong Trend (ADX: {adx:.1f}). Selected: Momentum (Reactive Mode)")
                selected_strategy = "MOMENTUM"

            # B. Early Morning (09:30 - 10:00) -> Range Breakouts
            elif datetime.time(9, 30) <= now < datetime.time(10, 0):
                logger.info(">>> [Brain] 🚀 Early Trend detected. Selected: ORB (Range Breakout)")
                selected_strategy = "ORB"
            
            # C. Post-Stability (10:00+) -> Institutional VWAP
            elif now >= datetime.time(10, 0):
                logger.info(">>> [Brain] 🏛️ Institutional Trend confirmed. Selected: VWAP (Institutional Mode)")
                selected_strategy = "VWAP"
            
        # Scenario: Rangebound / Sideways Market
        elif regime in ["SIDEWAYS", "CHOP"]:
            if funds_for_straddle and bias == "NEUTRAL":
                logger.info(">>> [Brain] 💠 Rangebound Market + Neutral OI. Selected: Straddle (Premium Capture)")
                selected_strategy = "STRADDLE"
            elif bias != "NEUTRAL":
                logger.info(f">>> [Brain] 🎯 Rangebound but OI has {bias} bias. Selected: Inside Bar Scalp")
                selected_strategy = "INSIDE_BAR"
            else:
                logger.info(">>> [Brain] 🕯️ Sideways. Selected: Inside Bar (Limited Risk)")
                selected_strategy = "INSIDE_BAR"

        # FINAL BUDGET CHECK
        MARGIN_MAP = {
            "STRADDLE": 150000,
            "VWAP": 9500,
            "ORB": 5500,
            "MOMENTUM": 5500,
            "OHL": 5500,
            "INSIDE_BAR": 5500
        }

        required = MARGIN_MAP.get(selected_strategy, 5500)
        if not self.gatekeeper.check_funds(required_margin_per_lot=required, silent=True):
            logger.warning(f">>> [Brain] ⚠️ Insufficient Funds for {selected_strategy} (Need ~₹{required}).")
            
            # Fallback Logic
            if selected_strategy == "VWAP":
                logger.info(">>> [Brain] 🔄 Downgrading to MOMENTUM (Cheaper Trend Strategy).")
                selected_strategy = "MOMENTUM"
            elif selected_strategy == "STRADDLE":
                logger.info(">>> [Brain] 🔄 Downgrading to INSIDE_BAR (Cheaper Range/Scalp Strategy).")
                selected_strategy = "INSIDE_BAR"
            else:
                logger.warning(">>> [Brain] ❌ No cheaper strategy available. Aborting.")
                return None, 1.0
        
        return selected_strategy, risk_multiplier

    def record_trade(self):
        """Kept for compatibility. The real gate is now DB-backed in analyze_and_select()."""
        logger.info(f">>> [Brain] 📊 Trade recorded. DB will enforce the {self.MAX_TRADES_PER_DAY}/day limit on next cycle.")

