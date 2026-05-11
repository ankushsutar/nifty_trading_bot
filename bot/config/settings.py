import os
from dataclasses import dataclass
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


# ─────────────────────────────────────────────────────────────────────────────
# CAPITAL TIER SYSTEM
# ─────────────────────────────────────────────────────────────────────────────
# All risk, sizing, and strategy parameters are defined here per capital tier.
# The bot auto-selects the correct tier based on current live capital.
# To change behavior: edit the tier values below — nothing else needs changing.
#
# Tier boundaries (₹):
#   MICRO  : < 25,000
#   SMALL  : 25,000 – 1,00,000
#   MEDIUM : 1,00,000 – 5,00,000
#   LARGE  : > 5,00,000
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CapitalTier:
    # Identity
    name: str
    capital_min: float
    capital_max: Optional[float]          # None = no upper bound

    # ── Risk parameters ────────────────────────────────────────────────────
    risk_per_trade_pct: float             # Fraction of capital risked per trade
    max_daily_loss_pct: float             # Daily halt trigger (fraction of capital)
    max_capital_usage_pct: float          # Max fraction of capital per single trade

    # ── Entry quality gate ─────────────────────────────────────────────────
    min_adx_to_trade: float               # Hard gate — no trade below this ADX
    adx_gamma_blast: float                # Escalate to GAMMA_BLAST above this ADX
    adx_trend_fade_exit: float            # Exit existing position if ADX drops below this

    # ── Position sizing ────────────────────────────────────────────────────
    max_lots: int                         # Hard ceiling (0 = unlimited, governed by % only)
    margin_buffer_pct: float              # Buffer on top of raw margin for lot calculation
    gamma_blast_lot_pct: float            # Fraction of compounded lots used by Gamma Blast
    sl_pct: float                         # Stop-loss as fraction of fill price
    min_sl_points: float                  # Floor to prevent "choking" on low premiums

    # ── Session rules ──────────────────────────────────────────────────────
    max_trades_per_day: int
    max_consecutive_losses: int

    # ── Strategy whitelist ─────────────────────────────────────────────────
    allowed_strategies: List[str]

    # ── Capital floor ──────────────────────────────────────────────────────
    min_capital_threshold: float          # Bot stops permanently below this
    min_risk_floor: float                 # Minimum ₹ risk per trade even if % is tiny

    # ── Execution ──────────────────────────────────────────────────────────
    entry_slippage_pct: float             # Buffer for LIMIT orders

    # ── Volatility (VIX & Indicators) ───────────────────────────────────
    vix_reduction_threshold: float        # VIX level that triggers qty halving
    vix_qty_multiplier: float             # Qty multiplier when VIX exceeded
    min_bbw_to_trade: float               # Minimum Bollinger Band Width to allow entry


# ─────────────────────────────────────────────────────────────────────────────
# TIER DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

