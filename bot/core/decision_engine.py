from bot.core.safety_checks import SafetyGatekeeper
from backend.market_service import market_service
import datetime
from bot.utils.logger import logger
from bot.utils.expiry_calculator import get_next_weekly_expiry


# Minimum strategy confidence score (0-100) to allow trade entry.
# Based on last 5-day win-rate from the live/paper trade history.
MIN_CONFIDENCE_SCORE = 60.0

# Minimum trades needed before applying the confidence gate (warm-up period).
MIN_TRADES_FOR_CONFIDENCE = 10


class DecisionEngine:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.dry_run = dry_run
        self.loader = token_loader
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        
        # --- THE X-FACTOR: AlphaEngine ---
        from bot.core.alpha_engine import AlphaEngine
        self.alpha_engine = AlphaEngine(self.api, self.loader)
        # MAX_TRADES_PER_DAY is now tier-driven — fetched live in analyze_and_select()
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

        # Rule D: Daily Trade Limit Check (DB-backed, limit from capital tier)
        try:
            from bot.core.trade_repo import trade_repo
            mode = "PAPER" if self.dry_run else "LIVE"
            today_trades = trade_repo.get_today_trades(mode=mode)
            trades_today = len(today_trades)
        except Exception as e:
            logger.error(f">>> [Brain] DB Error: {e}")
            today_trades = []
            trades_today = 0

        # Resolve tier once using already-fetched capital — no extra API call
        from bot.config.settings import Config
        available_cash_early = self.gatekeeper.get_current_capital()
        tier = Config.get_tier(available_cash_early)

        if trades_today >= tier.max_trades_per_day:
            logger.warning(
                f">>> [Brain] 🛑 Daily trade limit reached "
                f"({trades_today}/{tier.max_trades_per_day}) [{tier.name} tier]. No new entries."
            )
            return None, 1.0

        # 0b. Consecutive Loss Circuit Breaker — halts after tier-defined consecutive losses
        MAX_CONSECUTIVE_LOSSES = tier.max_consecutive_losses
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

        # --- SESSION-ADAPTIVE ADX BOOST ---
        # After 2+ consecutive losses, demand a stronger trend before entering again.
        # This prevents over-trading on choppy days where early signals were wrong.
        adx_boost = 0
        try:
            _closed = [t for t in today_trades if t.get('status') == 'CLOSED']
            if len(_closed) >= 2 and all(t.get('pnl', 0) < 0 for t in _closed[-2:]):
                adx_boost = 5
                logger.warning(
                    f">>> [Brain] ⚠️ Session Stress: Last 2 trades both lost. "
                    f"ADX threshold raised by +{adx_boost} (need ADX ≥ {tier.min_adx_to_trade + adx_boost}) "
                    "for remaining entries today."
                )
        except Exception:
            pass

        # 1. Check Capital & Mode  (tier already resolved above, reuse available_cash_early)
        available_cash = available_cash_early
        logger.info(f">>> [Brain] Current available capital: ₹{available_cash:,.2f} [{tier.name} tier]")

        # MICRO/SMALL tier = small account mode (focus on A+ setups only)
        is_small_account = (tier.name in ["MICRO", "SMALL"])
        if is_small_account:
            logger.info(">>> [Brain] 🍼 SMALL ACCOUNT MODE ACTIVE (Focus on A+ Setups)")

        # Minimum buying power check — uses tier's capital floor as the reference
        min_viable_margin = tier.min_capital_threshold * 0.5
        funds_for_buying = self.gatekeeper.check_funds(required_margin_per_lot=min_viable_margin, silent=True)

        if not funds_for_buying:
            logger.warning(
                f">>> [Brain] ❌ Insufficient Capital for ANY strategy. "
                f"Available: ₹{available_cash:,.2f} (Need: ~₹{min_viable_margin:,.0f} for 1 lot [{tier.name}])."
            )
            return None, 1.0

        # 2. Check Time
        now = datetime.datetime.now().time()
        logger.info(f">>> [Brain] Current Time: {now}")


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
        vix_multiplier = self.gatekeeper.get_vix_adjustment()
        volume_spike = regime_data.get('volume_spike', False)
        
        # --- CONFLUENCE SCORING (0–7 points → proportional position sizing) ---
        # Each factor that confirms the trade idea adds points.
        # Final score drives sizing: more confluence = larger position.
        _adx_now = regime_data.get('adx', 0)
        _rsi_now = regime_data.get('rsi', 50.0)
        confluence_score = 0

        # Trend + OI Bias alignment — the single strongest confirmation (+2)
        if (trend == "BULLISH" and bias == "BULLISH") or (trend == "BEARISH" and bias == "BEARISH"):
            confluence_score += 2

        # Market regime confirms a trending environment (+2)
        if regime == "TRENDING":
            confluence_score += 2

        # Institutional volume spike — smart money participating (+1)
        if volume_spike:
            confluence_score += 1

        # RSI is in the "sweet spot" — momentum confirmed, not yet overextended (+1)
        # Bullish: RSI between 45–70 | Bearish: RSI between 30–55
        if (trend == "BULLISH" and 45 < _rsi_now < 70) or (trend == "BEARISH" and 30 < _rsi_now < 55):
            confluence_score += 1

        # ADX strength is parabolic — extremely strong directional move (+1)
        if _adx_now > tier.adx_gamma_blast:
            confluence_score += 1

        confidence_high = (confluence_score >= 5)

        # Map score to scaling factor (4 discrete tiers for clean lot arithmetic)
        if confluence_score >= 6:
            scaling_factor = 1.0    # A+ setup — full size (4-5 lots)
        elif confluence_score >= 5:
            scaling_factor = 0.5    # Good setup — half size (2-3 lots)
        else:
            scaling_factor = 0.2    # Low Confidence — Minimum size (1 lot)

        logger.info(
            f">>> [Brain] 🎯 Confluence: {confluence_score}/7 | "
            f"Scale={scaling_factor}x | Confidence={'HIGH' if confidence_high else 'NORMAL' if confluence_score >= 3 else 'LOW'}"
        )
        risk_multiplier = scaling_factor * vix_multiplier

        # 6. Small Account "A+ Filter" (MICRO/SMALL tier only)
        if is_small_account:
            adx = regime_data.get('adx', 0)
            is_misaligned = (trend == "BULLISH" and bias == "BEARISH") or (trend == "BEARISH" and bias == "BULLISH")
            
            if not confidence_high and adx <= tier.min_adx_to_trade + adx_boost:
                # EXCEPTION: Allow low-ADX chop regimes through so Straddle Scalp can be evaluated
                if regime in ["SIDEWAYS", "CHOP"] and adx < 24:
                    logger.info(">>> [Brain] Small Account: Allowing sideways/chop setup through A+ filter for potential Scalp/Selling.")
                else:
                    reasons = []
                    if is_misaligned:
                        reasons.append(f"Trend-OI Misalignment ({trend} trend vs {bias} OI)")
                    else:
                        reasons.append(f"Low Confluence ({confluence_score}/7)")
                    
                    reasons.append(f"Weak ADX ({adx:.1f} < {tier.min_adx_to_trade + adx_boost} [{tier.name}])")
                    logger.warning(f">>> [Brain] ⏸️ Skipping — {' + '.join(reasons)}. Waiting for A+ Setup.")
                    return None, 1.0
            elif not confidence_high and adx > tier.min_adx_to_trade + adx_boost:
                if is_misaligned:
                    logger.info(f">>> [Brain] 🚀 Strong Trend detected (ADX: {adx:.1f}). Overriding Bias misalignment.")
                else:
                    logger.info(f">>> [Brain] 🚀 Strong Trend detected (ADX: {adx:.1f}). Overriding Confluence.")

        # ── HARD ADX GATE ─────────────────────────────────────────────────────────
        # No trade unless trend is strong enough for the current capital tier.
        # Larger accounts tolerate lower ADX; small accounts need strong trends only.
        # EXCEPTION: Sideways/Chop regimes skip this gate to allow Straddle Scalps.
        adx = regime_data.get('adx', 0)
        is_trending_request = (regime == "TRENDING") or (adx > 20)
        
        if adx < tier.min_adx_to_trade + adx_boost and regime not in ["SIDEWAYS", "CHOP"]:
            logger.info(
                f">>> [Brain] ⏸️ ADX GATE [{tier.name}]: ADX={adx:.1f} < "
                f"{tier.min_adx_to_trade + adx_boost} minimum"
                + (f" (base {tier.min_adx_to_trade} + session boost {adx_boost})" if adx_boost else "")
                + ". Skipping Trend strategy — waiting for momentum."
            )
            return None, 1.0
        # 6. Hybrid Strategy Switcher (The "At Any Cost" Logic)
        today_str = datetime.datetime.now().strftime("%d%b%Y").upper()
        expiry_calc = get_next_weekly_expiry()
        is_expiry_day = (expiry_calc == today_str)
        is_afternoon  = (datetime.datetime.now().time() >= datetime.time(13, 0))
        is_morning    = (datetime.datetime.now().time() < datetime.time(10, 30))
        is_power_hour = (datetime.time(13, 15) <= datetime.datetime.now().time() <= datetime.time(15, 0))
        
        # --- X-FACTOR: Institutional Panic Check (AlphaEngine) ---
        # Fetch ATM strike for NIFTY to check OI Velocity
        nifty_ltp = market_data.get('nifty', 0)
        panic_data = {"panic_score": 50, "confidence": "NEUTRAL"}
        if nifty_ltp > 0:
            atm_strike = round(nifty_ltp / 50) * 50
            panic_data = self.alpha_engine.analyze_panic(expiry_calc, atm_strike)
            
            # Confidence multiplier from AlphaEngine
            alpha_multiplier = self.alpha_engine.get_confidence_multiplier(panic_data)
            risk_multiplier *= alpha_multiplier
            logger.info(f">>> [Brain] AlphaEngine Multiplier: {alpha_multiplier}x (Confidence: {panic_data.get('confidence')})")

        # 6. Hybrid Strategy Switcher (Time + Regime + Panic)
        adx = regime_data.get('adx', 0)
        
        # RECOVERY MODE
        if adx_boost > 0:
            logger.warning(">>> [Brain] 🛡️ RECOVERY MODE ACTIVE: Reducing risk multiplier by 50%.")
            risk_multiplier *= 0.5

        # ── SNIPER OVERRIDE: GEOMETRIC BREAKOUT DETECTION ─────────────────────
        # If in the Afternoon Power Hour, and price pierces previous HOD/LOD
        # with an explicit Institutional Volume Spike, trigger GammaBlast 
        # IMMEDIATELY, bypassing the 15-30 minute ADX lag.
        hod = regime_data.get('hod', 0)
        lod = regime_data.get('lod', 0)
        volume_spike = regime_data.get('volume_spike', False)
        is_breakout = False
        
        if is_afternoon and volume_spike and nifty_ltp > 0:
            if hod > 0 and nifty_ltp > hod:
                is_breakout = True
                logger.info(f"🎯 [SNIPER] HOD BREAKOUT DETECTED! (Spot {nifty_ltp:.1f} > Ceiling {hod:.1f} with Volume Spike)")
            elif lod > 0 and nifty_ltp < lod:
                is_breakout = True
                logger.info(f"🎯 [SNIPER] LOD BREAKDOWN DETECTED! (Spot {nifty_ltp:.1f} < Floor {lod:.1f} with Volume Spike)")

        # ── STRATEGY SELECTION MATRIX ────────────────────────────────────────
        if panic_data.get('panic_score', 50) >= 80:
            # INSTITUTIONAL PANIC DETECTED -> Prioritize GAMMA regardless of day
            logger.info(f"🔥 [X-FACTOR] PANIC DETECTED ({panic_data.get('reason')}). Launching Alpha Strike.")
            selected_strategy = "GAMMA_BLAST"
        
        elif is_breakout:
            # GEOMETRIC VOLUME BREAKOUT DETECTED -> Enter immediately on explosion
            logger.info(f"🚀 [SNIPER ACTIVATE] Explosive Volume Breakout confirmed. Overriding ADX Lag -> Selecting GAMMA_BLAST")
            selected_strategy = "GAMMA_BLAST"

        elif adx >= tier.adx_gamma_blast:
            # PARABOLIC TREND -> Use Gamma Blast on ANY day
            logger.info(f"🚀 PARABOLIC MOVE (ADX: {adx:.1f} >= {tier.adx_gamma_blast}). Selected: GAMMA_BLAST")
            selected_strategy = "GAMMA_BLAST"

        elif adx > 25:
            # NORMAL TRENDING
            selected_strategy = "MOMENTUM"
        
        elif is_morning and adx < 20:
            # Morning Sideways -> Straddle Scalp
            selected_strategy = "STRADDLE_SCALP"
            
        elif adx < 18:
            # Range-Bound Regime
            selected_strategy = "SELLING"
        else:
            # Transition Phase
            selected_strategy = "STRADDLE_SCALP"
        
        # ── VOLATILITY DEADZONE OVERRIDE ─────────────────────────────────────
        # Explosive options buying relies on volatility 'fuel'. If VIX is historically
        # depressed, explosive moves frequently fail to sustain.
        # Downgrade to lower-leverage Momentum.
        vix = float(market_data.get('vix', 15.0) or 15.0)
        MIN_VIX_FOR_GAMMA = 11.5
        
        if selected_strategy == "GAMMA_BLAST" and vix < MIN_VIX_FOR_GAMMA:
            logger.warning(
                f">>> [Brain] 🛡️ VIX DEADZONE DETECTED ({vix:.1f} < {MIN_VIX_FOR_GAMMA}). "
                f"Insufficient volatility fuel for Gamma Blast. Downgrading to MOMENTUM."
            )
            selected_strategy = "MOMENTUM"

        # Whitelist guard — strategy must be enabled for this tier
        if selected_strategy not in tier.allowed_strategies:
            logger.info(
                f">>> [Brain] ⏸️ {selected_strategy} not in [{tier.name}] whitelist "
                f"{tier.allowed_strategies}. Staying in CASH."
            )
            return None, 1.0

        # BUDGET CHECK — minimum viable margin for 1 lot (tier-aware)
        required = tier.min_capital_threshold * 0.5
        if not self.gatekeeper.check_funds(required_margin_per_lot=required, silent=True):
            logger.warning(
                f">>> [Brain] ❌ Insufficient funds for {selected_strategy} [{tier.name}] "
                f"(Need ~₹{required:,.0f})."
            )
            return None, 1.0
        
        # CONFIDENCE GATE (Phase 2): Only proceed if strategy score ≥ 60%
        confidence = self.get_strategy_confidence(selected_strategy)
        if confidence < MIN_CONFIDENCE_SCORE:
            logger.warning(
                f">>> [Brain] ⏸️ Confidence Gate: {selected_strategy} score "
                f"{confidence:.1f} < {MIN_CONFIDENCE_SCORE}. Skipping."
            )
            
            # 1. Extreme Trend Override (ADX > 35)
            if adx >= 35.0:
                 logger.info(f">>> [Brain] 🚀 Confidence Override: ADX is extreme ({adx:.1f}). Proceeding at reduced size.")
                 risk_multiplier *= 0.5
                 return selected_strategy, risk_multiplier

            # 2. Try the 4H regime classifier as secondary validation
            regime_4h = self._classify_4h_regime()
            if regime_4h.get("regime") == "UNKNOWN":
                return None, 1.0
            
            # If 4H regime still aligns with the strategy, override the gate
            strategy_is_trending = selected_strategy in ("MOMENTUM", "GAMMA_BLAST", "STRADDLE_SCALP")
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
        logger.info(">>> [Brain] 📊 Trade recorded. DB will enforce the tier daily limit on next cycle.")

