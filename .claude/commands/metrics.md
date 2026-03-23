# /metrics — Display Real-Time Trading Metrics

Read `data/metrics.json` (written every 10s by MetricsExporter) and display a clean dashboard.

## Steps

1. Read `data/metrics.json`:
```python
import json, os, datetime

path = "data/metrics.json"
if not os.path.exists(path):
    print("metrics.json not found — is the bot running?")
    print("Start it with: python -m bot.main --auto")
else:
    with open(path) as f:
        m = json.load(f)

    age = (datetime.datetime.now() - datetime.datetime.fromisoformat(m['timestamp'])).seconds
    print(f"\n📊 BOT METRICS  (snapshot age: {age}s ago)")
    print(f"{'━'*55}")
    print(f"  Capital Available  : ₹{m['available_capital']:>10,.0f}")
    print(f"  Unrealized P&L     : ₹{m['unrealized_pnl']:>+10,.0f}")
    print(f"  Realized P&L Today : ₹{m['realized_pnl_today']:>+10,.0f}")
    print(f"  Total Equity       : ₹{m['total_equity']:>10,.0f}")
    print(f"{'━'*55}")

    # Session stats
    ss = m.get('session_stats', {})
    print(f"\n  Today's Session:")
    print(f"    Trades   : {ss.get('closed_trades', 0)} closed, {ss.get('open_positions', 0)} open")
    print(f"    Win Rate : {ss.get('win_rate_pct', 0):.0f}%  ({ss.get('wins', 0)}W / {ss.get('losses', 0)}L)")
    print(f"    Best     : ₹{ss.get('best_trade', 0):+,.0f}")
    print(f"    Worst    : ₹{ss.get('worst_trade', 0):+,.0f}")

    # Alpha per strategy
    alpha = m.get('alpha_per_strategy', {})
    if alpha:
        print(f"\n  Alpha by Strategy (last 30 days):")
        for strat, data in sorted(alpha.items(), key=lambda x: x[1]['total_pnl'], reverse=True):
            print(f"    {strat:15s} : ₹{data['total_pnl']:>+8,.0f} | "
                  f"WR={data['win_rate']}% | {data['trades']} trades | avg ₹{data['avg_pnl']:+,.0f}/trade")

    # Slippage
    slip = m.get('slippage', {})
    print(f"\n  Slippage (last 5 days):")
    print(f"    Avg Points  : {slip.get('avg_slippage_points', 0):.2f} pts")
    print(f"    Avg Pct     : {slip.get('avg_slippage_pct', 0):.3f}%")
    print(f"    Walk Ticks  : {slip.get('recommended_walk_ticks', 5)} (auto-recommended)")

    # Active positions
    active = m.get('active_trades', [])
    if active:
        print(f"\n  Open Positions ({len(active)}):")
        for t in active:
            print(f"    {t['strategy']:12s} | {t['symbol']:25s} | "
                  f"Entry ₹{t['entry_price']:.1f} | UnPnL ₹{t['unrealized_pnl']:+,.0f}")
```

2. If `metrics.json` is older than 60 seconds and the bot should be running, flag it:
   "WARNING: Metrics file is stale — MetricsExporter may not be running."

3. Show the last 5 equity curve datapoints to indicate direction:
```python
    curve = m.get('equity_curve', [])[-5:]
    if curve:
        print(f"\n  Equity Trend (last 5 snapshots):")
        for pt in curve:
            print(f"    {pt['ts'][11:19]}  ₹{pt['total_equity']:>10,.0f}")
```
