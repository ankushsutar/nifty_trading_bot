# Antigravity Prompt — Nifty 50 Options Selling Strategy Module

Paste this entire prompt into Antigravity. It will implement the full premium selling engine as a module that plugs into your existing buying bot.

---

## PROMPT START

You are building a **Nifty 50 Options Premium Selling Strategy Module** in Python. This is a self-contained module that will be imported into an existing options buying bot. Do not touch or modify any existing buying strategy code. Add this as a completely separate `selling_engine.py` module with a clean interface.

---

### MODULE OVERVIEW

Build a class called `NiftySellingEngine` with three sub-strategies:
1. **Iron Condor** — weekly, Monday entry
2. **Short Strangle** — weekly, Monday/Tuesday entry
3. **Iron Fly** — expiry day only (Tuesday), 9:20–9:45 AM entry

Each strategy must share common infrastructure:
- VIX gate (entry only allowed in valid VIX range)
- Position sizing engine (based on capital and margin per lot)
- Strike selector (based on delta targets)
- Adjustment manager (handles mid-week breaches)
- Exit manager (handles take-profit, stop-loss, time-based exits)
- Weekly P&L tracker (logs every trade outcome)

---

### FILE STRUCTURE TO CREATE

```
selling_engine/
├── __init__.py
├── engine.py               ← Main NiftySellingEngine class
├── strategies/
│   ├── __init__.py
│   ├── iron_condor.py
│   ├── short_strangle.py
│   └── iron_fly.py
├── core/
│   ├── __init__.py
│   ├── vix_gate.py         ← VIX validation logic
│   ├── strike_selector.py  ← Delta-based strike selection
│   ├── position_sizer.py   ← Lot size calculation
│   ├── exit_manager.py     ← All exit logic
│   ├── adjustment.py       ← Mid-trade adjustment logic
│   └── pnl_tracker.py      ← Weekly P&L logging
└── config.py               ← All strategy parameters in one place
```

---

### CONFIG (config.py)

Define all parameters here. Nothing should be hardcoded inside strategy files.

```python
SELLING_CONFIG = {

    # Capital & risk
    "total_capital": 200000,          # Total capital in INR
    "max_weekly_loss_pct": 5,         # Stop all trading if weekly loss > 5% of capital
    "max_deploy_pct": 50,             # Never deploy more than 50% of capital in one position
    "lot_size": 75,                   # Nifty lot size (update if NSE changes this)

    # VIX gates (check before ANY trade)
    "vix_min": 11,                    # Below this: premiums too low, skip
    "vix_max": 25,                    # Above this: tail risk too high, skip
    "vix_high_threshold": 20,         # Above this: reduce position size by 50%

    # Iron Condor parameters
    "ic_delta_target": 0.15,          # Sell strikes at this delta on both sides
    "ic_wing_gap": 100,               # Wing protection distance in points
    "ic_take_profit_pct": 50,         # Exit when 50% of premium collected
    "ic_stop_loss_multiplier": 2.0,   # Exit when loss = 2x premium received
    "ic_entry_day": "Monday",
    "ic_entry_window": ("09:30", "11:00"),
    "ic_exit_deadline": ("Wednesday", "14:00"),

    # Short Strangle parameters
    "sc_delta_target": 0.15,          # Sell strikes at this delta
    "sc_take_profit_pct": 45,         # Exit at 45% premium collected
    "sc_stop_loss_multiplier": 2.0,
    "sc_adjustment_trigger_pts": 200, # Roll if Nifty moves 200 pts toward short strike
    "sc_adjustment_roll_pts": 100,    # Roll the breached side 100 pts further OTM
    "sc_entry_days": ["Monday", "Tuesday"],
    "sc_entry_window": ("09:30", "11:00"),
    "sc_exit_deadline": ("Wednesday", "14:00"),

    # Iron Fly parameters (expiry day only)
    "if_wing_gap": 200,               # Wings 200 pts from ATM on each side
    "if_take_profit_pct": 40,         # Exit at 40% premium collected
    "if_stop_loss_pts": 150,          # Exit if Nifty moves 150 pts from ATM strike
    "if_entry_window": ("09:20", "09:45"),
    "if_exit_deadline": ("14:00",),   # Hard exit by 2 PM on expiry day
    "if_entry_day": "Tuesday",        # Nifty weekly expiry day

    # Max Pain settings
    "max_pain_magnet_threshold": 200, # If Nifty is 200+ pts from Max Pain, bias strikes
}
```

