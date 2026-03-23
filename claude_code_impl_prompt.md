# 🚀 Claude Code: "100x Trader" Implementation Prompt

*Copy and paste the section below into Claude Code to begin the transformation.*

---

### **System Context & Objective**
I have an existing **Nifty Options Trading Bot** built on the Angel One API. It has a solid foundation: modular strategies, a [SafetyGatekeeper](file:///d:/agents/nifty_trading_bot/bot/core/safety_checks.py#7-316) for risk management, [MarketFeedService](file:///d:/agents/nifty_trading_bot/bot/core/market_feed.py#14-419) for real-time WebSocket data, and an [OrderManager](file:///d:/agents/nifty_trading_bot/bot/core/order_manager.py#8-363) that uses "Smart-Limit" (price walking) to minimize slippage.

**Your Objective**: Transform this bot into a "100x Trader" by moving from simple heuristic-based rules to a data-driven, institutional-grade automated system.

### **Phase 1: Build the Performance Foundation (Vectorized Backtesting)**
1.  **Create `bot/core/backtest_engine.py`**:
    - Implement a vectorized backtesting engine using `Polars` or `Pandas`.
    - It must ingest 1-minute historical data (candles) and simulate the exact logic of the `strategies/` pool.
    - **Critical**: It must use the [SafetyGatekeeper](file:///d:/agents/nifty_trading_bot/bot/core/safety_checks.py#7-316)'s margin and loss logic to ensure backtest results are realistic. 
    - Output: CAGR, Max Drawdown, Sharpe Ratio, and Win Rate per strategy.

### **Phase 2: Algorithmic Intelligence (Decision Engine Upgrade)**
1.  **Refactor [bot/core/decision_engine.py](file:///d:/agents/nifty_trading_bot/bot/core/decision_engine.py)**:
    - Replace static ADX thresholds with a **Strategy Confidence Score**.
    - Implement a simple **Market Regime Classifier** that uses the last 4 hours of data to check for Trending vs. Mean-Reverting environments.
    - Integration: Strategies should only be selected if their current "Confidence Score" (based on the last 5 days of backtest/live data) is > 70%.

### **Phase 3: Strategy Optimization (Momentum & Volatility)**
1.  **Enhance `MomentumStrategy`**:
    - Implement **Multi-Timeframe Confluence**: A "BUY" signal on the 5m chart MUST align with the 15m trend (EMA 9/21 alignment).
2.  **Optimize Strike Selection**:
    - currently, strikes are +/- 1 OTM. Improve this in `bot/strategies/` by implementing **Volatility-Adjusted Strikes**. Use the current ATR to determine if we should go deeper OTM (High ATR) or closer to ATM (Low ATR).
3.  **Dynamic RR (Risk-Reward)**:
    - Modify the monitoring loops to adjust SL/Target based on the regime:
        - **Trending**: Move SL to 1:1 breakeven fast, but let Target run to 5:1.
        - **Rangebound**: Use 1.5:1 Target and exit at the first sign of reversal.

### **Phase 4: Execution & Performance (The "Alpha" Edge)**
1.  **Latency Optimization**:
    - Identify indicator calculations in the strategy loops (EMA, ADX, RSI) and optimize them using `Numba` (`@njit`) to reduce calculation time to microseconds.
2.  **Slippage Analytics**:
    - Update `trade_repo.py` to record the delta between `Expected Price` (at signal time) and `Actual Fill Price`. 
    - Use this data to auto-adjust the "Smart-Limit" walk speed.

### **Phase 5: Observability**
1.  **Real-Time Metrics**:
    - Implement a simple Prometheus exporter or a JSON-based stats writer that records: `Available Capital`, `Current Unrealized P&L`, `Alpha per Strategy`, and `Slippage`.

**Instructions for Implementation**:
- Read the existing `architecture_overview.md` and `senior_architect_review.md` to understand the current state.
- Implement these changes incrementally, starting with the Backtest Engine.
- Maintain the current "Safety First" philosophy of the `Gatekeeper`.

---
