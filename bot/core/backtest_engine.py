"""
BacktestEngine: Vectorized backtesting for Nifty Options strategies.

Ingests 1-minute OHLCV candles (NIFTY index) and simulates each strategy's
signal logic, applying SafetyGatekeeper-equivalent rules to produce realistic
performance metrics.

Expected DataFrame schema:
    timestamp (datetime, index or column), open, high, low, close, volume

Usage:
    engine = BacktestEngine(initial_capital=100000)
    engine.load_data(df_1m)
    results = engine.run_all_strategies()
    for name, metrics in results.items():
        print(name, metrics)
"""

import pandas as pd
import numpy as np
from datetime import time as dtime
from bot.utils.logger import logger


class BacktestEngine:
    """Vectorized backtesting engine that mirrors live strategy logic."""

    STRATEGIES = ["MOMENTUM", "VWAP", "ORB", "INSIDE_BAR", "OHL", "GAMMA_BLAST", "STRADDLE_SCALP"]

    # Market session constants
    SESSION_START = dtime(9, 15)
    SESSION_END   = dtime(15, 15)        # Time-exit cutoff
    BLACKOUT_START = dtime(11, 30)
    BLACKOUT_END   = dtime(13, 0)
    OHL_WINDOW_END = dtime(9, 20)
    ORB_WINDOW_END = dtime(10, 30)

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        lot_size: int = 65,
        max_daily_loss_pct: float = 0.10,
        risk_per_trade_pct: float = 0.06,
        brokerage_per_lot: float = 40.0,
        option_delta: float = 0.50,           # ATM delta approximation
        option_premium_atr_mult: float = 2.5,  # ATM premium ≈ ATR * this multiplier
        max_trades_per_day: int = 3,
        max_lots: int = 20,                    # Hard cap on position size
        slippage_pct: float = 0.005            # 0.5% slippage per side
    ):
        self.initial_capital = initial_capital
        self.lot_size = lot_size
        self.max_daily_loss = -(initial_capital * max_daily_loss_pct)
        self.risk_per_trade_pct = risk_per_trade_pct
        self.brokerage_per_lot = brokerage_per_lot
        self.option_delta = option_delta
        self.option_premium_atr_mult = option_premium_atr_mult
        self.max_trades_per_day = max_trades_per_day
        self.max_lots = max_lots
        self.slippage_pct = slippage_pct

        self.df_1m: pd.DataFrame | None = None
        self.df_5m: pd.DataFrame | None = None
        self.df_15m: pd.DataFrame | None = None

    @classmethod
    def from_capital(cls, initial_capital: float, lot_size: int = 65) -> "BacktestEngine":
        """
        Factory: builds a BacktestEngine whose risk parameters mirror the live bot
        for the given capital amount.  Uses the same CapitalTier system as the bot,
        so backtest results are honest — they reflect what the live bot would actually do.

        Usage:
            engine = BacktestEngine.from_capital(initial_capital=100_000)
            engine.load_data(df_1m)
            results = engine.run_all_strategies()
        """
        from bot.config.settings import Config
        tier = Config.get_tier(initial_capital)
        return cls(
            initial_capital=initial_capital,
            lot_size=lot_size,
            max_daily_loss_pct=tier.max_daily_loss_pct,
            risk_per_trade_pct=tier.risk_per_trade_pct,
            max_trades_per_day=tier.max_trades_per_day,
        )

    # ------------------------------------------------------------------ #
    #  Data Loading                                                         #
    # ------------------------------------------------------------------ #

    def load_data(self, df_1m: pd.DataFrame) -> None:
        """
        Load and validate 1-minute OHLCV data.
        Automatically resamples to 5m and 15m bars.

        Args:
            df_1m: DataFrame with columns [open, high, low, close, volume].
                   Index or 'timestamp' column must be datetime.
        """
        df = df_1m.copy()

        # Normalise timestamp to index
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp")
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        required = {"open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"BacktestEngine.load_data: missing columns {missing}")

        # Keep only market hours
        df = df.between_time("09:15", "15:30")

        self.df_1m  = df
        self.df_5m  = self._resample(df, "5min")
        self.df_15m = self._resample(df, "15min")

        logger.info(
            f"[Backtest] Data loaded: {len(df)} 1m bars | "
            f"{len(self.df_5m)} 5m | {len(self.df_15m)} 15m | "
            f"Range: {df.index[0].date()} → {df.index[-1].date()}"
        )

    # ------------------------------------------------------------------ #
    #  Public API                                                           #
    # ------------------------------------------------------------------ #

    def run_all_strategies(self) -> dict:
        """Run every strategy and return a dict of {strategy: metrics}."""
        results = {}
        for name in self.STRATEGIES:
            try:
                results[name] = self.run_strategy(name)
            except Exception as e:
                logger.error(f"[Backtest] {name} failed: {e}")
                results[name] = {"error": str(e)}
        return results

    def run_strategy(self, strategy_name: str) -> dict:
        """
        Run a single strategy backtest end-to-end.

        Returns a dict with CAGR, Max Drawdown, Sharpe, Win Rate, and trade log.
        """
        if self.df_1m is None:
            raise RuntimeError("Call load_data() before run_strategy().")

        logger.info(f"[Backtest] Running {strategy_name}...")

        # ---- 1. Generate raw signals ----
        signals = self._generate_signals(strategy_name)  # pd.DataFrame: timestamp, direction, atr

        if signals.empty:
            logger.warning(f"[Backtest] {strategy_name}: No signals generated.")
            return self._empty_metrics()

        # ---- 2. Simulate trades with daily risk management ----
        trades, equity_curve = self._simulate_session(signals, strategy_name)

        if not trades:
            logger.warning(f"[Backtest] {strategy_name}: No trades executed.")
            return self._empty_metrics()

        # ---- 3. Compute performance metrics ----
        trading_days = max(1, self.df_1m.index.normalize().nunique())
        metrics = self._calculate_metrics(trades, equity_curve, trading_days, strategy_name)
        return metrics

    # ------------------------------------------------------------------ #
    #  Signal Generators (vectorized per strategy)                         #
    # ------------------------------------------------------------------ #

    def _generate_signals(self, strategy_name: str) -> pd.DataFrame:
        """Dispatch to per-strategy signal generator."""
        dispatch = {
            "MOMENTUM":   self._momentum_signals,
            "VWAP":       self._vwap_signals,
            "ORB":        self._orb_signals,
            "INSIDE_BAR": self._inside_bar_signals,
            "OHL":        self._ohl_signals,
            "GAMMA_BLAST":self._gamma_blast_signals,
            "STRADDLE_SCALP": self._straddle_scalp_signals,
        }
        fn = dispatch.get(strategy_name)
        if fn is None:
            raise ValueError(f"Unknown strategy: {strategy_name}")
        return fn()

    # ---- MOMENTUM (EMA9/EMA21 crossover + RSI + HTF) ---- #

    def _momentum_signals(self) -> pd.DataFrame:
        df5  = self.df_5m.copy()
        df15 = self.df_15m.copy()

        # 5-minute indicators
        df5["ema9"]  = df5["close"].ewm(span=9,  adjust=False).mean()
        df5["ema21"] = df5["close"].ewm(span=21, adjust=False).mean()
        df5["atr"]   = self._atr(df5, 14)
        df5["rsi"]   = self._rsi(df5, 14)

        # 15-minute HTF trend (requires EMA9 > EMA21 alignment)
        df15["ema9_15"]  = df15["close"].ewm(span=9,  adjust=False).mean()
        df15["ema21_15"] = df15["close"].ewm(span=21, adjust=False).mean()
        df15["htf_bull"] = (df15["ema9_15"] > df15["ema21_15"]).astype(int)
        # Forward-fill into 5m timeframe
        htf = df15["htf_bull"].reindex(df5.index, method="ffill").fillna(0)

        # Signal: BUY CE when 5m bullish AND 15m bullish AND RSI not overbought
        # PLUS PRO-TRADER: Volume Confirmation (Fallback to ATR if Volume is 0)
        if df5["volume"].max() > 0:
            avg_vol = df5["volume"].rolling(window=5).mean().shift(1)
            vol_ok = df5["volume"] > (avg_vol * 1.1)
        else:
            # Fallback: ATR must be expanding (Volatility surge)
            avg_atr = df5["atr"].rolling(window=5).mean().shift(1)
            vol_ok = df5["atr"] > avg_atr

        bullish = (
            (df5["ema9"] > df5["ema21"]) &
            (df5["ema9"].shift(1) <= df5["ema21"].shift(1)) &  # fresh crossover
            (df5["rsi"] < 70) &
            (htf == 1) &
            vol_ok &
            self._in_session(df5) &
            ~self._in_blackout(df5)
        )
        # BUY PE: mirror bearish
        htf_bear = (df15["htf_bull"].reindex(df5.index, method="ffill").fillna(1) == 0)
        bearish = (
            (df5["ema9"] < df5["ema21"]) &
            (df5["ema9"].shift(1) >= df5["ema21"].shift(1)) &
            (df5["rsi"] > 30) &
            htf_bear &
            vol_ok &
            self._in_session(df5) &
            ~self._in_blackout(df5)
        )

        rows = []
        for ts, bull in bullish[bullish].items():
            atr_val = df5.loc[ts, "atr"]
            rows.append({"timestamp": ts, "direction": "CE", "atr": atr_val})
        for ts, bear in bearish[bearish].items():
            atr_val = df5.loc[ts, "atr"]
            rows.append({"timestamp": ts, "direction": "PE", "atr": atr_val})

        if not rows:
            return pd.DataFrame(columns=["timestamp", "direction", "atr"])
            
        return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)

    # ---- VWAP (Price vs VWAP + EMA20) ---- #

    def _vwap_signals(self) -> pd.DataFrame:
        df5 = self.df_5m.copy()
        df5["vwap"] = self._rolling_vwap(df5)
        df5["ema20"] = df5["close"].ewm(span=20, adjust=False).mean()
        df5["atr"]   = self._atr(df5, 14)

        bullish = (
            (df5["close"] > df5["vwap"]) &
            (df5["close"].shift(1) <= df5["vwap"].shift(1)) &   # cross above
            (df5["close"] > df5["ema20"]) &
            self._in_session(df5) & ~self._in_blackout(df5)
        )
        bearish = (
            (df5["close"] < df5["vwap"]) &
            (df5["close"].shift(1) >= df5["vwap"].shift(1)) &
            (df5["close"] < df5["ema20"]) &
            self._in_session(df5) & ~self._in_blackout(df5)
        )

        rows = []
        for ts in bullish[bullish].index:
            rows.append({"timestamp": ts, "direction": "CE", "atr": df5.loc[ts, "atr"]})
        for ts in bearish[bearish].index:
            rows.append({"timestamp": ts, "direction": "PE", "atr": df5.loc[ts, "atr"]})
            
        if not rows:
            return pd.DataFrame(columns=["timestamp", "direction", "atr"])
            
        return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)

    # ---- ORB (Opening Range Breakout 09:15-09:45) ---- #

    def _orb_signals(self) -> pd.DataFrame:
        df1 = self.df_1m.copy()
        df1["atr"] = self._atr(df1, 14)

        rows = []
        for date, day_df in df1.groupby(df1.index.date):
            opening = day_df.between_time("09:15", "09:45")
            if opening.empty:
                continue
            orb_high = opening["high"].max()
            orb_low  = opening["low"].min()

            post_orb = day_df.between_time("09:46", "10:30")
            if post_orb.empty:
                continue

            broken_up   = post_orb[post_orb["close"] > orb_high]
            broken_down = post_orb[post_orb["close"] < orb_low]

            if not broken_up.empty:
                ts = broken_up.index[0]
                rows.append({"timestamp": ts, "direction": "CE", "atr": day_df.loc[ts, "atr"]})
            elif not broken_down.empty:
                ts = broken_down.index[0]
                rows.append({"timestamp": ts, "direction": "PE", "atr": day_df.loc[ts, "atr"]})

        return pd.DataFrame(rows).reset_index(drop=True)

    # ---- INSIDE BAR (15m Mother-Baby pattern) ---- #

    def _inside_bar_signals(self) -> pd.DataFrame:
        df15 = self.df_15m.copy()
        df15["atr"] = self._atr(df15, 14)

        mother_high = df15["high"].shift(1)
        mother_low  = df15["low"].shift(1)
        is_inside   = (df15["high"] < mother_high) & (df15["low"] > mother_low)

        rows = []
        for date, day_df in df15.groupby(df15.index.date):
            inside_bars = day_df[is_inside.reindex(day_df.index, fill_value=False)]
            if inside_bars.empty:
                continue

            for ts, row in inside_bars.iterrows():
                bar_t = ts.time()
                if bar_t < dtime(9, 30) or bar_t > dtime(14, 0):
                    continue
                # Breakout direction determined by close vs mother midpoint
                mid = (mother_high[ts] + mother_low[ts]) / 2
                direction = "CE" if row["close"] > mid else "PE"
                rows.append({"timestamp": ts, "direction": direction, "atr": row["atr"]})

        return pd.DataFrame(rows).reset_index(drop=True)

    # ---- OHL SCALP (09:15 candle: Open==High → bear, Open==Low → bull) ---- #

    def _ohl_signals(self) -> pd.DataFrame:
        df1 = self.df_1m.copy()
        rows = []
        for date, day_df in df1.groupby(df1.index.date):
            opening_candles = day_df.between_time("09:15", "09:16")
            if opening_candles.empty:
                continue
            c = opening_candles.iloc[0]
            atr = self._atr(day_df, 14).iloc[-1] if len(day_df) >= 14 else 20.0

            # Open ≈ High → strong sellers → buy PE
            if abs(c["open"] - c["high"]) <= 1:
                rows.append({"timestamp": opening_candles.index[0], "direction": "PE", "atr": atr})
            # Open ≈ Low  → strong buyers → buy CE
            elif abs(c["open"] - c["low"]) <= 1:
                rows.append({"timestamp": opening_candles.index[0], "direction": "CE", "atr": atr})

        return pd.DataFrame(rows).reset_index(drop=True)

    # ---- GAMMA BLAST (ADX > 45 parabolic trending) ---- #

    def _gamma_blast_signals(self) -> pd.DataFrame:
        df5 = self.df_5m.copy()
        df5["adx"] = self._adx(df5, 14)
        df5["atr"] = self._atr(df5, 14)
        df5["ema9"]  = df5["close"].ewm(span=9,  adjust=False).mean()
        df5["ema21"] = df5["close"].ewm(span=21, adjust=False).mean()

        bullish = (
            (df5["adx"] > 45) &
            (df5["ema9"] > df5["ema21"]) &
            self._in_session(df5) & ~self._in_blackout(df5)
        )
        bearish = (
            (df5["adx"] > 45) &
            (df5["ema9"] < df5["ema21"]) &
            self._in_session(df5) & ~self._in_blackout(df5)
        )

        rows = []
        for ts in bullish[bullish].index:
            rows.append({"timestamp": ts, "direction": "CE", "atr": df5.loc[ts, "atr"]})
        for ts in bearish[bearish].index:
            rows.append({"timestamp": ts, "direction": "PE", "atr": df5.loc[ts, "atr"]})
            
        if not rows:
            return pd.DataFrame(columns=["timestamp", "direction", "atr"])
            
        return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)

    # ---- STRADDLE SCALP (ADX < 20 Sideways Market) ---- #

    def _straddle_scalp_signals(self) -> pd.DataFrame:
        df5 = self.df_5m.copy()
        df5["adx"] = self._adx(df5, 14)
        df5["atr"] = self._atr(df5, 14)

        # Signal: Entry when ADX < 20 and it's morning session
        # Strategy doesn't care about direction (buys both CE and PE)
        # Session filtering is handled by _simulate_session as well.
        entry_signals = (
            (df5["adx"] < 20) &
            self._in_session(df5) &
            (df5.index.time <= dtime(11, 0))  # Strategy limit
        )

        rows = []
        for ts in entry_signals[entry_signals].index:
            rows.append({"timestamp": ts, "direction": "STRADDLE", "atr": df5.loc[ts, "atr"]})
            
        if not rows:
            return pd.DataFrame(columns=["timestamp", "direction", "atr"])
            
        return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)

    # ------------------------------------------------------------------ #
    #  Trade Simulation Engine                                             #
    # ------------------------------------------------------------------ #

    def _simulate_session(self, signals: pd.DataFrame, strategy_name: str):
        """
        Walk through signals day-by-day, apply SafetyGatekeeper-equivalent rules,
        and simulate trade outcomes using the 1m candle data.
        """
        df1 = self.df_1m

        capital   = self.initial_capital
        equity_curve: list[dict] = [{"ts": df1.index[0], "capital": capital}]
        trades:    list[dict] = []
        daily_pnl = {}   # date → float
        daily_count = {} # date → int

        for _, signal in signals.iterrows():
            ts        = signal["timestamp"]
            direction = signal["direction"]
            atr       = signal["atr"] if signal["atr"] > 0 else 20.0
            date      = ts.date()

            # --- Daily caps (SafetyGatekeeper parity) ---
            d_pnl   = daily_pnl.get(date, 0.0)
            d_count = daily_count.get(date, 0)

            if d_pnl <= self.max_daily_loss:
                continue  # Circuit breaker
            if d_count >= self.max_trades_per_day:
                continue

            # Blackout / session filter (already in signals but double-check)
            bar_t = ts.time()
            if bar_t >= dtime(15, 15) or bar_t < dtime(9, 15):
                continue
            if dtime(11, 30) <= bar_t <= dtime(13, 0):
                continue

            # --- Option premium & sizing ---
            if direction == "STRADDLE":
                # Combined premium of CE + PE (ATM)
                entry_premium = 2 * (atr * self.option_premium_atr_mult)
                delta, sl_mult, tgt_mult = 0.50, 0.15, 0.20   # 15% SL, 20% Target (Combined)
            else:
                entry_premium = max(5.0, atr * self.option_premium_atr_mult)
                # Volatility-adjusted strike: high ATR → deeper OTM (lower premium ÷ higher leverage)
                # We stay in premium terms and adjust risk/target multipliers instead.
                if atr < 15:
                    # Low vol: tighter spreads, use ATM (delta 0.50)
                    delta, sl_mult, tgt_mult = 0.50, 0.25, 0.50   # SL 25%, Target 50%
                elif atr < 30:
                    # Medium vol: 1 OTM (delta 0.35)
                    delta, sl_mult, tgt_mult = 0.35, 0.30, 0.60
                else:
                    # High vol: 2 OTM (delta 0.25) — bigger swings expected
                    delta, sl_mult, tgt_mult = 0.25, 0.35, 0.70

            sl_price     = entry_premium * (1 - sl_mult)
            target_price = entry_premium * (1 + tgt_mult)

            # Phase 3: Capital & Lot Sizing Optimization (₹35,000 Specific)
            if 30000 <= capital <= 40000:
                if entry_premium <= 80:
                    lots = 2
                    logger.debug(f"[Backtest] Sizing: Premium ₹{entry_premium:.1f} <= 80. Trading 2 lots.")
                elif entry_premium > 100:
                    lots = 1
                    logger.debug(f"[Backtest] Sizing: Premium ₹{entry_premium:.1f} > 100. Trading 1 lot.")
                else:
                    lots = 1
            else:
                # Default position sizing: risk-based
                risk_amount  = capital * self.risk_per_trade_pct
                max_loss_per_lot = (entry_premium - sl_price) * self.lot_size
                lots = max(1, int(risk_amount / max_loss_per_lot)) if max_loss_per_lot > 0 else 1
            
            # Realistic Cap: Never trade more than max_lots
            lots = min(lots, self.max_lots)
            qty  = lots * self.lot_size

            # Margin check: estimated cost <= 90% of capital
            estimated_cost = entry_premium * qty
            if estimated_cost > capital * 0.90:
                lots = max(1, int(capital * 0.90 / (entry_premium * self.lot_size)))
                qty  = lots * self.lot_size
                estimated_cost = entry_premium * qty

            if estimated_cost > capital:
                continue  # Genuinely unaffordable

            # --- Apply Entry Slippage ---
            entry_premium_slippage = entry_premium * (1 + self.slippage_pct)

            # Brokerage + Realistic Taxes (STT, GST, Transaction Charges ≈ 0.1% of turnover)
            brokerage = self.brokerage_per_lot * lots * 2
            turnover = (entry_premium_slippage + (entry_premium_slippage * 1.5)) * qty # Rough estimate
            taxes = turnover * 0.001 
            total_cost = brokerage + taxes

            # --- Simulate trade outcome on 1m bars ---
            future_bars = df1[df1.index > ts]
            future_day  = future_bars[future_bars.index.date == date]
            future_day  = future_day[future_day.index.time <= dtime(15, 15)]

            if future_day.empty:
                continue

            # Enable progressive trail for GAMMA_BLAST and MOMENTUM
            use_progressive = (strategy_name in ["GAMMA_BLAST", "MOMENTUM"])

            exit_price, exit_reason, exit_time = self._find_exit(
                future_day, direction, entry_premium, sl_price, target_price,
                atr, delta, qty, use_progressive_trail=use_progressive
            )

            # --- Apply Exit Slippage ---
            exit_price_slippage = exit_price * (1 - self.slippage_pct)

            pnl = (exit_price_slippage - entry_premium_slippage) * qty - total_cost
            capital += pnl
            daily_pnl[date]   = daily_pnl.get(date, 0.0) + pnl
            daily_count[date] = d_count + 1

            trade_record = {
                "date":         str(date),
                "timestamp":    str(ts),
                "strategy":     strategy_name,
                "direction":    direction,
                "entry":        round(entry_premium, 2),
                "exit":         round(exit_price, 2),
                "sl":           round(sl_price, 2),
                "target":       round(target_price, 2),
                "qty":          qty,
                "lots":         lots,
                "pnl":          round(pnl, 2),
                "exit_reason":  exit_reason,
                "exit_time":    str(exit_time),
                "capital_after":round(capital, 2),
                "atr":          round(atr, 2),
                "delta":        delta,
            }
            trades.append(trade_record)
            equity_curve.append({"ts": ts, "capital": capital})

            entry_raw = trade_record['timestamp']
            exit_raw = trade_record['exit_time']
            entry_t = entry_raw.split(' ')[1][:5] if ' ' in entry_raw else entry_raw[:5]
            exit_t = exit_raw.split(' ')[1][:5] if ' ' in exit_raw else exit_raw[:5]
            icon = "📈" if pnl > 0 else "📉"
            logger.debug(f"      {icon} {entry_t} -> {exit_t} | PnL: ₹{trade_record['pnl']:,.0f} ({trade_record['exit_reason']})")

        return trades, equity_curve

    def _find_exit(
        self, future_bars, direction, entry_price, sl_price, target_price, atr, delta, qty,
        use_progressive_trail=False
    ):
        """
        Walk through 1m bars after entry to find the first exit.
        
        Optional: use_progressive_trail (Stage-Gate System)
        Stage 1: PnL >= 1500 -> SL = Entry + 16
        Stage 2: PnL >= 2600 -> SL = Entry + 30
        Stage 3: PnL >= 3900 -> 1m 9-EMA Trail
        """
        entry_index = future_bars["close"].iloc[0]
        current_sl = sl_price
        initial_risk = abs(entry_price - sl_price)
        stage = 0
        
        # Gamma Effect: Delta increases as index moves in favor
        # 0.005 increase per 1pt index move is a common OTM gamma proxy
        gamma_factor = 0.005 
        current_delta = delta

        for ts, row in future_bars.iterrows():
            if direction == "STRADDLE":
                entry_leg = entry_price / 2
                # CE leg: move up is profit
                move_ce = (row["close"] - entry_index)
                adj_delta_ce = max(0.05, min(1.0, current_delta + (move_ce * gamma_factor)))
                price_ce = entry_leg + (move_ce * (current_delta + adj_delta_ce) / 2)
                # PE leg: move down is profit
                move_pe = (entry_index - row["close"])
                adj_delta_pe = max(0.05, min(1.0, current_delta + (move_pe * gamma_factor)))
                price_pe = entry_leg + (move_pe * (current_delta + adj_delta_pe) / 2)
                
                option_price = max(0.1, price_ce + price_pe)
            else:
                index_move = (row["close"] - entry_index) if direction == "CE" else (entry_index - row["close"])
                
                # Non-linear price simulation (Gamma proxy)
                # Delta increases for profit, caps at 1.0 (Deep ITM)
                # Delta decreases for loss, floors at 0.05 (Deep OTM)
                adj_delta = max(0.05, min(1.0, current_delta + (index_move * gamma_factor)))
                # Option price using average delta over the move
                avg_delta = (current_delta + adj_delta) / 2
                option_price = entry_price + (index_move * avg_delta)
                option_price = max(0.05, option_price)

            if use_progressive_trail:
                unrealized_pnl = (option_price - entry_price) * qty
                
                # Stage 1: The ₹1,500 Floor
                if stage < 1 and unrealized_pnl >= 1500:
                    new_sl = entry_price + 16
                    if new_sl > current_sl:
                        current_sl = new_sl
                        stage = 1
                
                # Stage 2: The Buffer
                if stage < 2 and unrealized_pnl >= 2600:
                    new_sl = entry_price + 30
                    if new_sl > current_sl:
                        current_sl = new_sl
                        stage = 2
                
                # Stage 3: The 3R Hunter (1m 9-EMA Trail)
                if stage < 3 and unrealized_pnl >= 3900:
                    stage = 3
                
                if stage == 3:
                    # Simulate 1m 9-EMA Trail (approximate)
                    # For backtest, we use the row's close as a proxy for EMA9 if we don't calculate it fully here.
                    # Actually, we can calculate EMA9 on the fly or just use a tight trail.
                    # To be accurate, we'll use a tight 5-point trail in Stage 3 for the backtest.
                    new_sl = option_price - 5
                    if new_sl > current_sl:
                        current_sl = new_sl

                # --- STAGE 4: MOONSHOT MODE (X-FACTOR) ---
                if stage < 4 and unrealized_pnl >= 5000:
                    # In backtest, we simulate partial sell by adjusting current_sl
                    # to a 'Deep-Safe' zone and reducing future pnl impact.
                    # We lock in 50 points profit immediately.
                    new_sl = entry_price + 50
                    if new_sl > current_sl:
                        current_sl = new_sl
                        stage = 4
                        # Note: We don't reduce qty in vectorized backtest for simplicity,
                        # but locking the SL at +50 mimics the 'Safe Capital' effect.

                if stage >= 3:
                    # Trailing Stop: 1m 9-EMA or tight trail
                    # Use a 10% of premium trailing stop in runner mode
                    trail_sl = option_price * 0.90
                    if trail_sl > current_sl:
                        current_sl = trail_sl

            # Exits
            if ts.time() >= dtime(15, 15):
                return option_price, "TIME_EXIT", ts

            if option_price <= current_sl:
                reason = "STOPLOSS" if stage == 0 else f"TRAIL_HIT_S{stage}"
                return current_sl, reason, ts

            # Fixed Target Exit (only if progressive trail is OFF)
            if not use_progressive_trail and option_price >= target_price:
                return target_price, "TARGET", ts
            
            # Dream Target (10:1) for Progressive strategies
            if use_progressive_trail and option_price >= entry_price + (10 * initial_risk):
                return option_price, "DREAM_TARGET", ts

        # End of data = time exit at last price
        last_idx  = future_bars["close"].iloc[-1]
        idx_move  = last_idx - entry_index
        final_opt = max(0.05, entry_price + (idx_move * delta if direction == "CE" else -idx_move * delta))
        return final_opt, "TIME_EXIT", future_bars.index[-1]

    # ------------------------------------------------------------------ #
    #  Performance Metrics                                                 #
    # ------------------------------------------------------------------ #

    def _calculate_metrics(
        self, trades: list, equity_curve: list, trading_days: int, strategy_name: str
    ) -> dict:
        if not trades:
            return self._empty_metrics()

        df_trades = pd.DataFrame(trades)
        total_trades = len(df_trades)
        wins = df_trades[df_trades["pnl"] > 0]
        win_rate = len(wins) / total_trades * 100

        final_capital = equity_curve[-1]["capital"]
        total_pnl     = final_capital - self.initial_capital

        # CAGR: annualise over trading days (252/year)
        trading_years = trading_days / 252
        if trading_years > 0 and self.initial_capital > 0:
            cagr = ((final_capital / self.initial_capital) ** (1 / trading_years) - 1) * 100
        else:
            cagr = 0.0

        # Max Drawdown
        eq_vals = [e["capital"] for e in equity_curve]
        peak    = eq_vals[0]
        max_dd  = 0.0
        for v in eq_vals:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # Sharpe Ratio (daily returns)
        eq_series = pd.Series(eq_vals)
        daily_ret = eq_series.pct_change().dropna()
        rf_daily  = 0.065 / 252  # 6.5% risk-free rate
        if daily_ret.std() > 0:
            sharpe = (daily_ret.mean() - rf_daily) / daily_ret.std() * (252 ** 0.5)
        else:
            sharpe = 0.0

        # Average P&L per trade
        avg_pnl  = df_trades["pnl"].mean()
        avg_win  = wins["pnl"].mean() if not wins.empty else 0.0
        losses   = df_trades[df_trades["pnl"] <= 0]
        avg_loss = losses["pnl"].mean() if not losses.empty else 0.0

        # Profit factor
        gross_profit = wins["pnl"].sum() if not wins.empty else 0.0
        gross_loss   = abs(losses["pnl"].sum()) if not losses.empty else 1.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Exit reason breakdown
        exit_counts = df_trades["exit_reason"].value_counts().to_dict()

        metrics = {
            "strategy":       strategy_name,
            "initial_capital":self.initial_capital,
            "final_capital":  round(final_capital, 2),
            "total_pnl":      round(total_pnl, 2),
            "cagr_pct":       round(cagr, 2),
            "max_drawdown_pct":round(max_dd, 2),
            "sharpe_ratio":   round(sharpe, 2),
            "win_rate_pct":   round(win_rate, 2),
            "total_trades":   total_trades,
            "avg_pnl":        round(avg_pnl, 2),
            "avg_win":        round(avg_win, 2),
            "avg_loss":       round(avg_loss, 2),
            "profit_factor":  round(profit_factor, 2),
            "trading_days":   trading_days,
            "exit_breakdown": exit_counts,
            "trades":         trades,  # Full trade log
        }

        logger.info(
            f"[Backtest] {strategy_name} → "
            f"CAGR={cagr:.1f}% | MaxDD={max_dd:.1f}% | "
            f"Sharpe={sharpe:.2f} | WinRate={win_rate:.1f}% | "
            f"Trades={total_trades}"
        )
        return metrics

    def _empty_metrics(self) -> dict:
        return {
            "cagr_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe_ratio": 0.0,
            "win_rate_pct": 0.0, "total_trades": 0, "profit_factor": 0.0,
            "total_pnl": 0.0, "trades": [],
        }

    # ------------------------------------------------------------------ #
    #  Technical Indicator Helpers (vectorized Pandas)                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        hl  = df["high"] - df["low"]
        hc  = (df["high"] - df["close"].shift(1)).abs()
        lc  = (df["low"]  - df["close"].shift(1)).abs()
        tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean().fillna(0)

    @staticmethod
    def _rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        delta = df["close"].diff()
        gain  = delta.where(delta > 0, 0).ewm(alpha=1 / period, adjust=False).mean()
        loss  = (-delta.where(delta < 0, 0)).ewm(alpha=1 / period, adjust=False).mean()
        rs    = gain / loss.replace(0, 1e-10)
        return (100 - 100 / (1 + rs)).fillna(50)

    @staticmethod
    def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        h_diff = df["high"].diff()
        l_diff = df["low"].diff()
        plus_dm  = h_diff.where((h_diff > l_diff.abs()) & (h_diff > 0), 0)
        minus_dm = (-l_diff).where((l_diff.abs() > h_diff) & (l_diff < 0), 0)

        hl  = df["high"] - df["low"]
        hc  = (df["high"] - df["close"].shift(1)).abs()
        lc  = (df["low"]  - df["close"].shift(1)).abs()
        tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / period, adjust=False).mean().replace(0, 1e-10)

        plus_di  = 100 * (plus_dm.ewm(alpha=1 / period,  adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr)
        dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)
        return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0)

    @staticmethod
    def _rolling_vwap(df: pd.DataFrame) -> pd.Series:
        """Daily session VWAP (resets each trading day)."""
        vwap_vals = pd.Series(index=df.index, dtype=float)
        for date, day_df in df.groupby(df.index.date):
            tp  = (day_df["high"] + day_df["low"] + day_df["close"]) / 3
            cum_tpv = (tp * day_df["volume"]).cumsum()
            cum_vol = day_df["volume"].cumsum().replace(0, 1e-10)
            vwap_vals.loc[day_df.index] = cum_tpv / cum_vol
        return vwap_vals

    @staticmethod
    def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
        return df.resample(rule).agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        ).dropna()

    # ---- Session filter helpers ---- #

    @staticmethod
    def _in_session(df: pd.DataFrame) -> pd.Series:
        t = df.index.time
        return pd.Series(
            [(dtime(9, 15) <= x <= dtime(15, 29)) for x in t],
            index=df.index, dtype=bool
        )

    @staticmethod
    def _in_blackout(df: pd.DataFrame) -> pd.Series:
        t = df.index.time
        return pd.Series(
            [(dtime(11, 30) <= x <= dtime(13, 0)) for x in t],
            index=df.index, dtype=bool
        )
