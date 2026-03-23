from bot.core.safety_checks import SafetyGatekeeper
from backend.market_service import market_service
import datetime
from bot.utils.logger import logger


# Minimum strategy confidence score (0-100) to allow trade entry.
# Based on last 5-day win-rate from the live/paper trade history.
MIN_CONFIDENCE_SCORE = 70.0

# Minimum trades needed before applying the confidence gate (warm-up period).
MIN_TRADES_FOR_CONFIDENCE = 5


class DecisionEngine:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.dry_run = dry_run
        self.loader = token_loader
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.MAX_TRADES_PER_DAY = 3    # Hard cap to prevent brokerage drain
        # NOTE: No in-memory counter — we read from DB so the cap survives process restarts

    def analyze_and_select(self):
        """
        Analyzes Funds, Time, and VIX to select the best strategy.
        Returns: (Strategy Name, Risk Multiplier) or (None, 1.0)
        """
        logger.info("\n>>> [Brain] 🧠 Analyzing Market Conditions...")

        # 0. Global Safety Guards (Strict Enforcement)
        # Rule A: Market Hours (9:15 - 15:29)
        if not self.gatekeeper.is_market_open():
            logger.warning(">>> [Brain] 🛑 Decision Aborted: Market is Closed.")
            return None, 1.0

        # Rule B: Mid-day Blackout (11:30 - 13:00)
        if self.gatekeeper.is_blackout_period():
            logger.info(">>> [Brain] ⏸️ Decision Suspended: System in mid-day Blackout.")
            return None, 1.0

        # Rule C: Max Daily Loss Limit
        if not self.gatekeeper.check_max_daily_loss(active_unrealized_pnl=0.0):
             logger.critical(">>> [Brain] 🛑 Decision Blocked: Max Daily Loss reached.")
             return None, 1.0

        # Rule D: Daily Trade Limit Check (DB-backed)
        try:
            from bot.core.trade_repo import trade_repo
            mode = "PAPER" if self.dry_run else "LIVE"
            today_trades = trade_repo.get_today_trades(mode=mode)
            trades_today = len(today_trades)
        except Exception as e:
            logger.error(f">>> [Brain] DB Error: {e}")
            today_trades = []
            trades_today = 0

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

        # 5. Volatility Scaling & Confidence Analysis (Alpha Optimization)
        risk_multiplier = self.gatekeeper.get_vix_adjustment()
        volume_spike = regime_data.get('volume_spike', False)
        
        confidence_high = False
        # Confluence: Trend + Sentiment + Institutional Volume
        if (trend == "BULLISH" and bias == "BULLISH") or (trend == "BEARISH" and bias == "BEARISH"):
             if regime == "TRENDING" or volume_spike:
                 logger.info(">>> [Brain] 💎 High Confidence: Trend, Sentiment & Volume Align.")
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
            
            # --- PHASE 3: PROXIMITY FILTER (THE WALL CHECK) ---
            levels = market_data.get('levels', {})
            nifty_ltp = market_data.get('nifty', 0)
            
            if levels and nifty_ltp > 0:
                proximity_threshold = nifty_ltp * 0.0015 # 0.15% buffer
                
                # A. BULLISH ENTRY CHECK (Buying into Resistance?)
                if trend == "BULLISH":
                    resistances = [levels.get('pdh'), levels.get('cam_h3'), levels.get('cam_h4')]
                    resistances = [r for r in resistances if r and r > nifty_ltp]
                    
                    for r in resistances:
                        if (r - nifty_ltp) < proximity_threshold:
                            logger.warning(f">>> [Brain] 🛑 PROXIMITY ALERT: Buying too close to Resistance (₹{r:.0f}). entry deferred.")
                            return None, 1.0
                            
                # B. BEARISH ENTRY CHECK (Selling into Support?)
                elif trend == "BEARISH":
                    supports = [levels.get('pdl'), levels.get('cam_l3'), levels.get('cam_l4')]
                    supports = [s for s in supports if s and s < nifty_ltp]
                    
                    for s in supports:
                        if (nifty_ltp - s) < proximity_threshold:
                            logger.warning(f">>> [Brain] 🛑 PROXIMITY ALERT: Selling too close to Support (₹{s:.0f}). entry deferred.")
                            return None, 1.0
            # --------------------------------------------------

            logger.info(f">>> [Brain] 🔍 Evaluating Trending Strategies (ADX: {adx:.1f})...")

            # A. Early Morning (09:30 - 10:00) -> Range Breakouts
            # Priority: ORB is usually more reliable at the open than raw EMA crossover
            if datetime.time(9, 30) <= now < datetime.time(10, 0):
                logger.info(">>> [Brain] 🕒 Morning Range Setup detected. Selected: ORB (Opening Range Breakout)")
                selected_strategy = "ORB"
            
            # B. Post-Stability (10:00+) -> Institutional Trend Check
            elif now >= datetime.time(10, 0):
                # If ADX is extreme, we prefer Momentum/Gamma over VWAP
                if adx > 45:
                    logger.info(f">>> [Brain] 🚀 PARABOLIC TREND DETECTED (ADX: {adx:.1f}). Selected: Gamma Blast 💎")
                    selected_strategy = "GAMMA_BLAST"
                elif adx > 30:
                    logger.info(f">>> [Brain] ⚡ Strong Momentum detected (ADX: {adx:.1f}). Selected: Momentum (Reactive Mode)")
                    selected_strategy = "MOMENTUM"
                else:
                    logger.info(">>> [Brain] 🏛️ Institutional Setup. Selected: VWAP (Institutional Mode)")
                    selected_strategy = "VWAP"
            
            # Fallback for early day before 9:30 if trending
            else:
                 logger.info(">>> [Brain] ⚡ Early Momentum. Selected: Momentum (Reactive Mode)")
                 selected_strategy = "MOMENTUM"
            
        # Scenario: Rangebound / Sideways Market
        elif regime in ["SIDEWAYS", "CHOP"]:
            logger.info(">>> [Brain] 🔍 Evaluating Rangebound Strategies...")
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
        
        # CONFIDENCE GATE (Phase 2): Only proceed if strategy score ≥ 70%
        confidence = self.get_strategy_confidence(selected_strategy)
        if confidence < MIN_CONFIDENCE_SCORE:
            logger.warning(
                f">>> [Brain] ⏸️ Confidence Gate: {selected_strategy} score "
                f"{confidence:.1f} < {MIN_CONFIDENCE_SCORE}. Skipping."
            )
            # Try the 4H regime classifier as secondary validation
            regime_4h = self._classify_4h_regime()
            if regime_4h.get("regime") == "UNKNOWN":
                return None, 1.0
            # If 4H regime still aligns with the strategy, override the gate
            strategy_is_trending = selected_strategy in ("MOMENTUM", "GAMMA_BLAST", "ORB", "VWAP")
            regime_4h_trending   = regime_4h.get("regime") == "TRENDING"
            if strategy_is_trending == regime_4h_trending:
                logger.info(
                    f">>> [Brain] 4H Regime override: {regime_4h.get('regime')} "
                    f"aligns with {selected_strategy}. Proceeding at reduced size."
                )
                risk_multiplier *= 0.5   # Half size when confidence gate overridden
            else:
                return None, 1.0

        return selected_strategy, risk_multiplier

    # ------------------------------------------------------------------ #
    #  Strategy Confidence Score (Phase 2 upgrade)                         #
    # ------------------------------------------------------------------ #

    def get_strategy_confidence(self, strategy_name: str) -> float:
        """
        Computes a 0-100 confidence score for a strategy based on its
        recent performance over the last 5 trading days.

        Score formula:
            win_rate (60%) + avg_rr_score (30%) + trade_frequency_score (10%)

        Returns:
            float: score in [0, 100]. Returns 100.0 (pass-through) when
            insufficient historical data exists (warm-up period).
        """
        try:
            from bot.core.trade_repo import trade_repo
            mode = "PAPER" if self.dry_run else "LIVE"

            # Fetch last 5 days of closed trades for this strategy
            five_days_ago = datetime.datetime.now() - datetime.timedelta(days=5)
            all_trades = trade_repo.get_recent_closed_trades(
                mode=mode, strategy=strategy_name, since=five_days_ago
            )

            if len(all_trades) < MIN_TRADES_FOR_CONFIDENCE:
                # Warm-up: not enough data → allow all strategies
                logger.info(
                    f">>> [Brain] Confidence for {strategy_name}: N/A "
                    f"(only {len(all_trades)} trades, need {MIN_TRADES_FOR_CONFIDENCE}). "
                    "Passing through."
                )
                return 100.0

            wins      = [t for t in all_trades if t.get("pnl", 0) > 0]
            losses    = [t for t in all_trades if t.get("pnl", 0) <= 0]
            win_rate  = len(wins) / len(all_trades) * 100  # 0-100

            # Average Risk-Reward on winning vs losing trades
            avg_win  = sum(t.get("pnl", 0) for t in wins)  / max(len(wins), 1)
            avg_loss = abs(sum(t.get("pnl", 0) for t in losses)) / max(len(losses), 1)
            rr_ratio = avg_win / avg_loss if avg_loss > 0 else 2.0
            # Map rr_ratio to 0-100: rr ≥ 2.0 → 100, rr ≤ 0.5 → 0
            rr_score = min(100.0, max(0.0, (rr_ratio - 0.5) / 1.5 * 100))

            # Trade frequency (penalise strategies with too few signals)
            # 3+ trades/day over 5 days = full score
            freq_score = min(100.0, len(all_trades) / (3 * 5) * 100)

            score = win_rate * 0.60 + rr_score * 0.30 + freq_score * 0.10
            logger.info(
                f">>> [Brain] Confidence[{strategy_name}]: "
                f"Score={score:.1f} | WinRate={win_rate:.1f}% | "
                f"RR={rr_ratio:.2f} | Trades={len(all_trades)}"
            )
            return round(score, 1)

        except Exception as e:
            logger.warning(f">>> [Brain] Confidence score error for {strategy_name}: {e}")
            return 100.0  # Fail open — don't block on DB errors

    def _classify_4h_regime(self) -> dict:
        """
        Classifies the market regime using the last 4 hours (240 1-min candles)
        of NIFTY data from MarketFeedService.

        Returns:
            dict with keys: regime, trend, adx, atr, ema9, ema21
        """
        try:
            from bot.core.market_feed import market_feed
            from bot.core.regime_classifier import RegimeClassifier

            df = market_feed.get_1min_candles("99926000")  # NIFTY spot token
            if df is None or len(df) < 10:
                return {"regime": "UNKNOWN", "trend": "NEUTRAL"}

            # Use only last 240 bars (4 hours of 1-min data)
            df_4h = df.tail(240)
            result = RegimeClassifier().classify(df_4h)
            logger.info(
                f">>> [Brain] 4H Regime: {result.get('regime')} / "
                f"{result.get('trend')} | ADX={result.get('adx', 0):.1f}"
            )
            return result
        except Exception as e:
            logger.warning(f">>> [Brain] 4H Regime classify error: {e}")
            return {"regime": "UNKNOWN", "trend": "NEUTRAL"}

    def record_trade(self):
        """Kept for compatibility. The real gate is now DB-backed in analyze_and_select()."""
        logger.info(f">>> [Brain] 📊 Trade recorded. DB will enforce the {self.MAX_TRADES_PER_DAY}/day limit on next cycle.")

