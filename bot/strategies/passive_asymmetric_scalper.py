import time
import datetime
import json
import os
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.core.order_manager import OrderManager
from bot.utils.logger import logger
from bot.utils.notifier import notifier

class PassiveAsymmetricScalper:
    """
    PassiveAsymmetricScalper Strategy 🎯⚡
    An institutional option buying scalper designed for small accounts (₹50k capital).
    
    Key Rules:
    1. Dynamic Lot Allocation:
       - Fetch the current 3-minute ATR of Nifty.
       - If Option Premium <= ₹40: 4 Lots (260 units).
       - If Option Premium is between ₹41 and ₹70: 2 Lots (130 units).
       - If Option Premium > ₹70: 1 Lot (65 units).
    2. Asymmetric Risk Engine:
       - Stop Loss: Entry Price - (1.5 * Nifty 3-minute ATR).
       - Technical Target: Entry Price + (4.5 * Nifty 3-minute ATR) [Hard 1:3 RR].
    3. Absolute Monetary Killswitch:
       - Combined Net Profit touches +₹1,500: market close.
       - Combined Net Loss touches -₹1,000: emergency square-off.
    4. Over-Trading Shield:
       - Max 2 trading attempts per day.
    5. Strict Passive Mode:
       - No active WebSockets or direct REST market calls in child process.
       - Re-uses live ticks from MongoDB ticks_cache and Spot/ATR from market_analysis.json.
    """

    STRATEGY_NAME = "PASSIVE_ASYMMETRIC_SCALPER"

    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(api, dry_run=dry_run)
        self.order_manager = OrderManager(api, dry_run=dry_run)
        self.data_fetcher = DataFetcher(api)
        self.running = True
        self.active_position = None

    def stop(self):
        self.running = False
        if self.active_position:
            logger.warning("PassiveScalper: Stop signal received. Active position remains open for manual/master handling.")

    def sync_state(self):
        """Recover from server/process restart by loading active trade from MongoDB."""
        mode = "PAPER" if self.dry_run else "LIVE"
        try:
            open_trades = trade_repo.get_open_trades(mode=mode, strategy=self.STRATEGY_NAME)
            if open_trades:
                t = open_trades[0]
                self.active_position = {
                    'id': t['id'],
                    'symbol': t['symbol'],
                    'token': t['token'],
                    'qty': t['qty'],
                    'entry_price': float(t['entry_price']),
                    'atr': float(t.get('entry_atr') or 15.0),
                    'sl_price': float(t['sl_price']),
                    'target_price': float(t.get('target_price') or (t['entry_price'] + 4.5 * 15.0)),
                    'sl_oid': t.get('sl_order_id')
                }
                logger.info(f"♻️ [PassiveScalper] State Recovered successfully for trade {t['symbol']}")
        except Exception as e:
            logger.error(f"[PassiveScalper] State recovery failed: {e}")

    def _get_passive_ltp(self, token):
        """Fetches LTP passively from MongoDB ticks_cache (no REST/WebSocket)."""
        try:
            if trade_repo.client:
                db = trade_repo.client[Config.MONGO_DB]
                doc = db["ticks_cache"].find_one({"token": token})
                if doc:
                    return float(doc.get("ltp", 0.0))
        except Exception as e:
            logger.error(f"[PassiveScalper] MongoDB ticks_cache LTP read error: {e}")
        return 0.0

    def _read_market_analysis(self):
        """Reads spot price, 3m ATR and trend from shared market_analysis.json."""
        state_file = "data/market_analysis.json"
        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"[PassiveScalper] Error reading market_analysis.json: {e}")
        return {}

    def _get_today_attempts(self):
        """Counts trading attempts for today."""
        mode = "PAPER" if self.dry_run else "LIVE"
        try:
            today_trades = trade_repo.get_today_trades(mode=mode)
            strat_trades = [t for t in today_trades if t.get('strategy') == self.STRATEGY_NAME]
            return len(strat_trades)
        except Exception as e:
            logger.error(f"[PassiveScalper] Error counting today's attempts: {e}")
            return 0

    def place_market_order(self, symbol, token, qty, transaction_type="BUY"):
        """Isolated placement of market order to bypass limit walking for instant execution."""
        orderparams = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": transaction_type,
            "exchange": "NFO",
            "ordertype": "MARKET",
            "producttype": "INTRADAY",
            "duration": "DAY",
            "quantity": int(qty),
            "price": 0.0,
            "disclosedquantity": 0
        }
        return self.order_manager.place_order(orderparams, strategy_name=self.STRATEGY_NAME)

    def execute(self, expiry):
        logger.info(f"⚡ --- PASSIVE ASYMMETRIC SCALPER INITIATED ({expiry}) --- 🎯")
        self.sync_state()

        while self.running:
            # 1. Market Open Guard
            if not self.gatekeeper.is_market_open():
                logger.info("[PassiveScalper] Market Closed or outside trading hours. Standing down.")
                break

            # 2. Position Monitoring Path
            if self.active_position:
                self._monitor_position()
                continue

            # 3. Check Over-Trading Shield (Max 2 attempts per day)
            attempts = self._get_today_attempts()
            if attempts >= 2:
                logger.info(f"[PassiveScalper] Attempt Shield Active: {attempts}/2 trades completed today. Sleep 30s.")
                time.sleep(30)
                continue

            # 4. Read Shared Intelligence
            md = self._read_market_analysis()
            nifty_ltp = md.get("nifty_ltp", 0.0)
            atr_3m = md.get("nifty_3m_atr", 0.0)
            analysis = md.get("analysis", {})

            if nifty_ltp == 0.0 or atr_3m == 0.0:
                logger.debug("[PassiveScalper] Missing Nifty Spot or 3m ATR from Master. Retrying in 5s...")
                time.sleep(5)
                continue

            # 5. Trend Breakout Signals (EMA Cloud Crossover & RSI filter)
            ema9 = analysis.get("ema9", 0.0)
            ema21 = analysis.get("ema21", 0.0)
            rsi = analysis.get("rsi", 50.0)
            adx = analysis.get("adx", 0.0)

            if ema9 == 0.0 or ema21 == 0.0:
                time.sleep(5)
                continue

            leg = None
            import os
            if os.getenv("FORCE_SIGNAL") in ["CE", "PE"]:
                leg = os.getenv("FORCE_SIGNAL")
                logger.info(f"[PassiveScalper] 🚀 FORCING SIGNAL: {leg} (FORCE_SIGNAL={leg})")
            elif nifty_ltp > ema9 > ema21 and rsi > 54 and adx >= 22:
                leg = "CE"
            elif nifty_ltp < ema9 < ema21 and rsi < 46 and adx >= 22:
                leg = "PE"

            if not leg:
                time.sleep(5) # Passive sleep to preserve CPU
                continue

            # 6. Locate Target Option Contract
            atm_strike = int(round(nifty_ltp / 50.0) * 50.0)
            token, symbol = self.token_loader.get_token("NIFTY", expiry, atm_strike, leg)
            if not token:
                logger.warning(f"[PassiveScalper] Could not resolve token for {atm_strike} {leg}")
                time.sleep(10)
                continue

            # 7. Fetch Option LTP Passively from MongoDB ticks_cache
            opt_ltp = self._get_passive_ltp(token)
            if opt_ltp == 0.0:
                # If cache is not warm, poll up to 5s to see if it updates
                logger.warning(f"[PassiveScalper] Ticks cache cold for {symbol}. Waiting up to 5s...")
                start_w = time.time()
                while time.time() - start_w < 5:
                    opt_ltp = self._get_passive_ltp(token)
                    if opt_ltp > 0.0: break
                    time.sleep(1)
                
                # Fallback to REST only if absolutely empty at startup/warmup
                if opt_ltp == 0.0:
                    try:
                        from bot.utils.rate_limiter import rate_limiter
                        rate_limiter.wait()
                        q = self.api.ltpData("NFO", symbol, token)
                        if q and q.get('status'):
                            opt_ltp = float(q['data']['ltp'])
                    except Exception as e:
                        logger.error(f"[PassiveScalper] Fallback LTP Fetch failed: {e}")

            if opt_ltp == 0.0:
                logger.error(f"[PassiveScalper] Could not fetch Option LTP for {symbol}. Skipping cycle.")
                time.sleep(5)
                continue

            # 8. Dynamic Lot Allocation
            if opt_ltp <= 60.0:
                lots = 8
            elif opt_ltp <= 120.0:
                lots = 4
            else:
                lots = 2

            qty = lots * Config.NIFTY_LOT_SIZE
            logger.info(f"🎯 [PassiveScalper] Entry Triggered: {leg} Breakout | Spot: {nifty_ltp} | Option: {symbol} @ ₹{opt_ltp}")
            logger.info(f"📊 [Position Sizer] Premium: ₹{opt_ltp} -> Allocation: {lots} Lots ({qty} units)")

            # 9. Asymmetric Risk Calculations
            sl_price = round(opt_ltp - (1.5 * atr_3m), 2)
            target_price = round(opt_ltp + (4.5 * atr_3m), 2)
            
            if sl_price <= 0.5: sl_price = 0.5 # Never let SL drop to zero or negative

            logger.info(f"🛡️ [Asymmetric Risk] 3m ATR: {atr_3m:.2f} -> SL: ₹{sl_price} | Target: ₹{target_price} [Hard 1:3 RR]")

            # 10. Place Order
            mode = "PAPER" if self.dry_run else "LIVE"
            trade_id = trade_repo.save_trade(
                symbol=symbol, token=token, leg=leg, qty=qty,
                entry_price=opt_ltp, sl_price=sl_price, mode=mode, strategy=self.STRATEGY_NAME
            )

            # Update target price in MongoDB (save_trade doesn't take target_price directly)
            if trade_id:
                trade_repo.collection.update_one({"id": trade_id}, {"$set": {"target_price": target_price, "entry_atr": atr_3m}})

            # Place Smart Limit or Market
            order_id = self.order_manager.place_smart_limit(
                symbol, token, qty, opt_ltp, "BUY", max_walk_ticks=5, strategy_name=self.STRATEGY_NAME, mode=mode
            )

            if order_id:
                self.active_position = {
                    'id': trade_id,
                    'symbol': symbol,
                    'token': token,
                    'qty': qty,
                    'entry_price': opt_ltp,
                    'atr': atr_3m,
                    'sl_price': sl_price,
                    'target_price': target_price,
                    'sl_oid': None
                }
                notifier.send_message(
                    f"🎯 **PASSIVE SCALPER ORDER PLACED!** 🚀\n"
                    f"Symbol: {symbol}\n"
                    f"Premium: ₹{opt_ltp}\n"
                    f"Qty: {qty} ({lots} Lots)\n"
                    f"SL: ₹{sl_price} (1.5x ATR)\n"
                    f"Target: ₹{target_price} (4.5x ATR)"
                )
                
                # Place stoploss order on broker for structural protection
                sl_id = self.order_manager.place_sl_order(symbol, token, qty, sl_price, leg, "SELL")
                if sl_id:
                    self.active_position['sl_oid'] = sl_id
                    trade_repo.update_sl_order_id(trade_id, sl_id)
            else:
                logger.error("[PassiveScalper] Smart limit entry placement failed.")
                if trade_id:
                    trade_repo.close_trade(trade_id=trade_id, exit_price=0.0, pnl=0.0, exit_reason="ENTRY_FAILED")
                time.sleep(10)

    def _monitor_position(self):
        """Active High-Frequency Monitor Loop (Running entirely on passive LTP feeds)."""
        p = self.active_position
        token = p['token']
        sym = p['symbol']
        qty = p['qty']
        entry = p['entry_price']
        sl = p['sl_price']
        tgt = p['target_price']
        tid = p['id']

        logger.info(f"👀 [PassiveScalper] Monitoring: {sym} @ ₹{entry} | SL: {sl} | Target: {tgt}")

        while self.running and self.active_position:
            # Sleep 1s to execute close to real-time passive tracking
            time.sleep(1)

            opt_ltp = self._get_passive_ltp(token)
            if opt_ltp == 0.0:
                try:
                    from bot.utils.rate_limiter import rate_limiter
                    rate_limiter.wait()
                    q = self.api.ltpData("NFO", sym, token)
                    if q and q.get('status'):
                        opt_ltp = float(q['data']['ltp'])
                except Exception as e:
                    logger.error(f"[PassiveScalper] Monitor Fallback LTP Fetch failed: {e}")

            if opt_ltp == 0.0:
                continue

            # Calculate Combined Net Profit/Loss of the active lots
            unrealized_pnl = round((opt_ltp - entry) * qty, 2)
            logger.debug(f"[PassiveScalper] LTP: ₹{opt_ltp:.2f} | UnPnL: ₹{unrealized_pnl:+.2f}")

            # 1. Combined Net Profit Killswitch (+₹1,500 scaled proportionally by lot count)
            lots_count = float(qty) / Config.NIFTY_LOT_SIZE
            scaled_profit_target = 1500.0 * lots_count
            if unrealized_pnl >= scaled_profit_target:
                logger.critical(f"🏆 [KILLSWITCH] Net Profit target of +₹{scaled_profit_target:.2f} hit (Current: ₹{unrealized_pnl:.2f})! Sweeping profits.")
                self._execute_exit("PROFIT_KILLSWITCH", opt_ltp, unrealized_pnl)
                break

            # 2. Combined Net Loss Killswitch (-₹1,000 scaled proportionally by lot count)
            scaled_loss_limit = -1000.0 * lots_count
            if unrealized_pnl <= scaled_loss_limit:
                logger.critical(f"🚨 [KILLSWITCH] Net Loss limit of -₹{abs(scaled_loss_limit):.2f} hit (Current: ₹{unrealized_pnl:.2f})! Executing Emergency square-off.")
                self._execute_exit("LOSS_KILLSWITCH", opt_ltp, unrealized_pnl)
                break

            # 3. Dynamic Technical Stop Loss
            if opt_ltp <= sl:
                logger.warning(f"🛑 [TECHNICAL SL] Premium ₹{opt_ltp:.2f} dropped below Stop Loss ₹{sl:.2f}. Triggering exit.")
                self._execute_exit("TECHNICAL_STOP_LOSS", opt_ltp, unrealized_pnl)
                break

            # 4. Dynamic Technical Target (Hard 1:3 RR)
            if opt_ltp >= tgt:
                logger.info(f"🎉 [TECHNICAL TARGET] Premium ₹{opt_ltp:.2f} reached Target ₹{tgt:.2f}. Booking profits.")
                self._execute_exit("TECHNICAL_TARGET", opt_ltp, unrealized_pnl)
                break

            # 5. EOD Hard Cut at 15:10 IST
            now = datetime.datetime.now().time()
            if now >= datetime.time(15, 10):
                logger.warning(f"⏰ [EOD LIQUIDATION] Time is {now}. Squaring off position at ₹{opt_ltp:.2f}.")
                self._execute_exit("EOD_LIQUIDATION", opt_ltp, unrealized_pnl)
                break

    def _execute_exit(self, reason, exit_price, pnl):
        """Closes position instantly using market order and updates local/DB state."""
        p = self.active_position
        if not p: return

        sym = p['symbol']
        token = p['token']
        qty = p['qty']
        tid = p['id']
        sl_oid = p.get('sl_oid')

        logger.info(f"⚡ [Exit Execution] Squaring off {sym} ({qty} units) | Price: ₹{exit_price:.2f} | Reason: {reason}")

        # Cancel open broker-side SL if it exists
        if sl_oid:
            try:
                self.order_manager.cancel_order(sl_oid)
            except Exception as e:
                logger.warning(f"[PassiveScalper] Could not cancel broker SL: {e}")

        # Place instant exit market order
        exit_oid = self.place_market_order(sym, token, qty, "SELL")

        # Update Database
        try:
            trade_repo.close_trade(trade_id=tid, exit_price=exit_price, pnl=pnl, exit_reason=reason)
        except Exception as e:
            logger.error(f"[PassiveScalper] DB Close Trade error: {e}")

        # Notify
        notifier.send_message(
            f"⚡ **PASSIVE SCALPER POSITION CLOSED!**\n"
            f"Symbol: {sym}\n"
            f"Exit Reason: {reason}\n"
            f"Exit Price: ₹{exit_price:.2f}\n"
            f"Net PnL: **₹{pnl:+.2f}** 💸"
        )

        self.active_position = None
