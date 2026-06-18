# /debug — Debug a Strategy or Execution Issue

Systematically diagnose and fix the most common bot issues.

## Step 1: Identify the Problem Area

Ask the user which symptom they're seeing, then follow the relevant section:

---

### A. "Bot placed no trades today"

1. Check if the ADX gate fired:
   - Read `data/market_analysis.json` → look at `analysis.adx`
   - If ADX < 35 all day → correct behaviour, no bug
   - If ADX was > 35 but no trade → continue to B

2. Check the decision engine log for `[Brain]` entries:
   ```bash
   grep "\[Brain\]" logs/bot.log | tail -50
   ```
   Look for: proximity alerts, confidence gate blocks, fund check failures.

3. Check if the daily trade limit was hit:
   ```python
   from bot.core.trade_repo import trade_repo
   trades = trade_repo.get_today_trades()
   print(f"Trades today: {len(trades)}/3")
   ```

4. Check consecutive loss breaker:
   ```python
   closed = [t for t in trades if t['status'] == 'CLOSED']
   recent_2 = closed[-2:]
   print([t['pnl'] for t in recent_2])  # Both negative = halted
   ```

---

### B. "Order placed but not filled"

1. Check Smart-Limit walk logs:
   ```bash
   grep "Smart-Limit\|walk\|TIMEOUT\|FILLED" logs/bot.log | tail -30
   ```

2. Check slippage stats — if avg > 1%, increase walk ticks:
   ```python
   from bot.core.trade_repo import trade_repo
   print(trade_repo.get_slippage_stats())
   # If recommended_walk_ticks > 5, update order_manager.py max_walk_ticks
   ```

3. Read `bot/core/order_manager.py` `place_smart_limit()` — check `max_walk_ticks=5` parameter.

---

### C. "Strategy entered but SL/Target not working"

1. Check `active_position` dict has `target_price` and `dynamic_rr` keys:
   ```bash
   grep "dynamic_rr\|target_price\|Dynamic RR" logs/bot.log | tail -20
   ```

2. Verify `check_trailing_stop()` is being called (throttled every 3s):
   ```bash
   grep "Stage\|TRENDING\|RANGEBOUND\|Break-Even" logs/bot.log | tail -30
   ```

3. If SL order was placed with broker but not triggering — check `place_sl_order()` logs.

---

### D. "MongoDB connection error"

1. Confirm MongoDB is running:
   ```bash
   mongod --version
   # On Windows: net start MongoDB
   ```

2. Check MONGO_URI in `.env` — default is `mongodb://localhost:27017/`

3. The bot degrades gracefully — it will trade but won't persist state.
   Restart MongoDB and then run `/health` to confirm reconnection.

---

### E. "WebSocket disconnected / stale data"

1. Check market_feed logs:
   ```bash
   grep "market_feed\|WebSocket\|reconnect\|stale" logs/bot.log | tail -30
   ```

2. The feed auto-reconnects every 5s. If it keeps disconnecting:
   - Check Angel One session token validity (`data/session.json`)
   - Re-authenticate: `python -m bot.core.angel_connect`

3. Check data freshness:
   ```python
   from bot.core.market_feed import market_feed
   ltp, is_stale = market_feed.get_ltp_safe("99926000")
   print(f"NIFTY LTP: {ltp}, Stale: {is_stale}")
   ```

---

### F. "Wrong position size / too many lots"

1. Read current capital:
   ```python
   from bot.core.safety_checks import SafetyGatekeeper
   # SafetyGatekeeper.get_compounded_lots() uses Config.SIMULATION_CAPITAL
   from bot.config.settings import Config
   print(f"Capital: ₹{Config.SIMULATION_CAPITAL}")
   print(f"Risk/trade: {Config.RISK_PER_TRADE_PERCENT * 100}%")
   ```

2. The lot size formula: `lots = floor(capital / (premium × lot_size))`
   With ₹10,000 and a ₹150 ATM option: `floor(10000 / (150 × 65)) = 1 lot` — correct.

3. If sizing seems wrong, update `SIMULATION_CAPITAL` in `settings.py` to match actual balance.

---

## General Debug Commands
```bash
# Tail live logs
tail -f logs/bot.log

# Search for errors
grep -i "error\|exception\|critical" logs/bot.log | tail -50

# Check last 10 trade decisions
grep "\[Brain\]" logs/bot.log | tail -10
```
