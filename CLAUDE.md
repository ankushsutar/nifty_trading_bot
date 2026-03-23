# NIFTY Options Trading Bot — Claude Code Project Intelligence

## Project Overview
Automated intraday options trading bot for NIFTY 50 index on Angel One (NSE).
Language: Python 3.11+. Database: MongoDB. Real-time data: Angel One WebSocket API.

## Current Trading Mode
**LIVE — Aggressive Compounding**
- Capital: ₹10,000
- Risk per trade: 12% (₹1,200 max loss)
- Daily loss limit: ₹1,500
- Strategies: GAMMA_BLAST (ADX > 45) + MOMENTUM (ADX 35–45) ONLY
- ADX gate: No trade if ADX < 35

## Architecture — Key Files

| File | Purpose |
|------|---------|
| `bot/config/settings.py` | All capital, risk, and strategy parameters |
| `bot/core/safety_checks.py` | SafetyGatekeeper — 9 hard rules, never bypass |
| `bot/core/decision_engine.py` | Strategy selection logic (ADX gate + whitelist) |
| `bot/core/order_manager.py` | Smart-Limit execution, SL placement |
| `bot/core/market_feed.py` | WebSocket singleton, candle construction |
| `bot/core/trade_repo.py` | MongoDB persistence, crash recovery |
| `bot/core/backtest_engine.py` | Vectorized backtester (Pandas) |
| `bot/core/metrics_exporter.py` | JSON metrics writer → data/metrics.json |
| `bot/strategies/momentum_strategy.py` | Primary live strategy |
| `bot/strategies/gamma_blast_strategy.py` | High-ADX parabolic strategy |
| `bot/utils/indicators.py` | Numba JIT: EMA, RSI, ADX, ATR |

## Coding Conventions
- All strategies follow the same pattern: `execute()` → `enter_position()` → monitoring loop → `close_position()`
- SafetyGatekeeper must be checked at every entry point — never skip it
- All order placement goes through `OrderManager.place_smart_limit()` — never call the API directly
- All trade state is persisted to MongoDB via `trade_repo` — no in-memory-only state
- Indicator calculations: prefer `bot/utils/indicators.py` (Numba) over inline pandas for hot paths
- Position sizing: always via `gatekeeper.get_compounded_lots()` — never hardcode qty
- Log every decision with `logger.info/warning/critical` — the logs are the audit trail

## Safety Rules — Never Violate
1. `SafetyGatekeeper.is_market_open()` — always check before entry
2. `SafetyGatekeeper.check_max_daily_loss()` — checked every 0.5s in monitor loop
3. `MIN_ADX_TO_TRADE = 35` — no trade below this
4. `MAX_DAILY_LOSS = -1500` — bot halts for the day
5. `MIN_CAPITAL_THRESHOLD = 3000` — bot halts permanently if capital < ₹3k
6. Blackout period 11:30–13:00 — no new entries
7. Time exit at 15:15 — all positions closed

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

## Dependencies Added (requirements.txt)
- `numba` — JIT compilation for indicator hot paths
- `prometheus-client` — optional Prometheus endpoint
- `numpy` — explicit (was transitive dependency)