---

### CORE: vix_gate.py

```python
"""
VIX Gate — validates whether current VIX allows premium selling.
Call this BEFORE any strategy entry. If it returns False, abort entry.
"""

class VIXGate:
    def __init__(self, config: dict):
        self.config = config

    def check(self, current_vix: float) -> dict:
        """
        Returns:
            {
                "allowed": bool,
                "size_multiplier": float,  # 1.0 = normal, 0.5 = half size, 0.0 = no trade
                "reason": str,
                "recommended_strategy": str or None
            }
        """
        vix = current_vix
        vmin = self.config["vix_min"]
        vmax = self.config["vix_max"]
        vhigh = self.config["vix_high_threshold"]

        if vix < vmin:
            return {
                "allowed": False,
                "size_multiplier": 0.0,
                "reason": f"VIX {vix} below minimum {vmin}. Premiums too thin. Consider calendar spread.",
                "recommended_strategy": "calendar_spread"
            }
        elif vix > vmax:
            return {
                "allowed": False,
                "size_multiplier": 0.0,
                "reason": f"VIX {vix} above maximum {vmax}. Extreme tail risk. Sit out.",
                "recommended_strategy": None
            }
        elif vix > vhigh:
            return {
                "allowed": True,
                "size_multiplier": 0.5,
                "reason": f"VIX {vix} elevated. Using 50% position size. Iron condor only.",
                "recommended_strategy": "iron_condor"
            }
        else:
            return {
                "allowed": True,
                "size_multiplier": 1.0,
                "reason": f"VIX {vix} in optimal range. Full position size allowed.",
                "recommended_strategy": "iron_condor"
            }
```

---

### CORE: strike_selector.py

```python
"""
Strike Selector — selects OTM strikes based on delta targets.

For live trading: connect to broker API to fetch real option chain with delta values.
For backtesting: use approximation formulas below.
"""

import math

class StrikeSelector:
    def __init__(self, config: dict):
        self.config = config

    def get_atm_strike(self, spot: float, step: int = 50) -> int:
        """Round spot to nearest strike step."""
        return round(spot / step) * step

    def delta_to_strike_approx(
        self,
        spot: float,
        target_delta: float,
        vix: float,
        days_to_expiry: int,
        option_type: str  # "call" or "put"
    ) -> int:
        """
        Approximate strike for a given delta using Black-Scholes inversion.
        Replace with live broker delta lookup in production.

        target_delta: 0.15 for OTM, 0.50 for ATM
        option_type: "call" (positive delta) or "put" (negative delta)
        """
        sigma = vix / 100
        T = days_to_expiry / 365
        r = 0.065  # Risk-free rate approximation (Indian 10Y Gsec)

        if T <= 0:
            T = 1/365

        # Invert Black-Scholes to find d1 from delta
        # For call: delta = N(d1), for put: delta = N(d1) - 1
        from scipy.stats import norm

        if option_type == "call":
            d1 = norm.ppf(target_delta)
        else:
            d1 = norm.ppf(1 - target_delta)

        # d1 = (ln(S/K) + (r + 0.5*sigma^2)*T) / (sigma * sqrt(T))
        # Solve for K:
        # ln(S/K) = d1 * sigma * sqrt(T) - (r + 0.5*sigma^2)*T
        # K = S * exp(-(d1 * sigma * sqrt(T) - (r + 0.5*sigma^2)*T))

        log_SK = d1 * sigma * math.sqrt(T) - (r + 0.5 * sigma**2) * T
        K = spot * math.exp(-log_SK)

        # Round to nearest 50
        return round(K / 50) * 50

    def select_iron_condor_strikes(
        self,
        spot: float,
        vix: float,
        days_to_expiry: int,
        max_pain: float = None
    ) -> dict:
        """
        Returns all 4 iron condor strikes.
        Adjusts for max pain magnet if provided.
        """
        delta = self.config["ic_delta_target"]
        wing = self.config["ic_wing_gap"]

        short_call = self.delta_to_strike_approx(spot, delta, vix, days_to_expiry, "call")
        short_put = self.delta_to_strike_approx(spot, delta, vix, days_to_expiry, "put")

        # Max pain adjustment: bias the range toward max pain
        if max_pain and abs(spot - max_pain) > self.config.get("max_pain_magnet_threshold", 200):
            if spot > max_pain:
                # Nifty above max pain, likely to drift down → widen put side slightly
                short_put -= 50
            else:
                # Nifty below max pain, likely to drift up → widen call side slightly
                short_call += 50

        return {
            "short_call": short_call,
            "long_call": short_call + wing,
            "short_put": short_put,
            "long_put": short_put - wing,
            "spread_width": wing
        }

    def select_strangle_strikes(
        self,
        spot: float,
        vix: float,
        days_to_expiry: int
    ) -> dict:
        delta = self.config["sc_delta_target"]
        return {
            "short_call": self.delta_to_strike_approx(spot, delta, vix, days_to_expiry, "call"),
            "short_put": self.delta_to_strike_approx(spot, delta, vix, days_to_expiry, "put")
        }

    def select_iron_fly_strikes(self, spot: float) -> dict:
        atm = self.get_atm_strike(spot)
        wing = self.config["if_wing_gap"]
        return {
            "atm_strike": atm,
            "short_call": atm,
            "long_call": atm + wing,
            "short_put": atm,
            "long_put": atm - wing
        }
```

