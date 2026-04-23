# Live Trading Checklist — ₹10,000 Aggressive Compounding Plan

## Before First Live Trade

### 1. Enable Live Trading in `.env`
```
LIVE_TRADE_ENABLED=TRUE
API_KEY=your_angel_one_api_key
CLIENT_ID=your_client_id
PASSWORD=your_password
TOTP_SECRET=your_totp_secret
```

### 2. Verify Settings Are Correct
Open `bot/config/settings.py` and confirm:
- `SIMULATION_CAPITAL = 10000.0`
- `MIN_CAPITAL_THRESHOLD = 3000.0`
- `RISK_PER_TRADE_PERCENT = 0.12` (MICRO tier)
- `MAX_DAILY_LOSS_PCT = 0.15` (15% stop on total capital)
- `MIN_ADX_TO_TRADE = 25.0` (Lowered for early entry)

### 3. MongoDB Connectivity
The bot needs MongoDB to track trades and enable crash recovery.
- Ensure the `MONGO_URI` in `.env` is accessible.
- If using Atlas, check that your IP is whitelisted.
- **Note**: Check for "Name or service not known" errors in `trading_bot.log`.

---

## Daily Rules (Non-Negotiable)

| Rule | Value | Why |
|------|-------|-----|
| Only trade if ADX > 25 | Hard gate in code | Relaxed for better momentum capture |
| Max 2-3 trades per day | Tier-driven | Prevents overtrading |
| Stop after 15% daily loss | Tier-driven | Protects capital from blowouts |
| Confluence 4/6 minimum | MOMENTUM logic | Allows "A" setups, not just "A+" |
| No trading 11:30–13:00 | Blackout in gatekeeper | Lunch hour = choppy |

---

## Strategy Expectations

### MOMENTUM (ADX 25–45)
- Captures established trends with 5m/15m alignment.
- Requires 4/6 confluence (ADX, Trend, MTF, Squeeze, Sentiment, RSI).
- Trailing SL: Moves to Breakeven at 1.5x ATR.
- Target: 2.5x ATR.

### GAMMA_BLAST (ADX > 45)
- High-intensity trending days (Policy days, big news).
- Sizing increases to capture parabolic moves.
- Target: Open-ended with aggressive trailing.

---

## Warning Signs — Stop the Bot Immediately If

- Three consecutive losing trades in one week.
- Capital drops below ₹5,000 in the first month.
- MongoDB connection errors persist (prevents risk management).
- The bot enters a "Confidence Lockout" (Win rate < 60% over 10 trades).

---

## Key Files Optimized

| File | Change |
|------|--------|
| `bot/config/settings.py` | ADX 25 gate, 15% SL cap, 0.005 BBW squeeze. |
| `bot/core/decision_engine.py` | 60% confidence gate, 10-trade sample stabilization. |
| `bot/strategies/momentum_strategy.py` | 4/6 confluence requirement, 1.5x ATR breakeven trigger. |
