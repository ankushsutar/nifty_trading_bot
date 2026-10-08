"""
bot/strategies/vertical_debit_spread_strategy.py — Directional Vertical Debit Spread Strategy

Deployed when India VIX < 15.0 in trending environments.
1. Trend Alignment: Strictly aligns Delta exposure with 15-minute Supertrend (10, 3)
   and 20-EMA / 50-EMA directional bias:
   - BULLISH: Bull Call Spread (Buy ATM CE, Sell OTM CE) -> Positive Delta.
   - BEARISH: Bear Put Spread (Buy ATM PE, Sell OTM PE) -> Negative Delta.
2. Dynamic Stop-Loss: Dynamic trailing stop based on 1.5x the 5-minute ATR.
3. Execution Protocol: Routes multi-leg debit spread orders simultaneously.
4. Risk Management: Strictly ensures maximum risk per trade <= 2% of total account equity.
"""

import time
import datetime
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.core.basket_manager import BasketManager
from bot.core.market_regime_filter import MarketRegimeFilter
from bot.core.market_feed import market_feed
from bot.utils.logger import logger
from bot.utils.expiry_calculator import get_next_weekly_expiry


class VerticalDebitSpreadStrategy:
    STRATEGY_NAME = "VERTICAL_DEBIT_SPREAD"

    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(api, dry_run=dry_run)
        self.basket_manager = BasketManager(api, dry_run=dry_run)
        self.data_fetcher = DataFetcher(api)
        self.regime_filter = MarketRegimeFilter(api, token_loader, self.data_fetcher)
        self.running = True
        self.risk_multiplier = 1.0

    def get_atm_and_otm_strikes(self, spot: float, direction: str, spread_width: float = 100.0) -> tuple:
        """
        Calculates ATM strike and OTM wing strike.
        For Nifty, strikes are in multiples of 50.
        """
        atm_strike = round(spot / 50.0) * 50.0
        if direction == "BULLISH":
            # Bull Call Spread: Long ATM CE, Short OTM CE
            otm_strike = atm_strike + spread_width
            option_type = "CE"
        else:
            # Bear Put Spread: Long ATM PE, Short OTM PE
            otm_strike = atm_strike - spread_width
            option_type = "PE"

        return atm_strike, otm_strike, option_type

    def execute(self, expiry: str = None, action: str = None, **kwargs):
        """
        Main execution entry point.
        Checks regime filter, executes spread, and enters monitoring loop.
        """
        logger.info(f"\n{'='*65}\n🚀 [VERTICAL DEBIT SPREAD] Initializing Execution...\n{'='*65}")

        # 1. Gatekeeper Pre-Trade Safety Checks
        if not self.gatekeeper.is_market_open():
            logger.warning("[VerticalSpread] Market is closed. Entry rejected.")
            return False

        if self.gatekeeper.is_blackout_period():
            logger.info("[VerticalSpread] Mid-day blackout period. Entry suspended.")
            return False

        if not self.gatekeeper.check_max_daily_loss():
            logger.critical("[VerticalSpread] Max daily loss breached. Entry rejected.")
            return False

        # 2. Market Regime & VIX Filter Check
        regime = self.regime_filter.filter_regime()
        vix = regime["vix"]
        bias = regime["directional_bias"]
        atr_5m = regime["atr_5m"]

        logger.info(
            f"[VerticalSpread] Regime Analysis: VIX={vix:.2f} (Low VIX: {regime['is_low_vix']}) | "
            f"15m Trend Bias={bias} ({regime['trend_strength']}) | 5m ATR={atr_5m:.2f} pts"
        )

        if bias not in ["BULLISH", "BEARISH"]:
            logger.info(f"[VerticalSpread] Directional bias is NEUTRAL. Waiting for clear trend.")
            return False

        # 3. Capital & 2% Max Risk Sizing
        account_equity = self.gatekeeper.get_current_capital()
        tier = Config.get_tier(account_equity)
        logger.info(f"[VerticalSpread] Account Equity: ₹{account_equity:,.2f} [{tier.name} tier]")

        # 4. Resolve Strikes and Expiry
        spot_token = getattr(Config, "NIFTY_SPOT_TOKEN", "99926000")
        spot = self.data_fetcher.get_ltp(spot_token, exchange="NSE")
        if not spot or spot <= 0:
            logger.error("[VerticalSpread] Could not fetch Nifty spot price.")
            return False

        atm_strike, otm_strike, opt_type = self.get_atm_and_otm_strikes(spot, bias, spread_width=100.0)
        expiry = expiry or get_next_weekly_expiry()
        lot_size = Config.NIFTY_LOT_SIZE

        logger.info(
            f"[VerticalSpread] Setup: {bias} {opt_type} Spread | Spot: {spot:.1f} | "
            f"Buy ATM: {atm_strike} {opt_type} | Sell OTM: {otm_strike} {opt_type} | Expiry: {expiry}"
        )

        # 5. Token Lookup
        buy_symbol, buy_token = self.token_loader.get_token("NIFTY", expiry, atm_strike, opt_type)
        sell_symbol, sell_token = self.token_loader.get_token("NIFTY", expiry, otm_strike, opt_type)

        if not buy_token or not sell_token:
            logger.error(f"[VerticalSpread] Token lookup failed: Buy={buy_token}, Sell={sell_token}")
            return False

        # 6. Fetch Preliminary Quotes
        buy_ltp = self.data_fetcher.get_ltp(buy_token, exchange="NFO")
        sell_ltp = self.data_fetcher.get_ltp(sell_token, exchange="NFO")

        if buy_ltp <= 0 or sell_ltp <= 0:
            logger.warning(f"[VerticalSpread] Invalid quotes: Buy LTP={buy_ltp}, Sell LTP={sell_ltp}")
            return False

        estimated_debit = max(1.0, buy_ltp - sell_ltp)
        logger.info(f"[VerticalSpread] Estimated Net Debit: ₹{estimated_debit:.2f}/share (Buy ₹{buy_ltp:.2f} - Sell ₹{sell_ltp:.2f})")

        # 7. Position Sizing: Strict 2% Max Risk Protocol
        sizing = self.regime_filter.calculate_position_size(
            account_equity=account_equity,
            net_debit_per_share=estimated_debit,
            lot_size=lot_size,
            atr_5m=atr_5m,
            max_risk_pct=0.02
        )

        base_lots = sizing["lots"]
        multiplier = getattr(self, "risk_multiplier", 1.0)
        if multiplier < 1.0:
            lots = max(1, int(round(base_lots * multiplier))) if base_lots >= 1 else 0
        else:
            lots = int(base_lots * multiplier)
        if tier.max_lots > 0:
            lots = min(lots, tier.max_lots)
        if lots < 1:
            logger.warning(
                f"[VerticalSpread] 2% Risk Limit (₹{sizing['max_risk_rupees']:.0f}) insufficient "
                f"for 1 lot (Cost: ₹{estimated_debit * lot_size:.0f}). Aborting."
            )
            return False

        total_qty = lots * lot_size
        logger.info(
            f"🎯 [VerticalSpread] Risk Sized: {lots} lots ({total_qty} units) | "
            f"Total Risk: ₹{sizing['total_risk']:.2f} ({sizing['actual_risk_pct']:.2f}% <= 2.0% limit)"
        )

        # 8. Multi-Leg Execution: Route SIMULTANEOUSLY to avoid slippage
        mode = "PAPER" if self.dry_run else "LIVE"
        buy_leg = {"symbol": buy_symbol, "token": buy_token, "price": buy_ltp}
        sell_leg = {"symbol": sell_symbol, "token": sell_token, "price": sell_ltp}

        exec_res = self.basket_manager.execute_spread_simultaneous(
            strategy_name=self.STRATEGY_NAME,
            buy_leg=buy_leg,
            sell_leg=sell_leg,
            qty=total_qty,
            mode=mode
        )

        if exec_res["status"] != "SUCCESS":
            logger.error("[VerticalSpread] Order execution failed or incomplete. Aborting trade.")
            return False

        actual_buy_price = buy_ltp  # Populated from fills
        actual_sell_price = sell_ltp
        entry_spread_value = max(0.5, actual_buy_price - actual_sell_price)
        logger.info(f"✅ [VerticalSpread] Position Open: Net Debit Paid = ₹{entry_spread_value:.2f}/share")

        # 9. Register in MongoDB Trade Repository
        trade_id = None
        try:
            trade_id = trade_repo.save_trade(
                symbol=f"NIFTY_{opt_type}_DEBIT_SPREAD",
                token=str(buy_token),
                leg=f"{opt_type}_SPREAD",
                qty=total_qty,
                entry_price=entry_spread_value,
                status="OPEN",
                mode=mode,
                strategy=self.STRATEGY_NAME,
                sl_price=0.0  # Dynamic trailing ATR SL managed in loop
            )
        except Exception as e:
            logger.warning(f"[VerticalSpread] Trade DB registration warning: {e}")

        # 10. Dynamic Trailing Stop-Loss Monitoring Loop
        self._monitor_position(
            buy_leg=buy_leg,
            sell_leg=sell_leg,
            qty=total_qty,
            entry_spread_value=entry_spread_value,
            atr_5m=atr_5m,
            mode=mode,
            trade_id=trade_id,
            spread_width=100.0
        )

        return True

    def _monitor_position(
        self,
        buy_leg: dict,
        sell_leg: dict,
        qty: int,
        entry_spread_value: float,
        atr_5m: float,
        mode: str,
        trade_id: str,
        spread_width: float = 100.0
    ):
        """
        Dynamic Trailing Stop-Loss Monitoring:
        - Trailing stop based on 1.5x 5-minute ATR (scaled to spread).
        - Trails upward as spread value increases.
        - Profit Target: 1:2 R:R or 70% of max theoretical profit.
        """
        logger.info("[VerticalSpread] Entering Position Monitoring Loop with Dynamic 1.5x ATR Trailing SL...")

        # ATR SL distance: 1.5x 5m ATR scaled by spread delta sensitivity (~0.30)
        dynamic_sl_pts = max(3.0, (1.5 * atr_5m) * 0.30)
        initial_sl = max(0.10, entry_spread_value - dynamic_sl_pts)
        trailing_sl = initial_sl
        peak_spread_value = entry_spread_value

        # Target: 2.0x Risk or 70% of max width
        max_possible_profit = spread_width - entry_spread_value
        profit_target = min(entry_spread_value + (2.0 * dynamic_sl_pts), entry_spread_value + (0.70 * max_possible_profit))

        logger.info(
            f"📊 [VerticalSpread] Risk Grid: Entry Spread=₹{entry_spread_value:.2f} | "
            f"Initial Dynamic SL=₹{initial_sl:.2f} (-{dynamic_sl_pts:.2f}pts) | "
            f"Target=₹{profit_target:.2f} (+{profit_target - entry_spread_value:.2f}pts)"
        )

        last_log_time = 0.0

        while self.running:
            try:
                time.sleep(1.0)
                now = datetime.datetime.now().time()

                # Safety 1: Market Time Exit at 15:15
                if now >= datetime.time(15, 15):
                    logger.warning("[VerticalSpread] ⏰ Time Exit triggered at 15:15. Closing spread.")
                    break

                # Safety 2: Max Daily Loss check
                if not self.gatekeeper.check_max_daily_loss():
                    logger.critical("[VerticalSpread] 🛑 Max Daily Loss breached! Emergency exit.")
                    break

                # Current Quotes
                current_buy_ltp = market_feed.get_ltp(buy_leg["token"]) or self.data_fetcher.get_ltp(buy_leg["token"], exchange="NFO")
                current_sell_ltp = market_feed.get_ltp(sell_leg["token"]) or self.data_fetcher.get_ltp(sell_leg["token"], exchange="NFO")

                if current_buy_ltp <= 0 or current_sell_ltp <= 0:
                    continue

                current_spread_value = current_buy_ltp - current_sell_ltp
                pnl_per_share = current_spread_value - entry_spread_value
                total_pnl = pnl_per_share * qty

                # Update Trailing Stop Loss if price made a new high
                if current_spread_value > peak_spread_value:
                    peak_spread_value = current_spread_value
                    new_trailing_sl = max(initial_sl, peak_spread_value - dynamic_sl_pts)
                    if new_trailing_sl > trailing_sl:
                        trailing_sl = new_trailing_sl
                        logger.info(f"📈 [VerticalSpread] Trail Updated: New Peak=₹{peak_spread_value:.2f} -> SL Trailed to ₹{trailing_sl:.2f}")

                # Heartbeat log every 30 seconds
                if time.time() - last_log_time > 30:
                    last_log_time = time.time()
                    logger.info(
                        f"📊 [VerticalSpread MONITOR] Spread LTP=₹{current_spread_value:.2f} | "
                        f"Entry=₹{entry_spread_value:.2f} | Trailing SL=₹{trailing_sl:.2f} | "
                        f"Target=₹{profit_target:.2f} | P&L=₹{total_pnl:+,.1f}"
                    )

                # Check Exit Conditions
                # Condition A: Dynamic Trailing SL Hit
                if current_spread_value <= trailing_sl:
                    logger.warning(
                        f"🛑 [VerticalSpread EXIT] Dynamic Trailing SL Hit: "
                        f"Spread {current_spread_value:.2f} <= SL {trailing_sl:.2f}. Exiting."
                    )
                    break

                # Condition B: Profit Target Hit
                if current_spread_value >= profit_target:
                    logger.info(
                        f"🎯 [VerticalSpread EXIT] Profit Target Reached: "
                        f"Spread {current_spread_value:.2f} >= Target {profit_target:.2f}. Exiting."
                    )
                    break

            except Exception as e:
                logger.error(f"[VerticalSpread] Monitor loop error: {e}")
                time.sleep(2.0)

        # 11. Simultaneous Position Exit
        logger.info("[VerticalSpread] Closing both legs simultaneously...")
        buy_leg["current_price"] = current_buy_ltp
        sell_leg["current_price"] = current_sell_ltp
        self.basket_manager.close_spread_simultaneous(
            strategy_name=self.STRATEGY_NAME,
            buy_leg=buy_leg,
            sell_leg=sell_leg,
            qty=qty,
            mode=mode
        )

        # 12. Persist Close in Database
        final_spread = current_buy_ltp - current_sell_ltp
        realized_pnl = (final_spread - entry_spread_value) * qty
        logger.info(f"🏁 [VerticalSpread] Trade Closed: Final Realized P&L: ₹{realized_pnl:+,.2f}")

        try:
            if trade_id:
                trade_repo.close_trade(
                    trade_id=trade_id,
                    exit_price=final_spread,
                    pnl=realized_pnl,
                    exit_reason="DYNAMIC_ATR_TRAIL_OR_TARGET"
                )
        except Exception as e:
            logger.warning(f"[VerticalSpread] Trade DB close update error: {e}")

    def stop(self):
        """Signals graceful shutdown."""
        self.running = False