CAPITAL_TIERS: dict[str, CapitalTier] = {

    # ── MICRO: < ₹25,000 ─────────────────────────────────────────────────
    # Survival mode. Only the two highest-conviction strategies.
    # 12% risk per trade = ₹1,200 on ₹10k — survives 6 consecutive losses.
    "MICRO": CapitalTier(
        name="MICRO",
        capital_min=0.0,
        capital_max=25_000.0,
        risk_per_trade_pct=0.12,
        max_daily_loss_pct=0.15,
        max_capital_usage_pct=0.90,
        min_adx_to_trade=25.0,            # Lowered from 35.0 (Trader recommendation)
        adx_gamma_blast=45.0,
        adx_trend_fade_exit=25.0,
        max_lots=1,
        margin_buffer_pct=0.10,
        gamma_blast_lot_pct=0.50,
        sl_pct=0.30,                      # Relaxed from 0.15 to prevent technical ATR truncation on cheap options
        min_sl_points=6.0,                 # Ensure at least 6 points SL for micro accounts
        max_trades_per_day=2,
        max_consecutive_losses=2,
        allowed_strategies=["MOMENTUM", "GAMMA_BLAST"],
        min_capital_threshold=3_000.0,
        min_risk_floor=500.0,
        entry_slippage_pct=0.01,
        vix_reduction_threshold=25.0,
        vix_qty_multiplier=0.5,
        min_bbw_to_trade=0.005,           # Lowered from 0.008 (Smoother trends)
    ),

    # ── SMALL: ₹25,000 – ₹1,00,000 ──────────────────────────────────────
    # Growth mode. ADX gate relaxed slightly. ORB added as a third setup.
    # 8% risk = better capital efficiency while still protecting downside.
    "SMALL": CapitalTier(
        name="SMALL",
        capital_min=25_000.0,
        capital_max=1_00_000.0,
        risk_per_trade_pct=0.08,           # Reduced from 0.12 for better safety
        max_daily_loss_pct=0.08,           # Reduced from 0.12 (Max ₹2,800 loss)
        max_capital_usage_pct=0.85,
        min_adx_to_trade=25.0,
        adx_gamma_blast=42.0,
        adx_trend_fade_exit=22.0,
        max_lots=4,                        # Capped at 4 lots for safety
        margin_buffer_pct=0.12,
        gamma_blast_lot_pct=0.60,
        sl_pct=0.35,                       # Relaxed from 0.20 to prevent technical ATR truncation on cheap options
        min_sl_points=7.0,                 # Ensure at least 7 points SL
        max_trades_per_day=3,
        max_consecutive_losses=2,
        allowed_strategies=["MOMENTUM", "GAMMA_BLAST", "STRADDLE_SCALP"],
        min_capital_threshold=8_000.0,
        min_risk_floor=500.0,
        entry_slippage_pct=0.01,
        vix_reduction_threshold=25.0,
        vix_qty_multiplier=0.5,
        min_bbw_to_trade=0.005,           # Lowered from 0.008
    ),

    # ── MEDIUM: ₹1,00,000 – ₹5,00,000 ───────────────────────────────────
    # Balanced mode. Four strategies, more trades per day, lower risk%.
    # 5% risk per trade = can take 20 consecutive losses before ruin (never happens).
    "MEDIUM": CapitalTier(
        name="MEDIUM",
        capital_min=1_00_000.0,
        capital_max=5_00_000.0,
        risk_per_trade_pct=0.05,
        max_daily_loss_pct=0.08,
        max_capital_usage_pct=0.80,
        min_adx_to_trade=25.0,
        adx_gamma_blast=40.0,
        adx_trend_fade_exit=20.0,
        max_lots=5,
        margin_buffer_pct=0.15,
        gamma_blast_lot_pct=0.70,
        sl_pct=0.18,
        min_sl_points=8.0,                 # 8 points for medium accounts
        max_trades_per_day=4,
        max_consecutive_losses=3,
        allowed_strategies=["MOMENTUM", "GAMMA_BLAST", "STRADDLE_SCALP"],
        min_capital_threshold=25_000.0,
        min_risk_floor=800.0,
        entry_slippage_pct=0.008,
        vix_reduction_threshold=22.0,
        vix_qty_multiplier=0.5,
        min_bbw_to_trade=0.005,           # Lowered from 0.008
    ),

    # ── LARGE: > ₹5,00,000 ────────────────────────────────────────────────
    # Institutional mode. All six strategies, tightest risk%, maximum diversification.
    # 3% risk = pure capital preservation with aggressive compounding.
    "LARGE": CapitalTier(
        name="LARGE",
        capital_min=5_00_000.0,
        capital_max=None,
        risk_per_trade_pct=0.03,
        max_daily_loss_pct=0.05,
        max_capital_usage_pct=0.75,
        min_adx_to_trade=20.0,
        adx_gamma_blast=38.0,
        adx_trend_fade_exit=18.0,
        max_lots=0,                       # Unlimited — governed purely by capital %
        margin_buffer_pct=0.20,
        gamma_blast_lot_pct=0.75,
        sl_pct=0.15,
        min_sl_points=10.0,                # 10 points for large accounts
        max_trades_per_day=5,
        max_consecutive_losses=3,
        allowed_strategies=["MOMENTUM", "GAMMA_BLAST", "STRADDLE_SCALP"],
        min_capital_threshold=75_000.0,
        min_risk_floor=1_500.0,
        entry_slippage_pct=0.005,
        vix_reduction_threshold=20.0,
        vix_qty_multiplier=0.6,
        min_bbw_to_trade=0.005,           # Lowered from 0.008
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CONFIG CLASS
# ─────────────────────────────────────────────────────────────────────────────

class Config:
    # ── Angel One API credentials ──────────────────────────────────────────
    API_KEY      = os.getenv("API_KEY")
    CLIENT_ID    = os.getenv("CLIENT_ID")
    PASSWORD     = os.getenv("PASSWORD")
    TOTP_SECRET  = os.getenv("TOTP_SECRET")

    # ── Master mode control ────────────────────────────────────────────────
    LIVE_TRADE_ENABLED = os.getenv("LIVE_TRADE_ENABLED", "FALSE").upper() == "TRUE"
    TRADE_FULL_DAY     = os.getenv("TRADE_FULL_DAY", "FALSE").upper() == "TRUE"

    # ── Time configuration ─────────────────────────────────────────────────
    BLACKOUT_START_TIME      = (11, 30)   # (hour, minute)
    BLACKOUT_END_TIME        = (13, 0)
    EXPIRY_ENTRY_BLOCK_TIME  = (13, 30)
    STRATEGY_EXIT_TIME       = (15, 10)

    # ── NIFTY constants ────────────────────────────────────────────────────
    ACTIVE_SYMBOL = os.getenv("ACTIVE_SYMBOL", "NIFTY")
    NIFTY_LOT_SIZE = 65                   # Updated for 2026
    SCRIP_MASTER_URL = (
        "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    )

    # ── Simulation capital ─────────────────────────────────────────────────
    # Used in dry_run mode. Set this to match your real account balance so
    # the simulation uses the same tier and sizing as the live bot.
    SIMULATION_CAPITAL = float(os.getenv("SIMULATION_CAPITAL", "11000"))

    # ── Persistence ────────────────────────────────────────────────────────
    MONGO_URI        = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    MONGO_DB         = os.getenv("MONGO_DB", "nifty_bot")
    MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "trades")

    # ── Notifications ──────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

    # ── Infrastructure ─────────────────────────────────────────────────────
    STATIC_IP = os.getenv("STATIC_IP")

    # ── Entry slippage (used in both live and simulation) ──────────────────
    # Kept here as a per-execution constant; tier overrides this for sizing math.
    ENTRY_SLIPPAGE_BUFFER_PERCENT = 0.01  # Fallback only — strategies use tier value

    # ─────────────────────────────────────────────────────────────────────
    # TIER LOOKUP — the single source of truth for all risk parameters
    # ─────────────────────────────────────────────────────────────────────
    @classmethod
    def get_tier(cls, capital: float) -> CapitalTier:
        """
        Returns the CapitalTier that matches the given capital amount.
        Always returns the most conservative applicable tier (MICRO as fallback).

        Usage:
            tier = Config.get_tier(gatekeeper.get_current_capital())
            lots = min(computed_lots, tier.max_lots) if tier.max_lots > 0 else computed_lots
        """
        # Walk tiers from highest to lowest; return first match
        for tier in sorted(CAPITAL_TIERS.values(), key=lambda t: t.capital_min, reverse=True):
            if capital >= tier.capital_min:
                return tier
        return CAPITAL_TIERS["MICRO"]

    # ─────────────────────────────────────────────────────────────────────
    # CONVENIENCE PROPERTIES  (read from the simulation tier for dry_run)
    # These are kept so existing code that imports Config.X still works
    # during the transition period. Strategies should migrate to get_tier().
    # ─────────────────────────────────────────────────────────────────────
    @classmethod
    def _sim_tier(cls) -> CapitalTier:
        return cls.get_tier(cls.SIMULATION_CAPITAL)

    # Read-only shims — returns the value from the simulation tier
    @classmethod
    def get_risk_per_trade_pct(cls) -> float:
        return cls._sim_tier().risk_per_trade_pct

    @classmethod
    def get_max_daily_loss(cls) -> float:
        """Returns the absolute max daily loss for the simulation tier."""
        tier = cls._sim_tier()
        return -(cls.SIMULATION_CAPITAL * tier.max_daily_loss_pct)

    @classmethod
    def get_min_adx(cls) -> float:
        return cls._sim_tier().min_adx_to_trade

    @classmethod
    def get_min_capital_threshold(cls) -> float:
        return cls._sim_tier().min_capital_threshold
