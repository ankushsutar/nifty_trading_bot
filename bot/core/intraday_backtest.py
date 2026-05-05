
import pandas as pd
from datetime import time

class IntradayStraddleBacktest:
    """
    Simulates the 09:20 ATM Straddle / Strangle.
    Entry: 09:20 AM
    Exit: 15:10 PM or 25% SL per leg.
    """
    def __init__(self, initial_capital=200000):
        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.lot_size = 50
        self.slippage = 0.5 # 0.5 points per side
        self.trade_log = []

    def run(self, df_1m):
        # Ensure lowercase columns
        df_1m.columns = [c.lower() for c in df_1m.columns]
        
        days = df_1m.groupby(df_1m.index.date)
        for date, day_df in days:
            self._simulate_day(day_df, date)
            
        return self._report()

    def _simulate_day(self, df, date):
        # 1. Entry at 09:20
        entry_time = time(9, 20)
        try:
            entry_bar = df.at[df.index[df.index.time == entry_time][0], 'close']
        except:
            return

        # Estimate ATM Premium (roughly 0.8% of spot for the straddle)
        # Nifty at 24000 -> Straddle ≈ 190-200 points
        atm_premium = entry_bar * 0.008 
        call_price = atm_premium / 2
        put_price = atm_premium / 2
        
        call_sl = call_price * 1.25
        put_sl = put_price * 1.25
        
        call_active = True
        put_active = True
        
        call_exit_price = 0
        put_exit_price = 0
        
        # 2. Monitor Intraday
        future_day = df[df.index.time > entry_time]
        future_day = future_day[future_day.index.time <= time(15, 10)]
        
        entry_spot = entry_bar
        
        for ts, row in future_day.iterrows():
            spot = row['close']
            move = spot - entry_spot
            
            # Simple Intraday Pricing Proxy (Delta 0.5)
            # Call price increases as spot goes up
            current_call = max(1.0, call_price + (move * 0.5))
            # Put price increases as spot goes down
            current_put = max(1.0, put_price - (move * 0.5))
            
            # Time Decay (Intraday): roughly 20% decay by EOD
            elapsed = (ts.hour * 60 + ts.minute) - (9 * 60 + 20)
            total_min = (15 * 60 + 10) - (9 * 60 + 20)
            decay = 0.20 * (elapsed / total_min)
            
            current_call *= (1 - decay)
            current_put *= (1 - decay)
            
            # SL Checks
            if call_active and current_call >= call_sl:
                call_active = False
                call_exit_price = call_sl
                
            if put_active and current_put >= put_sl:
                put_active = False
                put_exit_price = put_sl
                
            if not call_active and not put_active:
                break
                
            # Time Exit
            if ts.time() >= time(15, 10):
                if call_active: call_exit_price = current_call
                if put_active: put_exit_price = current_put
                break

        # Sizing (2 lots for 2L capital)
        lots = 2
        qty = lots * self.lot_size
        
        # PnL = (Entry - Exit) * Qty
        pnl = ((call_price - call_exit_price) + (put_price - put_exit_price)) * qty
        # Charges (Brokerage + STT ≈ 100 per day)
        pnl -= 100
        
        self.capital += pnl
        self.trade_log.append({"date": date, "pnl": pnl, "capital": self.capital})

    def _report(self):
        df = pd.DataFrame(self.trade_log)
        total_pnl = self.capital - self.initial_capital
        win_rate = (df['pnl'] > 0).mean() * 100
        return {
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "final_capital": self.capital
        }

if __name__ == "__main__":
    import os
    hist_path = "data/historical_nifty_1m.csv"
    if os.path.exists(hist_path):
        df = pd.read_csv(hist_path, index_col=0, parse_dates=True)
        bt = IntradayStraddleBacktest()
        res = bt.run(df)
        print(f"\n--- 9:20 STRADDLE BACKTEST ---")
        print(f"Final Capital: ₹{res['final_capital']:,.0f}")
        print(f"Total P&L:     ₹{res['total_pnl']:,.0f}")
        print(f"Win Rate:      {res['win_rate']:.1f}%")
