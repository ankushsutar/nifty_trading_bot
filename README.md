# Nifty Options Trading Bot

A Python-based algorithmic trading bot for Nifty 50 Options, designed to work with the **Angel One SmartAPI**. It implements a **Straddle Strategy** (buying both CE and PE) with automatic ATM strike selection and dynamic weekly expiry calculation.

## 🚀 Key Features

*   **Smart Strategy (Momentum + RSI):** Uses EMA Crossover (9 vs 21) combined with **RSI** filters to avoid buying at peaks (Overbought > 70) or valleys (Oversold < 30).
*   **Robust Data Fetching:** Centralized `DataFetcher` with automatic retries and rate-limit handling for reliable market data.
*   **Professional Logging:** Replaces console spam with structured logging to both Console and File (`logs/trading_bot.log`).
*   **Dynamic Straddle Strategy:** Automatically calculates the At-The-Money (ATM) strike based on the live Nifty 50 spot price and places dual-leg (CE + PE) orders.
*   **Auto-Expiry Calculation:** Automatically determines the nearest upcoming Thursday expiry date.
*   **Mock Mode (`--test`):** A robust local testing mode that simulates market data and order placement.
*   **Modular Design:** Clean separation of concerns (Core, Strategies, Utils, Config).

## 🛠️ Setup & Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/ankushsutar/nifty_trading_bot.git
    cd nifty_trading_bot
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure Credentials:**
    Create a `.env` file in the root directory (copied from `.env.example` if available) and add your Angel One credentials:
    ```ini
    API_KEY=your_api_key
    CLIENT_ID=your_client_id
    PASSWORD=your_password
    TOTP_SECRET=your_totp_secret
    ```

## 🏃 How to Run the Full System (Command Center)

To run the complete system (Backend API + Frontend Dashboard), you will need **two separate terminal windows**.

### Terminal 1: Backend API 🧠
This starts the Python server that manages the bot and connects to Angel One.
```bash
# From project root (nifty_trading_bot/)
python3 -m uvicorn backend.server:app --reload
```
*   **Status**: Online at `http://localhost:8000`
*   **API Docs**: `http://localhost:8000/docs`

### Terminal 2: Frontend Dashboard 🖥️
This starts the Next.js User Interface.
```bash
# From project root (nifty_trading_bot/)
cd frontend
npm run dev
```
*   **dashboard**: Open `http://localhost:3000` in your browser.

---

## 🔧 Legacy CLI Usage (Optional)
If you prefer running the bot without the UI:

### 1. Mock Mode (Safe for Testing)
```bash
python3 main.py --test
```

### 2. Verify Credentials (Safe)
```bash
python3 tests/verify_login.py
```

### 3. Dry Run (Real Data, No Trades) 🟡 
```bash
python3 main.py --dry-run
```

### 4. Live Trading 🔴
**WARNING:** This will place REAL orders on your Angel One account.
```bash
python3 main.py
```

### 6. Strategy Selection 🧠
By default, the bot now uses the **Momentum Strategy (EMA + RSI)** for intelligent directional entries.
*   **Timeframe:** 5-minute candles.
*   **Signals:** 
    *   **Buy CE:** EMA 9 Crosses Above 21 + RSI < 70
    *   **Buy PE:** EMA 9 Crosses Below 21 + RSI > 30 
*   **Trailing Stop:** Automatic step-ladder trailing stop to lock in profits.

## 📂 Project Structure

```text
nifty_trading_bot/
├── config/
│   └── settings.py          # Configuration and secrets management
├── core/
│   ├── angel_connect.py     # Real SmartAPI connection logic
│   ├── data_fetcher.py      # Resilient Candle Data Fetching
│   ├── safety_checks.py     # Risk Management
│   └── mock_connect.py      # Mock classes for local testing
├── strategies/
│   ├── momentum_strategy.py # Main EMA+RSI Logic
│   └── nifty_straddle.py    # Legacy Straddle logic
├── utils/
│   ├── logger.py            # Centralized Logger
│   ├── expiry_calculator.py # Logic for finding next Thursday
│   └── token_lookup.py      # Parsing Scrip Master for token IDs
├── main.py                  # Entry point
├── .env                     # Secrets (Not committed)
└── requirements.txt         # Python dependencies
```

## ⚠️ Disclaimer
This bot is for educational purposes only. Algorithmic trading involves significant financial risk. The developers are not responsible for any financial losses incurred by using this software. Always test thoroughly in Mock Mode before going live.
