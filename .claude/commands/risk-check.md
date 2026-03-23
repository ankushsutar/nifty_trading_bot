# /risk-check — Validate All Risk Settings

Verify every risk parameter is correctly configured for the ₹10,000 aggressive compounding plan.
Run this before every live trading session.

## Steps

1. Read `bot/config/settings.py` and validate each setting:

| Setting | Expected | Critical? |
|---------|----------|-----------|
| `SIMULATION_CAPITAL` | ≥ 10000 (update monthly) | Yes |
| `MIN_CAPITAL_THRESHOLD` | 3000.0 | Yes |
| `RISK_PER_TRADE_PERCENT` | 0.12 | Yes |
| `MAX_CAPITAL_USAGE_PERCENT` | 0.90 | Yes |
| `MAX_DAILY_LOSS` | -1500.0 | Yes |
| `MIN_ADX_TO_TRADE` | 35.0 | Yes |
| `NIFTY_LOT_SIZE` | 65 | Yes |
| `LIVE_TRADE_ENABLED` | TRUE (from .env) | Yes |

2. For each setting, print PASS / FAIL / WARNING:

```
Risk Configuration Check
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ SIMULATION_CAPITAL    = ₹10,000.00  (matches live account)
✅ MIN_CAPITAL_THRESHOLD = ₹3,000.00   (30% floor)
✅ RISK_PER_TRADE        = 12.0%       (₹1,200 max loss/trade)
✅ MAX_CAPITAL_USAGE     = 90.0%       (₹9,000 max deployed)
✅ MAX_DAILY_LOSS        = -₹1,500.00  (15% daily stop)
✅ MIN_ADX_TO_TRADE      = 35.0        (trend gate active)
✅ NIFTY_LOT_SIZE        = 65          (2026 lot size)
✅ LIVE_TRADE_ENABLED    = TRUE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: ALL CHECKS PASSED — Safe to trade
```

3. Calculate and display the risk exposure:

```
Risk Summary for Today:
  Capital          : ₹10,000
  Max loss/trade   : ₹1,200  (12%)
  Daily stop       : ₹1,500  (15%)
  Trades before stop: ~1-2 losing trades
  Capital floor    : ₹3,000  (bot halts permanently below this)

Survival math:
  6 consecutive max losses: ₹10,000 → ₹2,800 (hits floor)
  But daily stop at ₹1,500 means max 1-2 bad trades per day
  → Real consecutive loss protection: ~5 bad trading days
```

4. Check `decision_engine.py` has the ADX gate and whitelist intact:
   - Read lines around `MIN_ADX_TO_TRADE` and `GAMMA_BLAST` / `MOMENTUM` selection
   - Confirm no other strategies can be selected

5. Confirm `SafetyGatekeeper.check_max_daily_loss()` uses `MAX_DAILY_LOSS` from settings:
   - Read `bot/core/safety_checks.py` and verify the reference

6. Flag CRITICAL if any of these are wrong:
   - `LIVE_TRADE_ENABLED = FALSE` while user thinks they're trading live
   - `RISK_PER_TRADE_PERCENT > 0.20` — dangerously high
   - `MAX_DAILY_LOSS > -500` — too tight, will halt on first normal trade
   - `NIFTY_LOT_SIZE != 65` — wrong lot size = wrong position sizing