---

### CORE: position_sizer.py

```python
"""
Position Sizer — calculates how many lots to sell based on capital and VIX.
"""

class PositionSizer:
    def __init__(self, config: dict):
        self.config = config

    def calculate_lots(
        self,
        strategy: str,        # "iron_condor", "short_strangle", "iron_fly"
        vix_multiplier: float # From VIXGate: 1.0 or 0.5
    ) -> dict:
        capital = self.config["total_capital"]
        deploy_pct = self.config["max_deploy_pct"] / 100
        lot_size = self.config["lot_size"]

        # Approximate margin requirements per lot (update from broker)
        margin_map = {
            "iron_condor": 40000,    # Defined risk = lower margin
            "short_strangle": 85000, # Naked = higher margin
            "iron_fly": 55000        # Defined risk, expiry day
        }

        margin_per_lot = margin_map.get(strategy, 50000)
        deployable = capital * deploy_pct * vix_multiplier
        lots = max(1, int(deployable / margin_per_lot))

        return {
            "lots": lots,
            "units": lots * lot_size,
            "margin_used": lots * margin_per_lot,
            "margin_pct": round(lots * margin_per_lot / capital * 100, 1)
        }
```

---

### CORE: exit_manager.py

```python
"""
Exit Manager — monitors all open selling positions and triggers exits.
Call check_exits() in your main bot loop every 1–5 minutes during market hours.
"""

from datetime import datetime, time

class ExitManager:
    def __init__(self, config: dict):
        self.config = config

    def check_exits(self, position: dict, current_data: dict) -> dict:
        """
        Args:
            position: {
                "strategy": str,
                "entry_premium": float,       # Total premium collected per unit
                "current_premium": float,     # Current mark-to-market premium
                "entry_time": datetime,
                "short_call_strike": float,
                "short_put_strike": float,
                "current_spot": float
            }
            current_data: {
                "spot": float,
                "time": datetime,
                "vix": float
            }

        Returns:
            {
                "should_exit": bool,
                "exit_type": str,   # "take_profit" | "stop_loss" | "time_exit" | "hold"
                "reason": str,
                "urgency": str      # "immediate" | "next_candle" | "eod"
            }
        """
        strategy = position["strategy"]
        entry_prem = position["entry_premium"]
        curr_prem = position["current_premium"]
        spot = current_data["spot"]
        now = current_data["time"]

        # --- Take profit checks ---
        tp_pct = {
            "iron_condor": self.config["ic_take_profit_pct"],
            "short_strangle": self.config["sc_take_profit_pct"],
            "iron_fly": self.config["if_take_profit_pct"]
        }.get(strategy, 50)

        profit_pct = (entry_prem - curr_prem) / entry_prem * 100
        if profit_pct >= tp_pct:
            return {
                "should_exit": True,
                "exit_type": "take_profit",
                "reason": f"Target hit: {profit_pct:.1f}% of premium collected (target {tp_pct}%)",
                "urgency": "next_candle"
            }

        # --- Stop loss checks ---
        sl_mult = {
            "iron_condor": self.config["ic_stop_loss_multiplier"],
            "short_strangle": self.config["sc_stop_loss_multiplier"],
            "iron_fly": None  # Iron fly uses point-based SL
        }.get(strategy)

        if sl_mult and curr_prem >= entry_prem * (1 + sl_mult - 1):
            loss_pct = (curr_prem - entry_prem) / entry_prem * 100
            return {
                "should_exit": True,
                "exit_type": "stop_loss",
                "reason": f"Stop loss: position loss {loss_pct:.1f}% (max {(sl_mult-1)*100:.0f}%)",
                "urgency": "immediate"
            }

        # Iron fly point-based stop
        if strategy == "iron_fly":
            atm = position.get("atm_strike", spot)
            sl_pts = self.config["if_stop_loss_pts"]
            if abs(spot - atm) >= sl_pts:
                return {
                    "should_exit": True,
                    "exit_type": "stop_loss",
                    "reason": f"Iron fly SL: Nifty moved {abs(spot-atm):.0f} pts from ATM (limit {sl_pts})",
                    "urgency": "immediate"
                }

        # --- Time-based exits ---
        current_time = now.time()

        if strategy == "iron_fly":
            deadline = time(14, 0)
            if current_time >= deadline:
                return {
                    "should_exit": True,
                    "exit_type": "time_exit",
                    "reason": "Iron fly 2 PM hard exit. Never hold past this on expiry day.",
                    "urgency": "immediate"
                }

        if strategy in ("iron_condor", "short_strangle"):
            if now.strftime("%A") == "Wednesday" and current_time >= time(14, 0):
                return {
                    "should_exit": True,
                    "exit_type": "time_exit",
                    "reason": "Wednesday 2 PM deadline. Exit to avoid expiry day gamma risk.",
                    "urgency": "immediate"
                }

        return {
            "should_exit": False,
            "exit_type": "hold",
            "reason": f"No exit condition met. P&L: {profit_pct:.1f}% collected.",
            "urgency": None
        }
```

