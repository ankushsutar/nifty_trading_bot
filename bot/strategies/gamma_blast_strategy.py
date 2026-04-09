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
        self.active_position = None
        self.last_sync_time = 0
        self.risk_multiplier = 1.0        # Set by DecisionEngine before execute()
        self.last_trend_fade_check = 0    # Throttle market_service calls in monitor
        self._last_sl_hit_time = 0        # Timestamp of last SL hit — gates re-entry
        self._last_status_log  = 0        # Throttle for periodic monitor heartbeat

    def sync_state(self):
        """
        Synchronizes active position from Broker API.
        Looks for the FIRST active NIFTY Intraday position that matches Gamma Blast style (OTM).
        """
        if self.dry_run:
            if self.active_position is None:
                db_trade = trade_repo.get_active_trade(mode="PAPER", strategy="GAMMA_BLAST")
                if db_trade:
                    self.active_position = {
                        'id': db_trade['id'],
                        'leg': db_trade['leg'],
                        'symbol': db_trade['symbol'],
                        'token': db_trade['token'],
                        'qty': db_trade['qty'],
                        'entry_price': db_trade['entry_price'],
                        'sl_price': db_trade['sl_price'],
                        'sl_order_id': None
                    }
                    logger.info(f"♻️ [Gamma Blast] PAPER RECOVERY: Found Active Trade in DB! {db_trade['symbol']}")
            return

        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            pos_resp = self.order_manager.get_positions()
            
            # transients (DNS, timeout) return None or False status
            if pos_resp is None or not pos_resp.get('status'):
                logger.warning("⚠️ [Gamma Blast] Sync State: API failure. Skipping sync to preserve local state.")
                return

            # If we reach here, the API call was successful
            found_active = None
            pos_data = pos_resp.get('data') or []
            
            for pos in pos_data:
                # Look for NIFTY Intraday options with non-zero quantity
                if (pos.get('symbolname') == 'NIFTY' and 
                    pos.get('producttype') == 'INTRADAY' and 
                    int(pos.get('netqty', 0)) != 0):
                    
                    qty = int(pos['netqty'])
                    
                    found_active = {
                        'leg': "CE" if "CE" in pos['tradingsymbol'] else "PE", 
                        'symbol': pos['tradingsymbol'],
                        'token': pos['symboltoken'],
                        'qty': abs(qty),
                        'entry_price': float(pos['avgnetprice']),
                        # If no local sl_price, default to 20% stop
                        'sl_price': float(pos['avgnetprice']) * 0.8
                    }
                    
                    # Match with DB record to get correct sl_price if available
                    db_trade = trade_repo.get_active_trade(mode="LIVE", strategy="GAMMA_BLAST", symbol=found_active['symbol'])
                    if db_trade:
                        found_active['id'] = db_trade['id']
                        found_active['sl_price'] = db_trade.get('sl_price', found_active['sl_price'])
                        logger.info(f"♻️ [Gamma Blast] RECOVERY: Linked to DB Trade #{db_trade['id']}")
                    
                    if self.active_position is None:
                        logger.info(f"♻️ [Gamma Blast] RECOVERY: Found Active Trade on Broker! {found_active['symbol']}")
                    
                    break 
            
            if found_active:
                self.active_position = found_active
            elif self.active_position is not None:
                logger.warning("⚠️ [Gamma Blast] SYNC: Active Position closed externally! Resetting State.")
                trade_repo.close_trade(symbol=self.active_position['symbol'])
                self.active_position = None
                    
        except Exception as e:
            logger.error(f"[Gamma Blast] Sync State Error: {e}")

    def execute(self, expiry, action="BUY"):
        logger.info(f"🚀 --- GAMMA BLAST OTM STRATEGY ACTIVATED ({expiry}) ---")
        
        while self.running:
            # 1.5 Global Safety Guards
            if not self.gatekeeper.is_market_open():
                logger.warning("Gamma Blast: 🛑 Execution Aborted - Market is Closed.")
                break
            if self.gatekeeper.is_blackout_period():
                logger.info("Gamma Blast: ⏸️ Execution Suspended - Mid-day Blackout.")
                time.sleep(60)
                continue
            if not self.gatekeeper.check_max_daily_loss(0.0):
                logger.critical("Gamma Blast: 🛑 Execution Blocked - Max Daily Loss reached.")
                break

            # Post-SL cooldown: wait 5 min before re-entering after a stop-loss hit.
            # Prevents revenge trading on bounces and ensures OI data refreshes.
            _SL_COOLDOWN_SECS = 300
            _secs_since_sl = time.time() - self._last_sl_hit_time
            if self._last_sl_hit_time > 0 and _secs_since_sl < _SL_COOLDOWN_SECS:
                _remaining = int(_SL_COOLDOWN_SECS - _secs_since_sl)
                logger.info(f"Gamma Blast: ⏳ Post-SL cooldown — {_remaining}s remaining before next entry.")
                time.sleep(30)
                continue

            # 1. Check for Resumption (DB check)
            mode = "PAPER" if self.dry_run else "LIVE"
            active_trade = trade_repo.get_active_trade(mode=mode, strategy="GAMMA_BLAST")

            if active_trade:
                # If the trade is still pending fill from a previous crash/timeout, entry_price might be 0.0
                if active_trade.get('entry_price', 0.0) == 0.0:
                    logger.warning(f">>> [Resumption] Found Ghost Trade (entry=0.0): {active_trade['symbol']}. Closing.")
                    trade_repo.collection.delete_one({"id": active_trade['id']})
                    # Do not return; continue loop to look for new signals
                    continue
                
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
                break # Monitoring finished or trade closed

            # 2. Market analysis & Final Confirmation
            # (Though DecisionEngine already checked, we double check local indicators)
            from backend.market_service import market_service
            market_data = market_service.get_market_data()
            analysis = market_data.get('analysis', {})

            if not analysis or analysis.get('regime') == 'UNKNOWN':
                logger.error("Gamma Blast: Market analysis unavailable. Fallback to safety check.")
                # Final fallback to direct fetch only if market_service is failing
                df = self.data_fetcher.fetch_latest_candles("99926000", interval="FIVE_MINUTE")
                if df is None or len(df) < 20:
                    time.sleep(30)
                    continue
                ltp = df.iloc[-1]['close']
                adx = self.calculate_adx(df).iloc[-1]
                ema9 = df['close'].ewm(span=9, adjust=False).mean().iloc[-1]
                ema21 = df['close'].ewm(span=21, adjust=False).mean().iloc[-1]
            else:
                ltp = market_data.get('nifty', 0)
                adx = analysis.get('adx', 0)
                ema9 = analysis.get('ema9', 0)
                ema21 = analysis.get('ema21', 0)
                logger.info(f"Gamma Blast: Using Shared Analysis (ADX: {adx:.1f} | Regime: {analysis.get('regime')})")

            if not ltp or ltp <= 0:
                logger.warning("Gamma Blast: NIFTY LTP is 0 or unavailable. Skipping.")
                time.sleep(30)
                continue

            if not ema9 or not ema21:
                logger.warning(f"Gamma Blast: EMA data unavailable (ema9={ema9}, ema21={ema21}). Skipping.")
                time.sleep(30)
                continue

            # ADX gate from capital tier — no hardcoded threshold
            from bot.config.settings import Config as _Cfg
            _capital = self.gatekeeper.get_current_capital()
            _tier = _Cfg.get_tier(_capital)
            if adx < _tier.min_adx_to_trade:
                logger.warning(
                    f"Gamma Blast: Trend strength (ADX: {adx:.1f}) below "
                    f"[{_tier.name}] threshold ({_tier.min_adx_to_trade}). Aborting."
                )
                time.sleep(30)
                continue

            # 3. Determine Leg (Trend Direction)
            leg = "CE" if ema9 > ema21 else "PE"

            # --- OI BIAS CONFIRMATION ---
            # Block entry if institutional OI flow contradicts the EMA-derived leg.
            # On a parabolic day, we want trend AND institutions aligned.
            oi_data = market_data.get('oi_data', {})
            oi_bias = oi_data.get('bias', 'NEUTRAL')
            if (leg == "CE" and oi_bias == "BEARISH") or (leg == "PE" and oi_bias == "BULLISH"):
                logger.warning(
                    f"Gamma Blast: ⚠️ OI Bias Conflict — Leg={leg} but institutions say {oi_bias}. "
                    "Skipping entry to avoid trading against smart money."
                )
                time.sleep(30)
                continue

            # 4. Strike Selection — OTM depth scales with ADX strength.
            # Stronger trend = deeper OTM = exponentially higher leverage.
            #   ADX 35–50 → 1 OTM (delta ~0.35, moderate leverage, safer)
            #   ADX 50–55 → 2 OTM (delta ~0.20, high leverage)
            #   ADX > 55  → 3 OTM (delta ~0.10, maximum leverage, parabolic days only)
            atm_strike = round(ltp / 50) * 50
            if adx < 50:
                otm_depth = 1
            elif adx < 55:
                otm_depth = 2
            else:
                otm_depth = 3

            # --- IV RANK: OTM DEPTH CAP ---
            # When options are expensive (IV Rank > 70%), avoid going too deep OTM —
            # a fat premium on a low-delta strike needs a huge move to break even.
            # Floor at 1 OTM (never go ATM for gamma blast — it's a leverage strategy).
            iv_rank = self.gatekeeper.get_iv_rank()
            if iv_rank > 0.70 and otm_depth > 1:
                otm_depth = max(1, otm_depth - 1)
                logger.info(
                    f"📉 IV Rank={iv_rank:.0%}: Options expensive, "
                    f"capping OTM depth to {otm_depth} strike(s) to avoid premium trap."
                )

            strike = atm_strike + (otm_depth * 50 * (1 if leg == "CE" else -1))

            logger.info(
                f"🎯 Analysis: ADX={adx:.1f} | Leg={leg} | "
                f"OTM depth={otm_depth} strikes | Strike={strike}"
            )

            token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg)
            if not token:
                logger.error(f"Gamma Blast: Token not found for {strike} {leg}")
                time.sleep(30)
                continue

            # Fetch Option LTP for early record and price estimate
            quote_ltp = self.data_fetcher.get_ltp(token, exchange="NFO") or 50.0

            # 5. Position Sizing — lot fraction from capital tier (MICRO=50%, SMALL=60%, etc.)
            # Reuse _tier already resolved above — no extra API call needed.
            margin_per_lot = (quote_ltp * Config.NIFTY_LOT_SIZE) if quote_ltp > 0 else (_tier.min_capital_threshold * 0.5)
            # Lots are scaled by the Brain's risk multiplier AND the strategy's specific lot fraction
            lots = int(self.gatekeeper.get_compounded_lots(margin_per_lot=margin_per_lot, multiplier=self.risk_multiplier) * _tier.gamma_blast_lot_pct)
            if lots < 1:
                # Only force 1 lot if capital can actually cover a single lot
                estimated_cost = margin_per_lot
                if self.gatekeeper.check_trade_margin(estimated_cost, silent=True):
                    lots = 1
                else:
                    logger.warning(f"Gamma Blast: ❌ Insufficient capital for 1 lot (₹{estimated_cost:,.0f} required). Skipping.")
                    time.sleep(60)
                    continue
            qty = lots * Config.NIFTY_LOT_SIZE

            # --- TRADE VIABILITY CHECK ---
            # Ensure brokerage (₹60 round-trip) doesn't exceed 15% of trade value.
            # Catches cheap deep-OTM options where costs eat the profit.
            if not self.gatekeeper.check_trade_viability(quote_ltp, qty):
                logger.warning("Gamma Blast: ❌ Trade viability check failed (brokerage ratio too high). Skipping.")
                time.sleep(60)
                continue

            self.place_entry(expiry, strike, leg, qty, quote_ltp)

            # If we didn't enter or monitoring finished, loop again after sleep
            time.sleep(30) # Throttle loop

    def place_entry(self, expiry, strike, leg, qty, quote_ltp):
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg)
        if not token:
            logger.error(f"Gamma Blast: Token not found for {strike} {leg}")
            return

        # Hard margin check before touching the broker API
        estimated_cost = quote_ltp * qty
        if not self.gatekeeper.check_trade_margin(estimated_cost):
            logger.warning(f"Gamma Blast: ❌ Margin check failed. Need ₹{estimated_cost:,.0f}. Aborting entry.")
            return

        # Place Smart-Limit Order — slippage buffer from capital tier config.
        # Smaller accounts (MICRO/SMALL) use a tighter buffer; avoids over-paying for OTM options.
        from bot.config.settings import Config as _Cfg
        _entry_tier = _Cfg.get_tier(self.gatekeeper.get_current_capital())
        limit_price = round(quote_ltp * (1.0 + _entry_tier.entry_slippage_pct), 1)
        
        logger.info(f">>> [Trade] Entering {symbol} (Qty: {qty}) via Smart-Limit @ ₹{limit_price}")
        
        oid = self.order_manager.place_smart_limit(
            symbol, token, qty, limit_price, 
            transaction_type="BUY", 
            strategy_name="GAMMA_BLAST"
        )
        if not oid: return

        # 2. Wait for fill (WebSocket or REST fallback)
        fill_result = self.wait_for_fill(oid)
        
        # FINAL REST FALLBACK IF TIMEOUT: The order might have filled right as timeout hit
        if fill_result['status'] == 'TIMEOUT' and not self.dry_run:
            logger.info(f"Gamma Blast: ⏳ Order {oid} timed out. Doing one final REST API check before aborting...")
            try:
                ob_res = self.api.orderBook()
                if ob_res and ob_res.get('status'):
                    for ord_info in ob_res.get('data', []):
                        if ord_info.get('orderid') == oid:
                            rest_status = ord_info.get('status', '').lower()
                            if rest_status == 'complete':
                                logger.info(f"Gamma Blast: ✅ Order {oid} actually FILLED on REST check!")
                                try:
                                    avg_price = float(ord_info.get('averageprice') or 0)
                                except (ValueError, TypeError):
                                    avg_price = 0.0
                                fill_result = {'status': 'FILLED', 'price': avg_price}
                            break
            except Exception as e:
                logger.error(f"Gamma Blast Final REST Check Error: {e}")

        if fill_result['status'] != 'FILLED':
            logger.warning(f"Gamma Blast: Entry failed or timed out permanently. Status: {fill_result['status']}")
            
            cancel_success = True
            if fill_result['status'] == 'TIMEOUT':
                cancel_success = self.order_manager.cancel_order(oid, variety="NORMAL")
                
            if cancel_success or fill_result['status'] in ['REJECTED', 'CANCELLED']:
                # CRITICAL FIX: Delete the zombie database record if entry failed and was cancelled
                failed_trade = trade_repo.get_active_trade(strategy="GAMMA_BLAST", symbol=symbol)
                if failed_trade:
                    trade_repo.collection.delete_one({"id": failed_trade['id']})
                    logger.info(f"Gamma Blast: Cleaned up failed entry record #{failed_trade['id']} from database.")
                return
            else:
                logger.critical(f"Gamma Blast: 🚨 DANGER! Order {oid} timed out, but CANCEL FAILED! It might be filling! Transitioning to monitor mode just in case.")
                fill_result = {'status': 'FILLED', 'price': limit_price} # Assume limit price fill to survive
                
        fill_price = fill_result['price']

        # Guard: if fill_price is 0 (bad REST data), fall back to limit_price
        if not fill_price or fill_price <= 0:
            logger.warning(f"Gamma Blast: fill_price is 0 — using limit_price {limit_price} as fallback.")
            fill_price = limit_price

        # 3. Update Trade with Actual Fill & Mark OPEN — SL% from capital tier
        from bot.config.settings import Config as _Cfg
        _tier = _Cfg.get_tier(self.gatekeeper.get_current_capital())
        sl_price = round(fill_price * (1 - _tier.sl_pct), 1)

        # Link and Update DB Record
        trade_id = self.order_manager.update_trade_fill(symbol, "GAMMA_BLAST", fill_price, expected_price=quote_ltp)
        if trade_id:
            trade_repo.update_sl(trade_id, sl_price)
        else:
            logger.warning("Gamma Blast: Could not link fill to DB record. Status might be out of sync.")

        if not trade_id:
            logger.critical(f"Gamma Blast: 🚨 trade_id is None after fill! Cannot track trade safely. Exiting position.")
            exit_params = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO",
                "ordertype": "MARKET", "price": 0,
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
            self.order_manager.place_order(exit_params)
            return

        # Place Broker SL
        sl_oid = self.order_manager.place_sl_order(symbol, token, qty, sl_price, leg)

        self.monitor_position(symbol, token, qty, sl_price, fill_price, trade_id, sl_oid, leg)

    def monitor_position(self, symbol, token, qty, sl, entry_price, trade_id, sl_oid, leg):
        """
        Progressive 3-stage trailing exit — replaces fixed 3:1 target.

        Stage 0 → 1  (ltp ≥ entry + 1R): SL → breakeven.  Trail: 1.0R below LTP.
        Stage 1 → 2  (ltp ≥ entry + 2R): Book 50% at market. SL → entry+0.5R. Trail: 0.75R.
        Stage 2 → 3  (ltp ≥ entry + 3R): Tighten trail to 0.50R. Let the trend run.

        No fixed profit target. The trailing SL decides when the move is over.
        """
        risk          = abs(entry_price - sl)   # Initial risk distance — reference point
        stage         = 0                        # 0→initial  1→breakeven  2→half_booked  3→tight_trail
        remaining_qty = qty                      # Shrinks after partial booking

        logger.info(
            f"Gamma Blast: 🎯 PROGRESSIVE TRAIL | "
            f"Entry={entry_price} | SL={sl} | Risk={risk:.1f}pts | Qty={qty}"
        )

        while self.running:
            try:
                # ── Periodic broker sync ──────────────────────────────────
                if not self.dry_run and time.time() - self.last_sync_time > 15:
                    self.sync_state()
                    self.last_sync_time = time.time()
                    if not self.active_position:
                        logger.warning("Gamma Blast: Sync found no active position. Stopping monitor.")
                        break

                time.sleep(0.5)
                ltp = self.data_fetcher.get_ltp(token, exchange="NFO")
                if not ltp:
                    continue

                # ── Global kill switch ────────────────────────────────────
                unrealized_pnl = (ltp - entry_price) * remaining_qty
                if not self.gatekeeper.check_max_daily_loss(unrealized_pnl):
                    logger.critical("🛑 EMERGENCY: Account Daily Loss Limit Breach in Gamma Blast!")
                    self.exit_market(token, symbol, remaining_qty, "MAX_DAILY_LOSS", trade_id, sl_oid)
                    break

                # ── Trend-fade check + status heartbeat (throttled to every 30s) ──
                # market_service data refreshes every ~3 min — polling faster wastes resources.
                if time.time() - self.last_trend_fade_check > 30:
                    self.last_trend_fade_check = time.time()
                    from backend.market_service import market_service
                    from bot.config.settings import Config as _Cfg
                    analysis = market_service.get_market_data().get('analysis', {})
                    curr_adx = analysis.get('adx', 0)
                    _tier    = _Cfg.get_tier(self.gatekeeper.get_current_capital())
                    if curr_adx > 0 and curr_adx < _tier.adx_trend_fade_exit:
                        logger.info(
                            f"Gamma Blast: ⚠️ Trend Fading (ADX={curr_adx:.1f} < {_tier.adx_trend_fade_exit} "
                            f"[{_tier.name}]). Exiting {remaining_qty} qty."
                        )
                        if self.exit_market(token, symbol, remaining_qty, "TREND_FADE", trade_id, sl_oid):
                            sl_oid = None
                            self.active_position = None
                            break
                        continue

                    # ── 30s status heartbeat ──────────────────────────────────────
                    _be_mult  = 0.5 if (qty >= 4 * Config.NIFTY_LOT_SIZE) else 1.0
                    _pnl      = round((ltp - entry_price) * remaining_qty, 2)
                    _pnl_pct  = round((_pnl / (entry_price * remaining_qty)) * 100, 2) if entry_price > 0 else 0

                    if stage == 0:
                        _next_label = f"BE trigger"
                        _next_price = round(entry_price + _be_mult * risk, 1)
                    elif stage == 1:
                        _next_label = f"2R partial-book"
                        _next_price = round(entry_price + 2 * risk, 1)
                    elif stage == 2:
                        _next_label = f"3R tight-trail"
                        _next_price = round(entry_price + 3 * risk, 1)
                    else:
                        _next_label = "Tight trail active"
                        _next_price = None

                    _next_str = (
                        f"{_next_label} @ ₹{_next_price} "
                        f"({abs(_next_price - ltp):.1f}pts {'away' if _next_price > ltp else 'PASSED'})"
                        if _next_price else _next_label
                    )

                    logger.info(
                        f"Gamma Blast: 📊 MONITOR | LTP=₹{ltp:.1f} | Entry=₹{entry_price} | "
                        f"SL=₹{sl:.1f} | Qty={remaining_qty} | Stage={stage} | "
                        f"P&L=₹{_pnl:+,.0f} ({_pnl_pct:+.1f}%) | Next: {_next_str}"
                    )

                # ── Stage 1: Breakeven at 1R (or 0.5R for high qty) ───
                be_trigger_mult = 0.5 if (qty >= 4 * Config.NIFTY_LOT_SIZE) else 1.0
                if stage < 1 and ltp >= entry_price + (be_trigger_mult * risk):
                    logger.info(f"Gamma Blast: 🛡️ Stage 1 ({be_trigger_mult}R). SL → Breakeven ({entry_price})")
                    sl    = entry_price
                    stage = 1
                    trade_repo.update_sl(trade_id, sl)
                    if sl_oid and not self.dry_run:
                        self.order_manager.modify_sl_order(sl_oid, sl, symbol, token, remaining_qty)

                # ── Stage 2: Book 50% at 2R ───────────────────────────────
                if stage < 2 and ltp >= entry_price + 2 * risk:
                    lot_size  = Config.NIFTY_LOT_SIZE
                    half_lots = max(0, (remaining_qty // lot_size) // 2)
                    half_qty  = half_lots * lot_size

                    if half_qty >= lot_size and remaining_qty > lot_size:
                        partial_pnl = round((ltp - entry_price) * half_qty, 2)
                        logger.info(
                            f"💰 Gamma Blast: Stage 2 (2R). Booking {half_qty} qty @ ₹{ltp:.1f} "
                            f"(locked P&L: ₹{partial_pnl:+,.0f}). "
                            f"Remaining {remaining_qty - half_qty} qty runs free."
                        )
                        partial_confirmed = self.dry_run  # dry_run always confirms
                        if not self.dry_run:
                            partial_limit = round(ltp * 0.98, 1)
                            partial_params = {
                                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                                "transactiontype": "SELL", "exchange": "NFO",
                                "ordertype": "LIMIT", "price": partial_limit,
                                "producttype": "INTRADAY", "duration": "DAY", "quantity": half_qty,
                            }
                            p_oid = self.order_manager.place_order(partial_params)
                            if p_oid:
                                fill = self.wait_for_fill(p_oid)
                                if fill['status'] == 'FILLED':
                                    partial_pnl = round((fill['price'] - entry_price) * half_qty, 2)
                                    partial_confirmed = True
                                else:
                                    logger.warning(
                                        f"Gamma Blast: ⚠️ Partial booking order {p_oid} not filled "
                                        f"(status={fill['status']}). Keeping full position active."
                                    )

                        # Only update state if the broker actually filled the partial sell.
                        # Avoids desync where local state shows half-position but broker holds full.
                        if partial_confirmed:
                            trade_repo.reduce_position(
                                trade_id=trade_id,
                                reduction_qty=half_qty,
                                exit_price=ltp,
                                pnl_segment=partial_pnl,
                                reason="PARTIAL_PROFIT_2R",
                            )
                            remaining_qty -= half_qty

                    # Raise SL floor to lock 0.5R on the remaining position
                    sl = round(entry_price + 0.5 * risk, 1)
                    stage = 2
                    trade_repo.update_sl(trade_id, sl)
                    if sl_oid and not self.dry_run:
                        self.order_manager.modify_sl_order(sl_oid, sl, symbol, token, remaining_qty)

                # ── Stage 3: Tighten trail at 3R ──────────────────────────
                if stage < 3 and ltp >= entry_price + 3 * risk:
                    logger.info(
                        f"Gamma Blast: 💎 Stage 3 (3R+). "
                        f"Activating tight trail on {remaining_qty} qty. No target cap."
                    )
                    stage = 3

                # ── Progressive trailing SL ───────────────────────────────
                # Trail distance shrinks as profit grows so winners run further:
                #   Stage 1 (1R–2R):  trail at 1.0R  — wide, avoids post-breakeven whipsaws
                #   Stage 2 (2R–3R):  trail at 0.75R — tighter, profit locked, let it breathe
                #   Stage 3 (3R+):    trail at 0.50R  — very tight, milk every point
                if stage >= 1:
                    trail_dist = risk * (1.0 if stage == 1 else 0.75 if stage == 2 else 0.5)
                    trail_dist = max(trail_dist, 3.0)   # never trail closer than ₹3 (bid/ask noise)
                    new_sl = round(ltp - trail_dist, 1)
                    if new_sl > sl:
                        logger.info(
                            f"Gamma Blast: 📈 Trail SL {sl} → {new_sl} "
                            f"(LTP={ltp:.1f}, dist={trail_dist:.1f}, stage={stage})"
                        )
                        sl = new_sl
                        trade_repo.update_sl(trade_id, sl)
                        if sl_oid and not self.dry_run:
                            self.order_manager.modify_sl_order(sl_oid, sl, symbol, token, remaining_qty)

                # ── SL hit → exit remaining ───────────────────────────────
                if ltp <= sl:
                    reason = "TRAIL_SL_HIT" if stage > 0 else "SL_HIT"
                    logger.info(
                        f"Gamma Blast: {reason} at ₹{ltp:.1f} (SL={sl:.1f}). "
                        f"Exiting {remaining_qty} qty."
                    )
                    # Step 1: Cancel broker SL FIRST so it can't double-fire
                    if sl_oid:
                        self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
                        sl_oid = None
                    # Step 2: Send market exit and capture actual fill price
                    actual_exit_price = ltp  # fallback
                    if not self.dry_run:
                        exit_params = {
                            "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                            "transactiontype": "SELL", "exchange": "NFO",
                            "ordertype": "MARKET", "price": 0,
                            "producttype": "INTRADAY", "duration": "DAY", "quantity": remaining_qty,
                        }
                        exit_oid = self.order_manager.place_order(exit_params)
                        if exit_oid:
                            fill = self.wait_for_fill(exit_oid)
                            if fill['status'] == 'FILLED' and fill.get('price', 0) > 0:
                                actual_exit_price = fill['price']
                    # Step 3: Close DB with real fill price (PnL auto-calculated)
                    trade_repo.close_trade(trade_id=trade_id, exit_price=actual_exit_price, exit_reason=reason)
                    self.active_position = None
                    self._last_sl_hit_time = time.time()
                    break

                # ── Time exit at 15:10 ────────────────────────────────────
                if datetime.datetime.now().time() >= datetime.time(15, 10):
                    if self.exit_market(token, symbol, remaining_qty, "TIME", trade_id, sl_oid):
                        sl_oid = None
                        self.active_position = None
                        break

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
                
                # REST FALLBACK FOR EXIT IF TIMEOUT
                if fill['status'] == 'TIMEOUT' and not self.dry_run:
                    logger.info(f"Gamma Blast: ⏳ Exit order {oid} timed out. Doing one final REST API check...")
                    try:
                        ob_res = self.api.orderBook()
                        if ob_res and ob_res.get('status'):
                            for ord_info in ob_res.get('data', []):
                                if ord_info.get('orderid') == oid:
                                    rest_status = ord_info.get('status', '').lower()
                                    if rest_status == 'complete':
                                        logger.info(f"Gamma Blast: ✅ Exit order {oid} actually FILLED on REST check!")
                                        fill = {'status': 'FILLED', 'price': float(ord_info.get('averageprice', 0))}
                                    break
                    except Exception as e:
                        logger.error(f"Gamma Blast Final REST Check Error: {e}")

                # Ensure the exit limit actually filled, so we don't abandon the order
                if fill['status'] == 'TIMEOUT':
                    logger.warning(f"Gamma Blast: Exit order {oid} TIMEOUT permanently. Canceling and retrying monitor mode.")
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
        
        result = order_feed.wait_for_fill(order_id, timeout=30)
        
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
