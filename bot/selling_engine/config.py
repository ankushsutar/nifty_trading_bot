import os
from bot.config.settings import Config

# --- SELLING ENGINE CONFIGURATION ---
# These parameters can be overridden via environment variables.

SELLING_CONFIG = {
    # Capital & Risk
    "total_capital": float(os.getenv("SELLING_TOTAL_CAPITAL", Config.SIMULATION_CAPITAL if not Config.LIVE_TRADE_ENABLED else 200000)),
    "max_weekly_loss_pct": float(os.getenv("SELLING_MAX_WEEKLY_LOSS_PCT", 5.0)),
    "max_deploy_pct": float(os.getenv("SELLING_MAX_DEPLOY_PCT", 50.0)),
    "lot_size": int(os.getenv("SELLING_LOT_SIZE", Config.NIFTY_LOT_SIZE)),

    # VIX Gates (Check before ANY trade)
    "vix_min": float(os.getenv("SELLING_VIX_MIN", 11.0)),
    "vix_max": float(os.getenv("SELLING_VIX_MAX", 25.0)),
    "vix_high_threshold": float(os.getenv("SELLING_VIX_HIGH_THRESHOLD", 20.0)),

    # Iron Condor Parameters
    "ic_delta_target": float(os.getenv("SELLING_IC_DELTA_TARGET", 0.15)),
    "ic_wing_gap": int(os.getenv("SELLING_IC_WING_GAP", 100)),
    "ic_take_profit_pct": float(os.getenv("SELLING_IC_TP_PCT", 50.0)),
    "ic_stop_loss_multiplier": float(os.getenv("SELLING_IC_SL_MULT", 2.0)),
    "ic_entry_day": os.getenv("SELLING_IC_ENTRY_DAY", "Monday"),
    "ic_entry_window": ("09:30", "11:00"),
    "ic_exit_deadline": ("Wednesday", "14:00"),

    # Short Strangle Parameters
    "sc_delta_target": float(os.getenv("SELLING_SC_DELTA_TARGET", 0.15)),
    "sc_take_profit_pct": float(os.getenv("SELLING_SC_TP_PCT", 45.0)),
    "sc_stop_loss_multiplier": float(os.getenv("SELLING_SC_SL_MULT", 2.0)),
    "sc_adjustment_trigger_pts": int(os.getenv("SELLING_SC_ADJ_TRIGGER", 200)),
    "sc_adjustment_roll_pts": int(os.getenv("SELLING_SC_ADJ_ROLL", 100)),
    "sc_entry_days": ["Monday", "Tuesday"],
    "sc_entry_window": ("09:30", "11:00"),
    "sc_exit_deadline": ("Wednesday", "14:00"),

    # Iron Fly Parameters (Expiry Day Only)
    "if_wing_gap": int(os.getenv("SELLING_IF_WING_GAP", 200)),
    "if_take_profit_pct": float(os.getenv("SELLING_IF_TP_PCT", 40.0)),
    "if_stop_loss_pts": int(os.getenv("SELLING_IF_SL_PTS", 150)),
    "if_entry_window": ("09:20", "09:45"),
    "if_exit_deadline": ("14:00",),
    "if_entry_day": os.getenv("SELLING_IF_ENTRY_DAY", "Tuesday"),

    # Max Pain Settings
    "max_pain_magnet_threshold": int(os.getenv("SELLING_MAX_PAIN_THRESHOLD", 200)),
}