---

### CORE: adjustment.py

```python
"""
Adjustment Manager — handles mid-week strike breaches for short strangle.
When Nifty breaks toward a short strike, this rolls that side further OTM.
"""

class AdjustmentManager:
    def __init__(self, config: dict):
        self.config = config

    def check_adjustment_needed(self, position: dict, spot: float) -> dict:
        """
        Returns adjustment instructions if Nifty is approaching a short strike.
        Only applies to short strangle (iron condor has defined risk via wings).
        """
        if position["strategy"] != "short_strangle":
            return {"adjustment_needed": False}

        trigger = self.config["sc_adjustment_trigger_pts"]
        roll = self.config["sc_adjustment_roll_pts"]

        short_call = position["short_call_strike"]
        short_put = position["short_put_strike"]

        call_distance = short_call - spot
        put_distance = spot - short_put

        if call_distance <= trigger:
            new_call = short_call + roll
            return {
                "adjustment_needed": True,
                "side": "call",
                "action": f"Roll short call from {short_call} to {new_call}",
                "buy_back": short_call,
                "sell_new": new_call,
                "reason": f"Nifty {spot} is only {call_distance:.0f} pts from short call {short_call}. Trigger: {trigger} pts.",
                "urgency": "immediate" if call_distance < 100 else "next_candle"
            }

        if put_distance <= trigger:
            new_put = short_put - roll
            return {
                "adjustment_needed": True,
                "side": "put",
                "action": f"Roll short put from {short_put} to {new_put}",
                "buy_back": short_put,
                "sell_new": new_put,
                "reason": f"Nifty {spot} is only {put_distance:.0f} pts from short put {short_put}. Trigger: {trigger} pts.",
                "urgency": "immediate" if put_distance < 100 else "next_candle"
            }

        return {
            "adjustment_needed": False,
            "call_distance": round(call_distance),
            "put_distance": round(put_distance)
        }
```

