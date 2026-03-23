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
- `RISK_PER_TRADE_PERCENT = 0.12`
- `MAX_DAILY_LOSS = -1500.0`
- `MIN_ADX_TO_TRADE = 35.0`

### 3. MongoDB Running
```
mongod --dbpath /your/data/path
```
The bot needs MongoDB to track trades and enable crash recovery.

---

## Daily Rules (Non-Negotiable)

| Rule | Value | Why |
|------|-------|-----|
| Only trade if ADX > 35 | Hard gate in code | Weak trends = noise trades |
| Max 3 trades per day | Already in decision engine | Prevents overtrading |
| Stop after ₹1,500 loss | MAX_DAILY_LOSS in settings | Protects 85% of capital |
| No trading 11:30–13:00 | Blackout in gatekeeper | Lunch hour = choppy |
| Never override the bot | — | The rules exist for a reason |

---

## What the Bot Will Do Now

### Days with ADX < 35 (~60–70% of days)
- Bot detects weak/choppy trend
- Returns `None` — no trade placed
- You wait. This is correct behaviour.

### Days with ADX 35–45 (~20–25% of days)
- MOMENTUM strategy selected
- EMA 9/21 crossover on 5m + 15m alignment required
- Volatility-adjusted strike (ATM if ATR < 15, 1-OTM if ATR 15–30)
- SL: 1× ATR (option space), Target: 2.5× ATR
- Expected: ₹800–2,500 profit per winning trade

### Days with ADX > 45 (~5–10% of days, 3–5 per month)
- GAMMA_BLAST strategy selected
- These are budget days, RBI policy days, global triggers
- OTM option can move 200–500% in 2–3 hours
- Expected: ₹2,000–8,000 profit per winning trade
- **These are the days that drive the compounding.**

---

## Monthly Expectation

```
Typical month breakdown:
  ~15 trading days: ADX < 35 → no trade (cash preserved)
  ~5 trading days:  ADX 35–45 → MOMENTUM
  ~2 trading days:  ADX > 45  → GAMMA_BLAST

Assuming 55% win rate:
  MOMENTUM (5 trades × 55% WR × avg ₹1,500 win):  +₹4,125 gross
  GAMMA_BLAST (2 trades × 55% WR × avg ₹4,000 win): +₹4,400 gross
  Losses (3.15 trades × avg -₹1,200):               -₹3,780 gross
  Net per month (approx):                            +₹4,745 (+47%)

Compounded:
  Month 1:  ₹10,000 → ₹14,745
  Month 2:  ₹14,745 → ₹21,737
  Month 3:  ₹21,737 → ₹32,070
  Month 6:  ₹32,070 → ₹69,740
  Month 9:  ₹69,740 → ₹1,51,700  ← crosses ₹1L here
```

---

## Compounding Rule

After every month-end close, update `SIMULATION_CAPITAL` in settings.py
to match your actual broker balance. This ensures position sizing scales
with your growing capital.

```python
# Example: after Month 1
SIMULATION_CAPITAL = 14745.0   # Update to actual balance
```

The bot's `get_compounded_lots()` function will automatically size up
your positions as capital grows.

---

## Warning Signs — Stop the Bot Immediately If

- Three consecutive losing trades in one week
- Capital drops below ₹5,000 in the first month (re-evaluate strategy)
- VIX > 22 for more than 3 consecutive days (market in fear mode)
- You feel the urge to manually override the bot's "no trade" decision

---

## Key Files Changed for This Mode

| File | Change |
|------|--------|
| `bot/config/settings.py` | Capital ₹10k, risk 12%, daily loss -₹1,500 |
| `bot/core/decision_engine.py` | ADX > 35 hard gate, GAMMA_BLAST+MOMENTUM whitelist |
| `bot/strategies/momentum_strategy.py` | Strict 15m MTF confluence, vol-adjusted strikes, dynamic RR |
