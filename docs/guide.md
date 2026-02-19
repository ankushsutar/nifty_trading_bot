# 📘 Nifty Trading Bot - Comprehensive Guide

This document details the inner workings of the Nifty Trading Bot, including its Decision Engine logic, strategy selection criteria, and concrete examples for each trading setup.

---

## 🧠 Decision Engine: The "Brain"

The `DecisionEngine` runs every minute to analyze the market and determine **IF** we should trade and **WHICH** strategy to deploy.

### 1. Selection Logic Flow

The engine uses a hierarchy of checks to filter out bad trades:

1.  **Daily Guardrails**:
    - **Max Trades**: Hard cap of **2 trades per day**. If 2 trades are already taken (regardless of PnL), the bot stops.
    - **Consecutive Losses**: If the last 2 trades were losses, the bot halts for the day to prevent "revenge trading".

2.  **Time-Based Selection**:
    - **09:15 - 09:20**: Only **OHL Scalp** is allowed (for opening volatility).
    - **09:20 - 09:30**: No new entries (Wait for data stabilization).
    - **09:30 - 10:00**: **ORB (Opening Range Breakout)** is prioritized if range breaks.
    - **10:00 - 15:15**: **Momentum**, **VWAP**, or **Inside Bar** depending on trend.

3.  **Market Regime Filtering (The "Traffic Light")**:
    - **ADX > 25 (Trending)**: Enables **Momentum** or **VWAP** strategies.
    - **ADX < 20 (Sideways)**: Enables **Inside Bar** or **Straddle** strategies.
    - **VIX > 20 (Volatile)**: Reduces position size by 50% or halts trading if extreme.

### 2. Scenario Example

**Scenario**: It's 10:30 AM. Nifty is at 22,100.

- **Check 1**: Daily Trades = 0. (Pass)
- **Check 2**: Time > 10:00. (Pass - Open strategies allowed)
- **Check 3**: ADX is 32 (Strong Trend). RSI is 65 (Bullish).
- **Decision**: System selects **Momentum Strategy** (because ADX > 25).
- **Action**: Scans for EMA Crossover to go LONG.

---

## 🚀 Strategy Details & Examples

### 1. OHL Scalp (Open-High-Low)

**Time**: 09:15 AM - 09:16 AM
**Logic**: Captures the immediate directional move at market open. If the market opens and cannot break the first minute's Low (Open ≈ Low), it suggests strong buying.

- **Buy CE Condition**: `Open - Low < 1 point` (Open ≈ Low)
- **Buy PE Condition**: `High - Open < 1 point` (Open ≈ High)

#### 📝 Example

- **9:15 Candle Data**: Open: 22000, High: 22050, Low: 21999.5, Close: 22040.
- **Analysis**: Open (22000) is almost equal to Low (21999.5). Difference is 0.5 pts.
- **Signal**: **STRONG BUY (CE)**.
- **Trade**: Bot buys ATM CE immediately at 09:16.
- **Exit**: Target (1:2 Risk-Reward) or 09:20 Time limit.

---

### 2. ORB (Opening Range Breakout)

**Time**: 09:30 AM - 10:00 AM
**Logic**: The first 15 minutes (09:15-09:30) defines the day's initial range. A breakout suggests a trend continuation.

- **Range Definition**: High and Low of the 09:15-09:30 period.
- **Buy CE**: Price closes **above** Breakdown High.
- **Buy PE**: Price closes **below** Breakdown Low.

#### 📝 Example

- **09:15-09:30 Range**: High: 22100, Low: 22050.
- **09:35 Candle**: Closes at 22110 (Above High).
- **Signal**: **BREAKOUT BUY (CE)**.
- **Stop Loss**: 22050 (Range Low) or max 15%.
- **Target**: 22200 (1:2 of range width).

---

### 3. Momentum (EMA + RSI)

**Time**: 10:00 AM - 03:00 PM
**Logic**: Classic trend following. Uses EMA crossovers to enter high-probability moves.

- **Bullish Entry**:
  1.  Price > **9 EMA** > **21 EMA**.
  2.  **RSI > 50** (Momentum confirmed).
  3.  **ADX > 25** (Trend strength).
- **Bearish Entry**:
  1.  Price < **9 EMA** < **21 EMA**.
  2.  **RSI < 50**.

#### 📝 Example

- **Market**: Nifty at 22150.
- **Indicators**: 9 EMA = 22140, 21 EMA = 22120. RSI = 60.
- **Signal**: **BUY CE**. (Price is above EMAs, EMAs are aligned, RSI supports).
- **Exit**: Price closes below 9 EMA (Trend change) or Target hit.

---

### 4. VWAP (Institutional Trend) - "Pro" Strategy

**Time**: After 10:00 AM
**Logic**: Institutions use Volume-Weighted Average Price (VWAP) as a benchmark. We trade with them.

- **Long**: Price crosses **above** VWAP with volume spike.
- **Short**: Price crosses **below** VWAP with volume spike.
- **Why**: Logic is that "smart money" is stepping in to push price away from the average value.

#### 📝 Example

- **Data**: Nifty Price: 22100. VWAP: 22105.
- **Event**: 10:15 Candle spikes up to 22115 on high volume.
- **Signal**: **VWAP CROSSOVER BUY (CE)**.
- **Stop Loss**: Recent swing low below VWAP.

---

### 5. Inside Bar (Volatility Contraction)

**Time**: During low volatility / consolidation.
**Logic**: Markets move from low volatility to high volatility. An "Inside Bar" represents a pause (indecision) before an explosive move.

- **Pattern**:
  1.  **Mother Candle**: Large range.
  2.  **Baby Candle**: Completely inside Mother's High-Low range.
- **Entry**: Breakout of Mother Candle's High (Buy) or Low (Sell).

#### 📝 Example

- **11:00 Candle (Mother)**: High 22150, Low 22100.
- **11:15 Candle (Baby)**: High 22140, Low 22110 (Inside).
- **11:30 Candle**: Breaks above 22150.
- **Signal**: **BUY CE**.
- **Stop Loss**: 22100 (Mother Low).

---

### 6. Straddle (Theta Decay) - _Capital Intensive_

**Time**: Sideways Market (ADX < 20)
**Logic**: If the market isn't moving, option buyers lose money (Theta decay). We become sellers.

- **Setup**: Sell ATM Call + Sell ATM Put.
- **Profit Source**: Time decay. As long as Nifty stays between the strikes, both options lose value -> We profit.
- **Risk**: High (Unlimited). Requires strict SL.

#### 📝 Example

- **Market**: Nifty at 22100 (Sideways).
- **Trade**:
  - Sell 22100 CE @ ₹150.
  - Sell 22100 PE @ ₹140.
- **Combined Premium**: ₹290.
- **Outcome**: After 2 hours, Nifty is still 22100.
  - CE drops to ₹130.
  - PE drops to ₹120.
  - Profit: (150-130) + (140-120) = ₹40 per qty.

---

## 🛡️ Risk Management Parameters

| Parameter           | Value           | Description                                                    |
| :------------------ | :-------------- | :------------------------------------------------------------- |
| **Max Trades/Day**  | 2               | Hard stop to prevent overtrading.                              |
| **Max Loss/Day**    | 1%              | System kills all positions if daily loss hits 3% of capital.   |
| **Position Sizing** | Dynamic         | Based on Capital. Uses compounding (profits reinvested).       |
| **SL Type**         | System + Broker | System monitors monitoring SL. Broker SL placed as safety net. |
| **Trailing SL**     | Yes             | Moves to Breakeven once 1:1 Risk-Reward is hit.                |