---

### CORE: pnl_tracker.py

```python
"""
P&L Tracker — logs every trade and generates weekly/monthly reports.
Saves to JSON file. Add database integration as needed.
"""

import json
import os
from datetime import datetime, date

class PnLTracker:
    def __init__(self, log_file: str = "selling_pnl.json"):
        self.log_file = log_file
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.log_file):
            with open(self.log_file, "r") as f:
                return json.load(f)
        return {"trades": [], "weekly_summary": {}}

    def _save(self):
        with open(self.log_file, "w") as f:
            json.dump(self.data, f, indent=2, default=str)

    def log_trade(
        self,
        strategy: str,
        entry_premium: float,
        exit_premium: float,
        lots: int,
        lot_size: int,
        exit_type: str,  # "take_profit" | "stop_loss" | "time_exit"
        notes: str = ""
    ):
        units = lots * lot_size
        pnl = (entry_premium - exit_premium) * units
        week_key = date.today().strftime("%Y-W%W")

        trade = {
            "date": str(date.today()),
            "week": week_key,
            "strategy": strategy,
            "entry_premium": entry_premium,
            "exit_premium": exit_premium,
            "lots": lots,
            "units": units,
            "pnl_inr": round(pnl),
            "exit_type": exit_type,
            "notes": notes,
            "timestamp": str(datetime.now())
        }
        self.data["trades"].append(trade)

        # Update weekly summary
        if week_key not in self.data["weekly_summary"]:
            self.data["weekly_summary"][week_key] = {
                "total_pnl": 0, "wins": 0, "losses": 0, "trades": 0
            }
        ws = self.data["weekly_summary"][week_key]
        ws["total_pnl"] += round(pnl)
        ws["trades"] += 1
        if pnl >= 0:
            ws["wins"] += 1
        else:
            ws["losses"] += 1

        self._save()
        return trade

    def get_weekly_summary(self, week_key: str = None) -> dict:
        if not week_key:
            week_key = date.today().strftime("%Y-W%W")
        return self.data["weekly_summary"].get(week_key, {
            "total_pnl": 0, "wins": 0, "losses": 0, "trades": 0
        })

    def check_weekly_loss_limit(self, capital: float, max_loss_pct: float) -> bool:
        """Returns True if weekly loss limit breached — halt all trading."""
        summary = self.get_weekly_summary()
        max_loss = capital * max_loss_pct / 100
        return summary["total_pnl"] < -max_loss

    def get_monthly_report(self) -> dict:
        month_key = date.today().strftime("%Y-%m")
        month_trades = [t for t in self.data["trades"] if t["date"].startswith(month_key)]
        total_pnl = sum(t["pnl_inr"] for t in month_trades)
        wins = sum(1 for t in month_trades if t["pnl_inr"] >= 0)
        return {
            "month": month_key,
            "total_pnl": total_pnl,
            "trades": len(month_trades),
            "wins": wins,
            "losses": len(month_trades) - wins,
            "win_rate_pct": round(wins / len(month_trades) * 100) if month_trades else 0
        }
```

---

### MAIN ENGINE: engine.py

