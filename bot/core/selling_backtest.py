
import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
from bot.selling_engine.core.strike_selector import StrikeSelector
from bot.selling_engine.config import SELLING_CONFIG
from bot.utils.logger import logger

class SellingBacktest:
    """
    Specialized Backtest Engine for Option Selling.
    Simulates Theta decay, Delta sensitivity, and Margin requirements.
    """
    def __init__(self, initial_capital=200000):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.selector = StrikeSelector(SELLING_CONFIG)
        self.lot_size = SELLING_CONFIG["lot_size"]
        self.margin_per_ic = 60000  # Conservative margin for 1 lot Iron Condor
        
        self.trade_log = []

    def run(self, df_1m):
        logger.info(f">>> [Backtest] Starting Selling Backtest with ₹{self.initial_capital:,.0f}...")
        
        # Ensure index is datetime
        if not isinstance(df_1m.index, pd.DatetimeIndex):
            df_1m.index = pd.to_datetime(df_1m.index)
        
        # Group by week (Monday to Thursday)
        df_1m['week'] = df_1m.index.to_period('W-THU')
        weeks = df_1m.groupby('week')
        
        for week_period, week_data in weeks:
            self._simulate_weekly_cycle(week_data, week_period)
            
        return self._generate_report()

    def _simulate_weekly_cycle(self, df, week_period):
        """Simulates one weekly expiry cycle (Monday entry, Wednesday/Thursday exit)."""
        # 1. Entry Check (Monday Window)
        entry_day = SELLING_CONFIG["ic_entry_day"] # Monday
        monday_data = df[df.index.strftime('%A') == entry_day]
        if monday_data.empty:
            return

        # Entry Window: 09:30 - 11:00
        entry_window = monday_data.between_time("09:30", "11:00")
        if entry_window.empty:
            return
            
        entry_bar = entry_window.iloc[0]
        spot_at_entry = entry_bar['close']
        vix = 15.0 # Proxy if not in data
        
        # 2. Strike Selection
        # We need DTE for the selector
        expiry_date = df.index[-1].date()
        dte = (expiry_date - entry_bar.name.date()).days
        
        strikes = self.selector.select_iron_condor_strikes(spot_at_entry, vix, dte)
        
        # 3. Premium Estimation (Initial)
        # For a 100-point gap IC at 0.15 Delta, net credit is usually ~100-110 points on Monday
        entry_price = 100.0 
        current_credit = entry_price
        
        # Sizing
        lots = int(self.capital * 0.8 / self.margin_per_ic)
        lots = max(1, lots)
        qty = lots * self.lot_size
        
        # 4. Monitoring the Week
        future_data = df[df.index > entry_bar.name]
        
        current_sl_points = entry_price * SELLING_CONFIG["ic_stop_loss_multiplier"]
        tp_price = entry_price * (1 - (SELLING_CONFIG["ic_take_profit_pct"]/100))
        
        exit_bar = None
        exit_reason = "EXPIRY"
        final_premium = 0.0
        adjustments_made = 0
        
        active_strikes = strikes.copy()
        
        for ts, row in future_data.iterrows():
            spot_now = row['close']
            
            # --- a) ADJUSTMENT LOGIC ---
            from bot.selling_engine.core.adjustment import AdjustmentManager
            adj_mgr = AdjustmentManager(SELLING_CONFIG)
            
            # Prepare position dict for adj_mgr
            pos_dict = {
                "strategy": "iron_condor",
                "short_call": active_strikes["short_call"],
                "short_put": active_strikes["short_put"]
            }
            
            adj_decision = adj_mgr.check_adjustment_needed(pos_dict, spot_now)
            
            if adj_decision["adjustment_needed"] and adjustments_made < 2:
                # Roll!
                old_short = adj_decision["old_short"]
                new_short = adj_decision["new_short"]
                
                # Update strikes
                if adj_decision["side"] == "call":
                    active_strikes["short_call"] = new_short
                    active_strikes["long_call"] = new_short + SELLING_CONFIG["ic_wing_gap"]
                else:
                    active_strikes["short_put"] = new_short
                    active_strikes["long_put"] = new_short - SELLING_CONFIG["ic_wing_gap"]
                
                # Rolling closer collects more premium (e.g. 15-20 points extra)
                current_credit += 20.0
                adjustments_made += 1
                logger.debug(f"      🔧 [Adjust] {adj_decision['action']} at {ts}")

            # --- b) PRICING MODEL ---
            # Time Decay (Linear)
            time_elapsed_pct = (ts - entry_bar.name) / (df.index[-1] - entry_bar.name)
            theta_decay = current_credit * 0.8 * time_elapsed_pct
            
            # Intrinsic Risk (Gamma)
            intrinsic_risk = 0.0
            if spot_now > active_strikes['short_call']:
                intrinsic_risk = (spot_now - active_strikes['short_call']) * 1.8 
            elif spot_now < active_strikes['short_put']:
                intrinsic_risk = (active_strikes['short_put'] - spot_now) * 1.8
            
            current_premium = max(1.0, current_credit - theta_decay + intrinsic_risk)
            
            # --- c) EXIT CHECKS ---
            if current_premium >= current_sl_points:
                exit_bar = row
                exit_reason = "STOP_LOSS"
                final_premium = current_sl_points
                break
            
            if current_premium <= tp_price:
                exit_bar = row
                exit_reason = "TAKE_PROFIT"
                final_premium = tp_price
                break
                
            if ts.strftime('%A') == "Wednesday" and ts.time() >= time(14, 0):
                exit_bar = row
                exit_reason = "TIME_EXIT"
                final_premium = current_premium
                break

        if exit_bar is None:
            exit_bar = df.iloc[-1]
            final_premium = current_premium
            
        # 5. PnL Calculation
        # Profit = (Total Credit Collected - Final Premium Paid) * Qty
        gross_pnl = (current_credit - final_premium) * qty
        # Charges (Extra per adjustment)
        brokerage = 20 * 4 * 2 * lots + (adjustments_made * 20 * 2 * 2 * lots)
        net_pnl = gross_pnl - brokerage
        
        self.capital += net_pnl
        
        self.trade_log.append({
            "week": str(week_period),
            "entry_time": entry_bar.name,
            "exit_time": exit_bar.name,
            "spot_entry": spot_at_entry,
            "spot_exit": exit_bar['close'],
            "strikes": f"C:{strikes['short_call']} P:{strikes['short_put']}",
            "entry_premium": round(entry_price, 2),
            "exit_premium": round(final_premium, 2),
            "pnl": round(net_pnl, 2),
            "reason": exit_reason,
            "capital": round(self.capital, 2)
        })

    def _generate_report(self):
        if not self.trade_log:
            return {"error": "No trades executed"}
            
        df = pd.DataFrame(self.trade_log)
        total_pnl = self.capital - self.initial_capital
        win_rate = (df['pnl'] > 0).mean() * 100
        
        report = {
            "initial_capital": self.initial_capital,
            "final_capital": self.capital,
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "total_trades": len(df),
            "max_drawdown": self._calculate_drawdown(df),
            "trades": self.trade_log
        }
        
        logger.info(f">>> [Backtest] Finished. Total P&L: ₹{total_pnl:,.0f} | Win Rate: {win_rate:.1f}%")
        return report

    def _calculate_drawdown(self, df):
        capitals = df['capital'].tolist()
        peak = capitals[0]
        max_dd = 0
        for c in capitals:
            if c > peak:
                peak = c
            dd = (peak - c) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 2)

if __name__ == "__main__":
    # Test with historical data
    import os
    hist_path = "data/historical_nifty_1m.csv"
    if os.path.exists(hist_path):
        df = pd.read_csv(hist_path, index_col=0, parse_dates=True)
        # Rename columns to lowercase if they are uppercase
        df.columns = [c.lower() for c in df.columns]
        
        bt = SellingBacktest(initial_capital=200000)
        results = bt.run(df)
        
        print("\n--- SELLING BACKTEST RESULTS ---")
        print(f"Initial Capital: ₹{results['initial_capital']:,.0f}")
        print(f"Final Capital:   ₹{results['final_capital']:,.0f}")
        print(f"Total P&L:       ₹{results['total_pnl']:,.0f}")
        print(f"Win Rate:        {results['win_rate']:.1f}%")
        print(f"Max Drawdown:    {results['max_drawdown']}%")
        print("---------------------------------\n")
    else:
        print(f"Historical data file not found at {hist_path}")
