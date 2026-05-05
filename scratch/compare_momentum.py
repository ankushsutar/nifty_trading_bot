
import pandas as pd
from bot.core.backtest_engine import BacktestEngine
from bot.utils.logger import logger
from datetime import time as dtime

def compare_momentum_filters():
    hist_path = "data/historical_nifty_1m.csv"
    df = pd.read_csv(hist_path, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    
    # 1. Normal Momentum
    engine_normal = BacktestEngine(initial_capital=200000)
    engine_normal.load_data(df)
    res_normal = engine_normal.run_strategy("MOMENTUM")
    
    # 2. Filtered Momentum (Simulating the DecisionEngine's ADX > 25 gate)
    # We'll override the _momentum_signals to add the ADX gate
    class FilteredEngine(BacktestEngine):
        def _momentum_signals(self) -> pd.DataFrame:
            df5 = self.df_5m.copy()
            df5["adx"] = self._adx(df5, 14)
            
            # Original signals
            raw_signals = super()._momentum_signals()
            if raw_signals.empty: return raw_signals
            
            # Apply ADX Filter (Decision Engine Gate)
            merged = pd.merge_asof(raw_signals, df5[["adx"]], left_on="timestamp", right_index=True)
            filtered = merged[merged["adx"] > 25].drop(columns=["adx"])
            
            return filtered

    engine_filtered = FilteredEngine(initial_capital=200000)
    engine_filtered.load_data(df)
    res_filtered = engine_filtered.run_strategy("MOMENTUM")
    
    print("\n--- BUYING STRATEGY COMPARISON (MOMENTUM) ---")
    print(f"{'Metric':<20} | {'Normal (No Filter)':<20} | {'Filtered (ADX > 25)':<20}")
    print("-" * 65)
    print(f"{'Total P&L':<20} | ₹{res_normal['total_pnl']:>18,.0f} | ₹{res_filtered['total_pnl']:>18,.0f}")
    print(f"{'Win Rate':<20} | {res_normal['win_rate_pct']:>19.1f}% | {res_filtered['win_rate_pct']:>19.1f}%")
    print(f"{'Max Drawdown':<20} | {res_normal['max_drawdown_pct']:>19.1f}% | {res_filtered['max_drawdown_pct']:>19.1f}%")
    print(f"{'Total Trades':<20} | {res_normal['total_trades']:>19} | {res_filtered['total_trades']:>19}")
    print(f"{'Sharpe Ratio':<20} | {res_normal['sharpe_ratio']:>19.2f} | {res_filtered['sharpe_ratio']:>19.2f}")
    
    pnl_diff = res_filtered['total_pnl'] - res_normal['total_pnl']
    print("\n>>> VERDICT:")
    if pnl_diff > 0:
        print(f"✅ Filtered Mode made ₹{pnl_diff:,.0f} MORE profit by skipping low-probability trades!")
    else:
        print(f"⚠️ Filtered Mode made ₹{abs(pnl_diff):,.0f} less, but with {res_normal['total_trades'] - res_filtered['total_trades']} fewer trades (Better efficiency).")

if __name__ == "__main__":
    compare_momentum_filters()