```python
"""
NiftySellingEngine — main entry point.
Import this into your existing bot and call run_selling_cycle() every morning.
"""

from datetime import datetime, time
from .config import SELLING_CONFIG
from .core.vix_gate import VIXGate
from .core.strike_selector import StrikeSelector
from .core.position_sizer import PositionSizer
from .core.exit_manager import ExitManager
from .core.adjustment import AdjustmentManager
from .core.pnl_tracker import PnLTracker

class NiftySellingEngine:
    def __init__(self, config: dict = None):
        self.config = config or SELLING_CONFIG
        self.vix_gate = VIXGate(self.config)
        self.strike_selector = StrikeSelector(self.config)
        self.position_sizer = PositionSizer(self.config)
        self.exit_manager = ExitManager(self.config)
        self.adjustment_manager = AdjustmentManager(self.config)
        self.pnl_tracker = PnLTracker()
        self.open_positions = []  # List of active selling positions

    def run_selling_cycle(self, market_data: dict) -> dict:
        """
        Call this every morning at 9:25 AM with current market data.

        Args:
            market_data: {
                "spot": float,          # Current Nifty spot price
                "vix": float,           # India VIX
                "max_pain": float,      # From NSE option chain
                "days_to_expiry": int,  # Calendar days to weekly expiry (Tuesday)
                "time": datetime        # Current timestamp
            }

        Returns:
            {
                "action": str,          # "enter_trade" | "skip" | "monitor_only"
                "strategy": str,
                "strikes": dict,
                "sizing": dict,
                "vix_status": dict,
                "reason": str
            }
        """
        spot = market_data["spot"]
        vix = market_data["vix"]
        now = market_data["time"]
        day_name = now.strftime("%A")
        max_pain = market_data.get("max_pain", spot)
        dte = market_data.get("days_to_expiry", 5)

        # Step 1: Check weekly loss limit
        if self.pnl_tracker.check_weekly_loss_limit(
            self.config["total_capital"],
            self.config["max_weekly_loss_pct"]
        ):
            return {
                "action": "skip",
                "strategy": None,
                "reason": "Weekly loss limit hit (5% of capital). Halting all selling for the week.",
                "vix_status": None
            }

        # Step 2: VIX gate
        vix_result = self.vix_gate.check(vix)
        if not vix_result["allowed"]:
            return {
                "action": "skip",
                "strategy": None,
                "vix_status": vix_result,
                "reason": vix_result["reason"]
            }

        # Step 3: Strategy selection by day and time
        current_time = now.time()

        # Iron Fly — Tuesday expiry day, 9:20–9:45 AM only
        if (day_name == "Tuesday" and dte <= 1 and
                time(9, 20) <= current_time <= time(9, 45)):
            strikes = self.strike_selector.select_iron_fly_strikes(spot)
            sizing = self.position_sizer.calculate_lots("iron_fly", vix_result["size_multiplier"])
            return {
                "action": "enter_trade",
                "strategy": "iron_fly",
                "strikes": strikes,
                "sizing": sizing,
                "vix_status": vix_result,
                "reason": "Expiry day iron fly conditions met."
            }

        # Iron Condor — Monday, 9:30–11:00 AM
        if (day_name == "Monday" and dte >= 4 and
                time(9, 30) <= current_time <= time(11, 0)):
            strikes = self.strike_selector.select_iron_condor_strikes(spot, vix, dte, max_pain)
            sizing = self.position_sizer.calculate_lots("iron_condor", vix_result["size_multiplier"])
            return {
                "action": "enter_trade",
                "strategy": "iron_condor",
                "strikes": strikes,
                "sizing": sizing,
                "vix_status": vix_result,
                "reason": "Iron condor entry conditions met."
            }

        # Short Strangle — Monday or Tuesday, if iron condor skipped
        if (day_name in ["Monday", "Tuesday"] and dte >= 3 and
                time(9, 30) <= current_time <= time(11, 0) and
                vix <= 18):
            strikes = self.strike_selector.select_strangle_strikes(spot, vix, dte)
            sizing = self.position_sizer.calculate_lots("short_strangle", vix_result["size_multiplier"])
            return {
                "action": "enter_trade",
                "strategy": "short_strangle",
                "strikes": strikes,
                "sizing": sizing,
                "vix_status": vix_result,
                "reason": "Short strangle entry conditions met."
            }

        return {
            "action": "monitor_only",
            "strategy": None,
            "vix_status": vix_result,
            "reason": f"No entry conditions met for {day_name} at {current_time}. Monitoring open positions."
        }

    def monitor_open_positions(self, current_data: dict) -> list:
        """
        Call this every 1–5 minutes during market hours.
        Returns list of actions needed on open positions.
        """
        actions = []
        for position in self.open_positions:
            # Check exits
            exit_result = self.exit_manager.check_exits(position, current_data)
            if exit_result["should_exit"]:
                actions.append({
                    "type": "exit",
                    "position": position,
                    "exit_info": exit_result
                })
                continue

            # Check adjustments (short strangle only)
            adj_result = self.adjustment_manager.check_adjustment_needed(
                position, current_data["spot"]
            )
            if adj_result["adjustment_needed"]:
                actions.append({
                    "type": "adjustment",
                    "position": position,
                    "adjustment_info": adj_result
                })

        return actions

    def add_position(self, position: dict):
        """Call after successfully placing a selling trade."""
        self.open_positions.append(position)

    def close_position(self, position: dict, exit_premium: float, exit_type: str):
        """Call after successfully closing a position."""
        self.open_positions = [p for p in self.open_positions if p != position]
        self.pnl_tracker.log_trade(
            strategy=position["strategy"],
            entry_premium=position["entry_premium"],
            exit_premium=exit_premium,
            lots=position["lots"],
            lot_size=self.config["lot_size"],
            exit_type=exit_type
        )
```

