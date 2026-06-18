# /compound — Monthly Capital Compounding Update

Update the bot's capital settings after month-end to reflect actual broker balance.
This is the most important monthly task — it ensures position sizing scales with growth.

## Steps

1. Ask the user: "What is your current broker account balance (net available margin)?"

2. Read the current value from `bot/config/settings.py`:
```python
# Show current setting
SIMULATION_CAPITAL = <current_value>
```

3. Calculate the growth:
```
Previous capital : ₹<old_value>
New capital      : ₹<user_provided>
Monthly gain     : ₹<diff> (<pct>%)
```

4. Update `bot/config/settings.py` with the new capital:
   - `SIMULATION_CAPITAL = <new_value>`
   - Keep all other settings unchanged

5. Recalculate the updated trajectory to ₹1,00,000:
```
Current capital  : ₹<new_value>
Monthly rate     : ~32-47% (based on actual last month)
Months to ₹1L   : log(100000 / new_value) / log(1 + monthly_rate)
```

6. Show the updated compounding projection table:
```
Month +1 : ₹<new × 1.32>
Month +2 : ₹<...>
Month +3 : ₹<...>
...until ₹1,00,000
```

7. Confirm: "Settings updated. Bot will now size positions based on ₹X capital."

## Important
- Never update `MAX_DAILY_LOSS` — it scales automatically (15% of capital is implied by the loss limit)
- Never update `RISK_PER_TRADE_PERCENT` — keep at 0.12 until capital > ₹50,000
- If capital dropped below ₹8,000, flag a review before continuing live trading
