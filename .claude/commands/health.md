# /health — System Health Check

Verify all systems are ready for live trading.

## Checks to Run

### 1. Environment Variables
Verify `.env` file exists and has all required keys:
```
API_KEY          — must be set
CLIENT_ID        — must be set
PASSWORD         — must be set
TOTP_SECRET      — must be set
LIVE_TRADE_ENABLED — must be TRUE for live trading
MONGO_URI        — defaults to mongodb://localhost:27017/
```

### 2. MongoDB Connectivity
```python
from bot.core.trade_repo import trade_repo
# If trade_repo.client is not None → MongoDB connected
# Run: trade_repo.get_today_trades() and confirm no exception
```

### 3. Settings Validation
Read `bot/config/settings.py` and confirm:
- `SIMULATION_CAPITAL` matches intended live capital
- `RISK_PER_TRADE_PERCENT = 0.12`
- `MAX_DAILY_LOSS = -1500.0`
- `MIN_CAPITAL_THRESHOLD = 3000.0`
- `MIN_ADX_TO_TRADE = 35.0`
- `NIFTY_LOT_SIZE = 65` (updated for 2026)

### 4. Data Directory
Check `data/` directory exists and contains:
- `data/metrics.json` — written by metrics_exporter (present if bot ran recently)
- `data/market_analysis.json` — written by backend service
- `data/session.json` — Angel One session token

### 5. Strategy Files Integrity
Confirm these files exist and have no syntax errors:
- `bot/strategies/momentum_strategy.py`
- `bot/strategies/gamma_blast_strategy.py`
- `bot/core/decision_engine.py`
- `bot/core/backtest_engine.py`
- `bot/core/metrics_exporter.py`
- `bot/utils/indicators.py`

### 6. Numba Status
```python
from bot.utils.indicators import _NUMBA
print("Numba JIT:", "ENABLED" if _NUMBA else "DISABLED (fallback to NumPy)")
```

### 7. Kill Switch State
```python
from bot.core.kill_switch import is_kill_switch_active
# Should be False before starting
```

## Output Format
Print a clear pass/fail table:
```
System Health Check — <datetime>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Environment variables   — all set
✅ MongoDB connection       — connected
✅ Settings validation      — correct
✅ Data directory           — present
✅ Strategy files           — intact
⚠️ Numba JIT               — not installed (slower indicator calc)
✅ Kill switch              — inactive
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: READY TO TRADE / NOT READY
```

If any check fails, explain what to fix and how.
