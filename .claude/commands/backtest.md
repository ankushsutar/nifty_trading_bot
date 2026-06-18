# /backtest — Run Vectorized Backtest

Run the BacktestEngine against historical NIFTY 1-minute candle data and report results.

## Steps

1. Ask the user for the path to their historical data CSV if not already provided.
   Expected format: `timestamp, open, high, low, close, volume`

2. Read `bot/core/backtest_engine.py` to confirm the current implementation.

3. Write and run a backtest script:

```python
import pandas as pd
from bot.core.backtest_engine import BacktestEngine

# Load data
df = pd.read_csv("PATH_TO_DATA.csv", parse_dates=["timestamp"])

# Run all strategies
engine = BacktestEngine(initial_capital=10000)
engine.load_data(df)
results = engine.run_all_strategies()

# Print ranked results
ranked = sorted(results.items(), key=lambda x: x[1].get("sharpe_ratio", 0), reverse=True)
for name, m in ranked:
    print(f"\n{'='*50}")
    print(f"Strategy : {name}")
    print(f"CAGR     : {m.get('cagr_pct', 0):.1f}%")
    print(f"Max DD   : {m.get('max_drawdown_pct', 0):.1f}%")
    print(f"Sharpe   : {m.get('sharpe_ratio', 0):.2f}")
    print(f"Win Rate : {m.get('win_rate_pct', 0):.1f}%")
    print(f"Trades   : {m.get('total_trades', 0)}")
    print(f"Net P&L  : ₹{m.get('total_pnl', 0):,.0f}")
    print(f"Exits    : {m.get('exit_breakdown', {})}")
```

4. Highlight the top-performing strategy by Sharpe ratio.

5. Flag any strategy with:
   - Max Drawdown > 40% → "High risk — not suitable for ₹10k capital"
   - Win Rate < 40% → "Below acceptable threshold"
   - Sharpe < 1.0 → "Insufficient risk-adjusted return"

6. Based on results, confirm or suggest adjustments to `MIN_ADX_TO_TRADE` in `settings.py`.
