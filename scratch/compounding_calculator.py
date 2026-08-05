import sys
import os
import pandas as pd
from datetime import time as dtime

# Add project root to sys.path
sys.path.append(os.getcwd())

from bot.core.backtest_engine import BacktestEngine
from bot.config.settings import Config
from bot.utils.logger import logger

def simulate_dynamic_compounding(start_capital=91000.0, target_capital=500000.0):
    DATA_PATH = os.path.join(os.getcwd(), "data", "historical_nifty_1m.csv")
    if not os.path.exists(DATA_PATH):
        print("❌ Historical data not found.")
        return

    df = pd.read_csv(DATA_PATH, parse_dates=['timestamp']).set_index('timestamp')
    
    # We will run both strategies but select the one that triggers.
    # In live trading, the DecisionEngine selects the best strategy.
    # Here we will simulate how the capital grows if we trade the recommended strategy: GAMMA_BLAST
    # (since it is the primary trending strategy).
    
    print(f"--- Running Dynamic Compounding Simulation ---")
    print(f"Start Capital: ₹{start_capital:,.2f}")
    print(f"Target Capital: ₹{target_capital:,.2f}")
    
    # We'll run multiple passes of the 30-day data if needed to reach the target,
    # simulating how many months of similar market conditions it takes.
    
    current_capital = start_capital
    days_elapsed = 0
    trade_count = 0
    pass_number = 1
    
    while current_capital < target_capital and pass_number <= 12: # max 1 year simulation
        # For each pass, we re-run the backtest engine
        # But we customize the simulation loop to dynamically adjust tier settings at each trade!
        engine = BacktestEngine.from_capital(initial_capital=current_capital)
        engine.load_data(df)
        
        # Generate signals for GAMMA_BLAST (our primary high-conviction strategy)
        signals = engine._generate_signals("GAMMA_BLAST")
        if signals.empty:
            break
            
        # Walk through trades and apply dynamic tier settings
        df1 = engine.df_1m
        last_exit_time = None
        daily_pnl = {}
        daily_count = {}
        
        for _, signal in signals.iterrows():
            ts = signal["timestamp"]
            direction = signal["direction"]
            atr = signal["atr"] if signal["atr"] > 0 else 20.0
            date = ts.date()
            
            # Since we loop the same data, we map the date to a virtual date to avoid daily cap overlaps
            virtual_date = f"pass_{pass_number}_{date}"
            
            if last_exit_time is not None and ts < last_exit_time:
                continue
                
            # Dynamic Tier Lookup based on CURRENT running capital!
            tier = Config.get_tier(current_capital)
            
            # Apply dynamic risk settings of the tier
            max_daily_loss = -(current_capital * tier.max_daily_loss_pct)
            risk_per_trade_pct = tier.risk_per_trade_pct
            max_trades_per_day = tier.max_trades_per_day
            max_lots = tier.max_lots  # Live strict limit!
            
            d_pnl = daily_pnl.get(virtual_date, 0.0)
            d_count = daily_count.get(virtual_date, 0)
            
            if d_pnl <= max_daily_loss:
                continue
            if d_count >= max_trades_per_day:
                continue
                
            # Option premium & sizing
            entry_premium = max(5.0, atr * engine.option_premium_atr_mult)
            if atr < 15:
                delta, sl_mult, tgt_mult = 0.50, tier.sl_pct, 0.50
            elif atr < 30:
                delta, sl_mult, tgt_mult = 0.35, tier.sl_pct * 1.2, 0.60
            else:
                delta, sl_mult, tgt_mult = 0.25, tier.sl_pct * 1.5, 0.70
                
            sl_price = entry_premium * (1 - sl_mult)
            target_price = entry_premium * (1 + tgt_mult)
            
            # Live sizing rules
            risk_amount = current_capital * risk_per_trade_pct
            max_loss_per_lot = (entry_premium - sl_price) * engine.lot_size
            lots = max(1, int(risk_amount / max_loss_per_lot)) if max_loss_per_lot > 0 else 1
            
            if max_lots > 0:
                lots = min(lots, max_lots)
                
            qty = lots * engine.lot_size
            estimated_cost = entry_premium * qty
            if estimated_cost > current_capital * 0.90:
                lots = max(1, int(current_capital * 0.90 / (entry_premium * engine.lot_size)))
                if max_lots > 0:
                    lots = min(lots, max_lots)
                qty = lots * engine.lot_size
                estimated_cost = entry_premium * qty
                
            if estimated_cost > current_capital:
                continue
                
            # Entry slippage & transaction costs
            current_slippage_pct = engine.slippage_pct * (0.5 if atr < 15 else (1.5 if atr > 30 else 1.0))
            entry_premium_slippage = entry_premium * (1 + current_slippage_pct)
            brokerage = engine.brokerage_per_lot * lots * 2
            turnover = (entry_premium_slippage + (entry_premium_slippage * 1.5)) * qty
            taxes = turnover * 0.001
            total_cost = brokerage + taxes
            
            # Exit simulation
            execution_ts = ts + pd.Timedelta(minutes=5)
            future_bars = df1[df1.index >= execution_ts]
            future_day = future_bars[future_bars.index.date == date]
            future_day = future_day[future_day.index.time <= dtime(15, 10)]
            
            if future_day.empty:
                continue
                
            exit_price, exit_reason, exit_time = engine._find_exit(
                future_day, direction, entry_premium, sl_price, target_price,
                atr, delta, qty, use_progressive_trail=True
            )
            
            exit_price_slippage = exit_price * (1 - current_slippage_pct)
            pnl = (exit_price_slippage - entry_premium_slippage) * qty - total_cost
            
            current_capital += pnl
            daily_pnl[virtual_date] = d_pnl + pnl
            daily_count[virtual_date] = d_count + 1
            last_exit_time = exit_time
            trade_count += 1
            
            # Print milestone checks
            if current_capital >= target_capital:
                break
                
        # End of pass
        trading_days_in_pass = df.index.normalize().nunique()
        days_elapsed += trading_days_in_pass
        print(f"Pass {pass_number} (Month {pass_number}) completed. Equity: ₹{current_capital:,.2f} | Tier: {Config.get_tier(current_capital).name}")
        
        if current_capital >= target_capital:
            break
            
        pass_number += 1
        
    print("\n" + "="*50)
    print("      🏆 COMPOUNDING SIMULATION RESULT")
    print("="*50)
    print(f"Start Capital:  ₹{start_capital:,.2f}")
    print(f"End Capital:    ₹{current_capital:,.2f}")
    print(f"Total Trades:   {trade_count}")
    print(f"Time Taken:     {days_elapsed} trading days (~{days_elapsed/20:.1f} months)")
    print(f"Final Tier:     {Config.get_tier(current_capital).name}")
    print("="*50)

if __name__ == "__main__":
    simulate_dynamic_compounding()
