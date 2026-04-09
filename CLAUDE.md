# NIFTY Options Trading Bot — Claude Code Project Intelligence

## Project Overview
Automated intraday options trading bot for NIFTY 50 index on Angel One (NSE).
Language: Python 3.11+. Database: MongoDB. Real-time data: Angel One WebSocket API.

## Current Trading Mode
**LIVE — Aggressive Compounding (SMALL tier)**
- Capital: ~₹46,500 (auto-detects from Angel One balance)
- Risk per trade: 8% of capital (SMALL tier)
- Daily loss limit: 12% of capital
- GAMMA_BLAST threshold: ADX > 42 | MOMENTUM: ADX 30–42
- ADX gate: No trade if ADX < 30 (SMALL tier)
- Max lots: 5 | Max trades/day: 3

## Capital Tier System
All risk, sizing, and strategy parameters are **tier-driven** — never hardcoded.
The bot calls `Config.get_tier(capital)` at runtime to get the active tier.

| Tier   | Capital Range       | Risk/Trade | Daily Loss | ADX Gate | Gamma Blast ADX |
|--------|---------------------|------------|------------|----------|-----------------|
| MICRO  | < ₹25,000           | 12%        | 15%        | 35       | 45              |
| SMALL  | ₹25k – ₹1L         | 8%         | 12%        | 30       | 42              |
| MEDIUM | ₹1L – ₹5L          | 5%         | 8%         | 25       | 40              |
| LARGE  | > ₹5L              | 3%         | 5%         | 20       | 38              |

**Never hardcode risk/ADX values** — always read from the tier object.

## Architecture — Key Files

| File | Purpose |
|------|---------|
| `bot/config/settings.py` | Capital tier definitions — single source of truth for all risk params |
| `bot/core/safety_checks.py` | SafetyGatekeeper — 9 hard rules, never bypass |
| `bot/core/decision_engine.py` | Strategy selection logic (ADX gate + whitelist) |
| `bot/core/order_manager.py` | Smart-Limit execution, SL placement |
| `bot/core/market_feed.py` | WebSocket singleton, candle construction |
| `bot/core/trade_repo.py` | MongoDB persistence, crash recovery |
| `bot/core/backtest_engine.py` | Vectorized backtester (Pandas) |
| `bot/core/metrics_exporter.py` | JSON metrics writer → data/metrics.json |
| `bot/strategies/momentum_strategy.py` | Primary live strategy (ADX 30–42) |
| `bot/strategies/gamma_blast_strategy.py` | High-ADX parabolic strategy (ADX > 42) |
| `bot/utils/indicators.py` | Numba JIT: EMA, RSI, ADX, ATR |

## Coding Conventions
- All strategies follow the same pattern: `execute()` → `enter_position()` → monitoring loop → `close_position()`
- SafetyGatekeeper must be checked at every entry point — never skip it
- All order placement goes through `OrderManager.place_smart_limit()` — never call the API directly
- All trade state is persisted to MongoDB via `trade_repo` — no in-memory-only state
- Indicator calculations: prefer `bot/utils/indicators.py` (Numba) over inline pandas for hot paths
- Position sizing: always via `gatekeeper.get_compounded_lots()` — never hardcode qty
- Log every decision with `logger.info/warning/critical` — the logs are the audit trail
- Risk/ADX thresholds: always read from `Config.get_tier(capital)` — never use raw numbers

## Safety Rules — Never Violate
1. `SafetyGatekeeper.is_market_open()` — always check before entry
2. `SafetyGatekeeper.check_max_daily_loss()` — checked every 0.5s in monitor loop
3. `MIN_ADX_TO_TRADE` — tier-driven, no trade below this (30 for SMALL)
4. `MAX_DAILY_LOSS` — tier-driven daily halt (12% for SMALL)
5. `MIN_CAPITAL_THRESHOLD` — bot halts permanently if capital < ₹8k (SMALL)
6. Blackout period 11:30–13:00 — no new entries
7. Time exit at 15:15 — all positions closed
8. Post-SL cooldown: 5 minutes before re-entry after any SL hit (gamma blast)

## Monitor Loop Heartbeat (both strategies)
Every 30 seconds while a trade is open, both strategies log a status line:

**Gamma Blast:**
```
Gamma Blast: 📊 MONITOR | LTP=₹162.5 | Entry=₹155.4 | SL=₹124.3 | Qty=65 | Stage=0 | P&L=₹+461 (+0.5%) | Next: BE trigger @ ₹186.5 (24.0pts away)
```
Stages: 0=initial SL | 1=breakeven | 2=50% booked | 3=tight trail

**Momentum:**
```
Momentum: 📊 MONITOR | LTP=₹105.0 | Entry=₹100.0 | SL=₹88.0 (17.0pts below) | Target=₹125.0 (+20.0pts) | Qty=65 | P&L=₹+325 (+0.5%) | RR=TRENDING_5:1
```

## Known Fixes Applied
| Date | Bug | Fix |
|------|-----|-----|
| 2026-04-09 | PnL always recorded as ₹0.0 | `trade_repo.close_trade` now auto-calculates PnL via `$inc` from stored entry_price when caller doesn't pass pnl explicitly |
| 2026-04-09 | SL hit: double-exit race (broker SL + market order both firing) | New order: cancel broker SL → place market exit → wait for fill → close DB with actual fill price |
| 2026-04-09 | Gamma blast re-entered within 60s of SL hit (revenge trade on bounce) | 5-min post-SL cooldown gate in `execute()` via `_last_sl_hit_time` |

## Available Slash Commands
Run these with `/command-name` in Claude Code:

| Command | What It Does |
|---------|-------------|
| `/backtest` | Run the backtest engine against historical data |
| `/review` | Review today's trades and P&L from MongoDB |
| `/compound` | Monthly compounding workflow — update capital |
| `/health` | Check system health (MongoDB, WebSocket, data freshness) |
| `/add-strategy` | Scaffold a new strategy following the standard pattern |
| `/debug` | Debug a failing strategy or execution issue |
| `/metrics` | Read and display current data/metrics.json |
| `/risk-check` | Validate all risk settings are correctly configured |

## Monthly Compounding Protocol
After each month-end, run `/compound` to update capital.
Target trajectory: ₹10k → ₹1L in ~8–9 months at ~32–47% monthly gain.
Current milestone: ₹10k → ₹46.5k (SMALL tier unlocked ✅)

## Dependencies Added (requirements.txt)
- `numba` — JIT compilation for indicator hot paths
- `prometheus-client` — optional Prometheus endpoint
- `numpy` — explicit (was transitive dependency)