---

### HOW TO INTEGRATE WITH YOUR EXISTING BOT

Add these lines to your existing main bot file:

```python
from selling_engine.engine import NiftySellingEngine

# Initialize once at startup
selling_engine = NiftySellingEngine()

# In your main morning loop (run once at 9:25 AM):
market_data = {
    "spot": get_nifty_spot(),          # Your existing spot fetch function
    "vix": get_india_vix(),            # Fetch from NSE or broker API
    "max_pain": get_max_pain(),        # Fetch from NSE option chain
    "days_to_expiry": get_dte(),       # Calculate from today to next Tuesday
    "time": datetime.now()
}
selling_decision = selling_engine.run_selling_cycle(market_data)

if selling_decision["action"] == "enter_trade":
    # Place your orders via broker API using selling_decision["strikes"] and ["sizing"]
    # Then register the position:
    selling_engine.add_position({
        "strategy": selling_decision["strategy"],
        **selling_decision["strikes"],
        "entry_premium": actual_premium_received,
        "lots": selling_decision["sizing"]["lots"],
        "entry_time": datetime.now()
    })

# In your every-5-minute monitoring loop:
actions = selling_engine.monitor_open_positions({
    "spot": get_nifty_spot(),
    "time": datetime.now(),
    "vix": get_india_vix()
})
for action in actions:
    if action["type"] == "exit":
        # Execute exit orders via broker API
        selling_engine.close_position(
            action["position"],
            exit_premium=current_premium,
            exit_type=action["exit_info"]["exit_type"]
        )
    elif action["type"] == "adjustment":
        # Execute roll orders via broker API
        print(action["adjustment_info"]["action"])
```

---

### ADDITIONAL INSTRUCTIONS FOR ANTIGRAVITY

1. Install required packages: `scipy` (for Black-Scholes delta calculation)
2. Replace all `get_nifty_spot()`, `get_india_vix()`, `get_max_pain()` placeholders with actual broker API calls from the existing buying bot
3. Add logging to every function using the same logger as the existing bot
4. Add unit tests for `VIXGate`, `StrikeSelector`, and `ExitManager` in a `tests/` folder
5. The `delta_to_strike_approx()` method in `StrikeSelector` should be replaced with live option chain delta lookup from broker API once confirmed working
6. All config values in `config.py` should be overridable via environment variables for production deployment
7. Add a `dry_run: bool` flag to `NiftySellingEngine.__init__()` — when True, log all decisions but do not place any actual orders

## PROMPT END
