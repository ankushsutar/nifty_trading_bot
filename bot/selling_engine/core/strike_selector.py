"""
Strike Selector — selects OTM strikes based on delta targets.
"""

import math
from scipy.stats import norm
from bot.utils.logger import logger

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
        Replace with live broker delta lookup in production if available.

        target_delta: e.g. 0.15 for OTM, 0.50 for ATM
        option_type: "call" (positive delta) or "put" (negative delta)
        """
        sigma = vix / 100
        T = days_to_expiry / 365
        r = 0.065  # Risk-free rate approximation (Indian 10Y Gsec)

        if T <= 0:
            T = 1/365

        # Invert Black-Scholes to find d1 from delta
        # For call: delta = N(d1), for put: delta = N(d1) - 1
        if option_type == "call":
            d1 = norm.ppf(target_delta)
        else:
            d1 = norm.ppf(1 - target_delta)

        # d1 = (ln(S/K) + (r + 0.5*sigma^2)*T) / (sigma * sqrt(T))
        # Solve for K:
        # ln(S/K) = d1 * sigma * sqrt(T) - (r + 0.5*sigma^2)*T
        # K = S * exp(-(d1 * sigma * sqrt(T) - (r + 0.5*sigma^2)*T))

        try:
            log_SK = d1 * sigma * math.sqrt(T) - (r + 0.5 * sigma**2) * T
            K = spot * math.exp(-log_SK)
            # Round to nearest 50
            return round(K / 50) * 50
        except Exception as e:
            logger.error(f">>> [StrikeSelector] Delta to Strike approximation error: {e}")
            # Fallback to simple spot offset if math fails
            offset = 200 if target_delta < 0.2 else 0
            if option_type == "call":
                return self.get_atm_strike(spot + offset)
            else:
                return self.get_atm_strike(spot - offset)

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
    
    def calculate_max_pain(self, option_chain: list) -> int:
        """
        Calculates Max Pain from a list of option chain data points.
        Each item in option_chain should be: {'strike': int, 'ce_oi': int, 'pe_oi': int}
        """
        if not option_chain:
            return 0
            
        strikes = [x['strike'] for x in option_chain]
        min_pain = float('inf')
        max_pain_strike = strikes[0]
        
        for hypothetical_expiry in strikes:
            total_pain = 0
            for row in option_chain:
                strike = row['strike']
                ce_oi = row.get('ce_oi', 0)
                pe_oi = row.get('pe_oi', 0)
                
                # Pain for CE holders if expiry is above strike
                if hypothetical_expiry > strike:
                    total_pain += (hypothetical_expiry - strike) * ce_oi
                # Pain for PE holders if expiry is below strike
                elif hypothetical_expiry < strike:
                    total_pain += (strike - hypothetical_expiry) * pe_oi
                    
            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = hypothetical_expiry
                
        return max_pain_strike
