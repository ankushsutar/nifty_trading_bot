# Nifty Options Trading Bot

A Python-based algorithmic trading bot for Nifty 50 Options, designed to work with the **Angel One SmartAPI**. It implements a **Straddle Strategy** (buying both CE and PE) with automatic ATM strike selection and dynamic weekly expiry calculation.

## 🚀 Key Features

*   **Dynamic Straddle Strategy:** Automatically calculates the At-The-Money (ATM) strike based on the live Nifty 50 spot price and places dual-leg (CE + PE) orders.
*   **Auto-Expiry Calculation:** Automatically determines the nearest upcoming Thursday expiry date, so you don't need to manually update dates.
*   **Mock Mode (`--test`):** A robust local testing mode that simulates market data and order placement without using real API credentials or money.
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

## 🏃 Usage

### 1. Mock Mode (Safe for Testing)
Use this mode to verify logic without connecting to the broker or verifying credentials.
```bash
python3 main.py --test
```
*   **What it does:** Returns a random Nifty spot price (~23000), calculates the ATM strike, selects the next Thursday expiry, and simulates placing fake orders.

### 2. Live Trading
**WARNING:** This will place REAL orders on your Angel One account.
```bash
python3 main.py
```
*   **What it does:** Connects to Angel One, authenticates using TOTP, downloads the Master Scrip file (first time), fetches live Nifty LTP, and executes the Straddle strategy.

## 📂 Project Structure

```text
nifty_trading_bot/
├── config/
│   └── settings.py          # Configuration and secrets management
├── core/
│   ├── angel_connect.py     # Real SmartAPI connection logic
│   └── mock_connect.py      # Mock classes for local testing
├── strategies/
│   └── nifty_straddle.py    # Main Straddle logic (ATM calculation + Execution)
├── utils/
│   ├── expiry_calculator.py # Logic for finding next Thursday
│   └── token_lookup.py      # Parsing Scrip Master for token IDs
├── main.py                  # Entry point
├── .env                     # Secrets (Not committed)
└── requirements.txt         # Python dependencies
```

## ⚠️ Disclaimer
This bot is for educational purposes only. Algorithmic trading involves significant financial risk. The developers are not responsible for any financial losses incurred by using this software. Always test thoroughly in Mock Mode before going live.
