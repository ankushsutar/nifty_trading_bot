from dataclasses import dataclass
from typing import Dict

@dataclass
class Instrument:
    name: str              # e.g., NIFTY, BANKNIFTY, CRUDEOIL
    analysis_token: str    # The token used for price analysis (Spot/Index)
    lot_size: int          # Standard lot size
    strike_step: int       # Gap between strike prices
    asset_type: str        # INDEX, COMMODITY, EQUITY
    instrument_type: str   # OPTIDX, FUTCOM, etc. (Angel One constant)
    exchange: str          # NSE, MCX, etc.
    market_start: str = "09:15"
    market_end: str = "15:30"
    expiry_day: int = 1    # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri (weekly only)
    expiry_type: str = "WEEKLY"   # "WEEKLY" or "MONTHLY"
    expiry_day_of_month: int = 20  # For MONTHLY: day of month (e.g. 20 for MCX)

# Registry for supported instruments
# Expiry Days as of 2026: Nifty (Tue), BankNifty (Wed), Midcap (Mon), FinNifty (Tue)
INSTRUMENTS: Dict[str, Instrument] = {
    "NIFTY": Instrument(
        name="NIFTY",
        analysis_token="99926000",
        lot_size=65,
        strike_step=50,
        asset_type="INDEX",
        instrument_type="OPTIDX",
        exchange="NSE",
        market_start="09:15",
        market_end="15:30",
        expiry_day=1 # Tuesday
    ),
    "BANKNIFTY": Instrument(
        name="BANKNIFTY",
        analysis_token="99926009",
        lot_size=30,
        strike_step=100,
        asset_type="INDEX",
        instrument_type="OPTIDX",
        exchange="NSE",
        market_start="09:15",
        market_end="15:30",
        expiry_day=2 # Wednesday
    ),
    "CRUDEOIL": Instrument(
        name="CRUDEOIL",
        # NOTE: MCX futures tokens are contract-specific. Update this token
        # each month after the front-month contract rolls (typically ~20th).
        # Token format: MCX CRUDEOIL <DD><MON><YY>FUT — fetch fresh from scrip master.
        analysis_token="486502", # CRUDEOIL20APR26FUT — update monthly
        lot_size=100,
        strike_step=50,
        asset_type="COMMODITY",
        instrument_type="FUTCOM",  # Futures contract, not options
        exchange="MCX",
        market_start="09:00",
        market_end="23:00",  # Stop before illiquid late-night session
        expiry_type="MONTHLY",
        expiry_day_of_month=20,  # MCX CRUDEOIL expires on 20th of delivery month
    ),
    "GOLD": Instrument(
        name="GOLD",
        # NOTE: MCX futures tokens are contract-specific. Update each month.
        analysis_token="459277", # GOLD05JUN26FUT — update monthly
        lot_size=100,
        strike_step=100,
        asset_type="COMMODITY",
        instrument_type="FUTCOM",  # Futures contract, not options
        exchange="MCX",
        market_start="09:00",
        market_end="23:00",  # Stop before illiquid late-night session
        expiry_type="MONTHLY",
        expiry_day_of_month=5,   # MCX GOLD expires around 5th of delivery month
    )
}

def get_instrument(name: str) -> Instrument:
    return INSTRUMENTS.get(name.upper(), INSTRUMENTS["NIFTY"])
