# Nifty Trading Bot - Strategy Guide

This document provides a detailed explanation of the trading strategies implemented in the Nifty Options Trading Bot.

## 1. Nifty 9:20 Straddle (Short)
**File:** `strategies/nifty_straddle.py`  
**Type:** Non-Directional / Delta Neutral  
**Ideal Market:** Range-bound / Sideways  

### Logic
The classic "9:20 Straddle" takes advantage of high morning premiums and Theta (time) decay.
*   **Time:** Execution starts at **09:20 AM**.
*   **Strike Selection:** Fetches Nifty 50 Spot price and rounds to the nearest 50 (ATM).
*   **Action:** **SELL** 1 Lot of ATM Call (CE) and **SELL** 1 Lot of ATM Put (PE).
*   **Sizing:** Dynamically adjusted based on VIX (Volatility Index).

### Risk Management
*   **Stop Loss (SL):** A **25% Stop Loss** is placed on *both* option legs immediately after entry.
*   **SL Modification (Adjustment):**
    *   If one leg hits its Stop Loss (e.g., Market shoots up, Call SL hit), the bot automatically **moves the Stop Loss of the remaining leg (Put) to Cost (Entry Price)**.
    *   This creates a "Free Trade" scenario effectively protecting capital.

### Exit
*   **Time Exit:** Hard exit at **15:15 (03:15 PM)** to avoid carry-forward risk.
*   **All Stops Hit:** If market swings wildly and hits both SLs.

---

## 2. Momentum Strategy (EMA + RSI)
**File:** `strategies/momentum_strategy.py`  
**Type:** Directional / Trend Following  
**Ideal Market:** Strong Trending Days  

### Logic
This strategy captures intraday trends using a combination of Moving Averages (Lagging) and RSI (Leading).
*   **Timeframe:** 5-Minute Candles.
*   **Indicators:**
    *   **EMA 9** (Fast Moving Average)
    *   **EMA 21** (Slow Moving Average)
    *   **RSI (14)** (Relative Strength Index)
*   **Entry Signals:**
    *   **Buy CE (Bullish):** When 9 EMA crosses **above** 21 EMA **AND** RSI < 70 (Not overbought).
    *   **Buy PE (Bearish):** When 9 EMA crosses **below** 21 EMA **AND** RSI > 30 (Not oversold).

### Risk Management
*   **Trailing Stop Loss (Step Ladder):** Uses a "Step" system to lock in profits as the price moves in our favor.
    *   At **+20 points** profit → SL moves to **Entry + 5** (Breakeven+).
    *   At **+40 points** profit → SL moves to **Entry + 25**.
    *   At **+60 points** profit → SL moves to **Entry + 45**.
*   **Reversals:** Strategies exits immediately if the EMAs cross back in the opposite direction.
*   **Safety:** Includes "Blind Mode" to handle API data failures gracefully.

---

## 3. ORB (Open Range Breakout)
**File:** `strategies/orb_strategy.py`  
**Type:** Directional / Breakout  
**Ideal Market:** Volatile Morning Moves  

### Logic
Trades the breakout of the market's opening volatility range.
*   **Range Establishment:** Monitors the High and Low prices between **09:15 AM** and **09:30 AM**.
*   **Breakout Signal:**
    *   **Buy CE:** If Nifty price breaks **above** the Opening Range High.
    *   **Buy PE:** If Nifty price breaks **below** the Opening Range Low.

### Risk Management
*   **Stop Loss:** A fixed **10% Stop Loss** on the option premium.
*   **Target:** Aims for quick momentum bursts (Scalping style).

---

## 4. VWAP Strategy (Institutional Trend)
**File:** `strategies/vwap_strategy.py`  
**Type:** Directional / Institutional Flow  
**Ideal Market:** Sustained Trends with Volume  

### Logic
Known as "The Senior Trader" strategy, it focuses on where institutions are transacting.
*   **Indicators:**
    *   **VWAP** (Volume Weighted Average Price) - The "Fair Value" line.
    *   **EMA 20** - Short-term trend baseline.
    *   **Volume** - Confirmation.
*   **Conditions:**
    *   **Bullish (Buy CE):** Price > VWAP **AND** Price > EMA 20.
    *   **Bearish (Buy PE):** Price < VWAP **AND** Price < EMA 20.
    *   **Neutral (No Trade):** If price is stuck between VWAP and EMA (aka "Chop Zone").
*   **AI Filter:**
    *   Calculates **PCR (Put Call Ratio)** for the ATM strike.
    *   Rejects Bullish trades if PCR suggests Bearish big money sentiment (and vice versa).

### Risk Management
*   **Strike Selection:** Prefers **ITM (In-The-Money)** strikes for higher Delta (better movement with underlying).
*   **Funds Check:** Requires higher capital buffer.

---

## 5. OHL (Open High Low) Scalp
**File:** `strategies/ohl_strategy.py`  
**Type:** Scalping  
**Ideal Market:** First minute of market open  

### Logic
A rapid-fire strategy based on the very first 1-minute candle of the day (09:15 - 09:16).
*   **Observation:** Checks the relationship between Open, High, and Low of the first candle.
*   **Strategy:**
    *   **Strong Buy (Buy CE):** If **Open == Low** (Price initiated and only went up).
    *   **Strong Sell (Buy PE):** If **Open == High** (Price initiated and only went down).
*   **Action:** Enters immediately at 09:16 AM if the pattern is confirmed.

### Risk Management
*   **Stop Loss:** Based on the Index Level (Low of the candle for Calls, High of the candle for Puts).
*   **Outcome:** Quick entry and exit, usually capturing the initial morning thrust.

---

## 6. Inside Bar Breakout
**File:** `strategies/inside_bar_strategy.py`  
**Type:** Price Action / Breakout  
**Ideal Market:** Consolidations before big moves  

### Logic
Identifies a period of consolidation (indecision) followed by a breakout.
*   **Timeframe:** 15-Minute Candles.
*   **Pattern:** Looks for a "Mother Candle" followed by an "Inside Candle" (Baby) completely contained within the Mother's range (High/Low).
*   **Signal:**
    *   **Buy CE:** If current price breaks the **Mother Candle's High**.
    *   **Buy PE:** If current price breaks the **Mother Candle's Low**.

### Risk Management
*   **Stop Loss:** The opposite end of the Mother Candle (e.g., if buying breakout of High, SL is at Low).
