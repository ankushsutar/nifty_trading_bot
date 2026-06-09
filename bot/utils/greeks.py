import math
import datetime
from bot.utils.logger import logger


def norm_cdf(x):
    """Cumulative Distribution Function of standard normal distribution."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def parse_expiry(expiry_str: str) -> datetime.date:
    """Parses standard expiry format 'DDMMMYYYY' (e.g. '06JAN2026') to a datetime.date object."""
    try:
        day = int(expiry_str[0:2])
        month_str = expiry_str[2:5].upper()
        year = int(expiry_str[5:9])
        
        months = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
        }
        month = months[month_str]
        return datetime.date(year, month, day)
    except Exception as e:
        raise ValueError(f"Failed to parse expiry string '{expiry_str}': {e}")

def calculate_black_scholes_delta(spot: float, strike: float, days_to_expiry: float, vix: float, option_type: str = "CE") -> float:
    """
    Calculates option Delta using the Black-Scholes model.
    
    Args:
        spot: Underlying asset price (Nifty spot)
        strike: Option strike price
        days_to_expiry: Days remaining until contract expiry (can be fractional)
        vix: India VIX as a percentage proxy for implied volatility (e.g. 15.4)
        option_type: "CE" for Call, "PE" for Put
        
    Returns:
        float: Estimated option Delta (0.0 to 1.0 for Calls, -1.0 to 0.0 for Puts)
    """
    if days_to_expiry <= 0:
        # Expiry day boundary logic:
        if option_type == "CE":
            return 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
        else:
            return -1.0 if spot < strike else (-0.5 if spot == strike else 0.0)
            
    t = days_to_expiry / 365.0
    r = 0.065  # 6.5% standard Indian risk-free rate proxy
    sigma = max(0.05, vix / 100.0)  # Bound volatility from dropping below 5%
    
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * math.sqrt(t))
    
    if option_type == "CE":
        return norm_cdf(d1)
    else:
        return norm_cdf(d1) - 1.0

def select_strike_by_delta(token_lookup, spot: float, expiry: str, vix: float, option_type: str, target_delta: float, symbol_name: str = None):
    """
    Selects the option strike from TokenLookup option bucket closest to target_delta.
    target_delta: positive float (e.g. 0.40)
    """
    try:
        from bot.config.settings import Config
        from bot.config.instruments import get_instrument

        if symbol_name is None:
            symbol_name = Config.ACTIVE_SYMBOL

        instr = get_instrument(symbol_name)
        strike_diff = instr.strike_step

        expiry_date = parse_expiry(expiry)
        today = datetime.date.today()
        days_to_expiry = float((expiry_date - today).days)
        
        # If it is the expiry day, approximate remaining fractional day until close (15:30)
        if days_to_expiry <= 0:
            now = datetime.datetime.now()
            market_close = datetime.datetime.combine(today, datetime.time(15, 30))
            if now < market_close:
                remaining_seconds = (market_close - now).total_seconds()
                days_to_expiry = max(0.01, remaining_seconds / (3600.0 * 24.0))
            else:
                days_to_expiry = 0.001  # Small positive number for after-market queries

        atm_strike = round(spot / strike_diff) * strike_diff
        # Search a wide range of strikes (up to 16 strikes away) to find optimal delta
        range_pts = 16 * strike_diff
        bucket = token_lookup.get_option_bucket(symbol_name, expiry, atm_strike, range_points=range_pts)
        
        best_strike = None
        best_token = None
        best_symbol = None
        min_diff = float('inf')
        
        for key, info in bucket.items():
            if info["type"] != option_type:
                continue
            
            strike = info["strike"]
            delta = calculate_black_scholes_delta(spot, strike, days_to_expiry, vix, option_type)
            diff = abs(abs(delta) - target_delta)
            
            if diff < min_diff:
                min_diff = diff
                best_strike = strike
                best_token = info["token"]
                best_symbol = info["symbol"]
                
        if best_strike is None:
            logger.error(f"[Greeks] No matching option found in bucket for {symbol_name} {expiry} {option_type} target delta {target_delta}")
            return None, None, None
            
        logger.info(f"[Greeks] Selected strike {best_strike} for delta {target_delta:.2f} (est. delta: {calculate_black_scholes_delta(spot, best_strike, days_to_expiry, vix, option_type):.2f})")
        return best_strike, best_token, best_symbol
    except Exception as e:
        logger.error(f"[Greeks] Error in select_strike_by_delta: {e}")
        return None, None, None

