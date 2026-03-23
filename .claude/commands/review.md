# /review — Review Today's Trading Performance

Pull today's trades from MongoDB and produce a clear P&L summary.

## Steps

1. Read `bot/core/trade_repo.py` to confirm the `get_today_trades()` signature.

2. Run the following analysis:

```python
from bot.core.trade_repo import trade_repo
import datetime

trades = trade_repo.get_today_trades(mode=None)  # Both LIVE and PAPER

closed = [t for t in trades if t['status'] == 'CLOSED']
open_t  = [t for t in trades if t['status'] in ('OPEN', 'PLACED')]

print(f"\n📊 TRADING REVIEW — {datetime.date.today()}")
print(f"{'='*50}")
print(f"Total trades today : {len(trades)}")
print(f"Open positions     : {len(open_t)}")
print(f"Closed trades      : {len(closed)}")

if closed:
    pnls    = [float(t.get('pnl', 0)) for t in closed]
    wins    = [p for p in pnls if p > 0]
    losses  = [p for p in pnls if p <= 0]
    print(f"\nRealized P&L  : ₹{sum(pnls):+,.0f}")
    print(f"Win Rate      : {len(wins)/len(closed)*100:.0f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Best trade    : ₹{max(pnls):+,.0f}")
    print(f"Worst trade   : ₹{min(pnls):+,.0f}")
    print(f"\nTrade Log:")
    for t in closed:
        print(f"  {t.get('strategy'):12s} | {t.get('symbol'):25s} | "
              f"Entry ₹{t.get('entry_price',0):.1f} → Exit ₹{t.get('exit_price',0):.1f} | "
              f"P&L ₹{t.get('pnl',0):+,.0f} | {t.get('exit_reason')}")

if open_t:
    print(f"\nOpen Positions:")
    for t in open_t:
        print(f"  {t.get('strategy'):12s} | {t.get('symbol'):25s} | "
              f"Entry ₹{t.get('entry_price',0):.1f} | Status: {t.get('status')}")
```

3. Check if today's realized P&L is within the daily loss limit (`MAX_DAILY_LOSS = -1500`).

4. Check slippage for today:
```python
stats = trade_repo.get_slippage_stats(days=1)
print(f"\nToday's Slippage: avg {stats['avg_slippage_pct']:.2f}% ({stats['avg_slippage_points']:.1f} pts)")
print(f"Recommended walk ticks: {stats.get('recommended_walk_ticks', 5)}")
```

5. Summarise: Was today a good trading day? Were any rules violated?
   Flag if: loss > ₹1,000, more than 3 trades, any trade without ADX > 35.
