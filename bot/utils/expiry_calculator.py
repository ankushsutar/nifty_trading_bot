import datetime

# NSE Holidays 2026 — update annually from NSE circular
# Source: https://www.nseindia.com/resources/exchange-communication-holidays
NSE_HOLIDAYS_2026 = {
    datetime.date(2026, 1, 26),   # Republic Day
    datetime.date(2026, 3, 3),    # Holi
    datetime.date(2026, 3, 26),   # Shri Ram Navami
    datetime.date(2026, 3, 31),   # Shri Mahavir Jayanti
    datetime.date(2026, 4, 3),    # Good Friday
    datetime.date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    datetime.date(2026, 5, 1),    # Maharashtra Day
    datetime.date(2026, 5, 28),   # Bakra Eid
    datetime.date(2026, 6, 26),   # Muharram
    datetime.date(2026, 9, 14),   # Ganesh Chaturthi
    datetime.date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    datetime.date(2026, 10, 20),  # Dussehra
    datetime.date(2026, 11, 9),   # Diwali-Balipratipada
    datetime.date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
    datetime.date(2026, 12, 25),  # Christmas
}

# Locale-safe month abbreviations (strftime %b is locale-dependent on Windows).
# Angel One scrip master uses uppercase English abbreviations — always match this exactly.
_MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR",
    5: "MAY", 6: "JUN", 7: "JUL", 8: "AUG",
    9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}


def _format_expiry(date: datetime.date) -> str:
    """
    Formats a date as 'DDMMMYYYY' in a locale-safe way.
    e.g. datetime.date(2026, 1, 6) → '06JAN2026'

    strftime('%b') is locale-dependent and can return regional month names
    on some Indian Windows systems — this function always returns English.
    """
    return f"{date.day:02d}{_MONTH_ABBR[date.month]}{date.year}"


def is_trading_day(date=None):
    """
    Returns True if the given date is a valid NSE trading day.
    Excludes weekends (Sat/Sun) and NSE holidays.
    Defaults to today if no date is provided.
    """
    if date is None:
        date = datetime.date.today()
    if date.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    if date in NSE_HOLIDAYS_2026:
        return False
    return True


def get_next_weekly_expiry(symbol_name=None):
    """
    Returns the next weekly (or active front-month) expiry for the active or given symbol as 'DDMMMYYYY' (e.g. '06JAN2026').
    
    If the symbol is a COMMODITY (or monthly) asset, it queries the Scrip Master to find
    the actual nearest unexpired option expiry date.
    Otherwise (for index options), it rolls to the next weekly target day.
    """
    from bot.config.settings import Config
    from bot.config.instruments import get_instrument

    if symbol_name is None:
        symbol_name = Config.ACTIVE_SYMBOL

    instr = get_instrument(symbol_name)
    
    # COMMODITY / MONTHLY assets logic: find nearest option expiry in scrip master
    if instr.asset_type == "COMMODITY" or instr.expiry_type == "MONTHLY":
        try:
            from bot.utils.token_lookup import TokenLookup
            tl = TokenLookup()
            tl.load_scrip_master()
            if tl.df is not None and not tl.df.empty:
                # Find all unexpired option contracts for this name
                options_df = tl.df[
                    (tl.df['name'] == symbol_name.upper()) &
                    (tl.df['instrumenttype'].isin(['CE', 'PE', 'OPTFUT', 'OPTIDX', 'OPTSTK']))
                ].copy()
                
                if not options_df.empty:
                    today = datetime.date.today()
                    def parse_exp(exp_str):
                        try:
                            return datetime.datetime.strptime(exp_str, "%d%b%Y").date()
                        except:
                            return None
                    options_df['expiry_dt'] = options_df['expiry'].apply(parse_exp)
                    # Filter for >= today
                    options_df = options_df[options_df['expiry_dt'] >= today]
                    if not options_df.empty:
                        sorted_expiries = sorted(options_df['expiry_dt'].dropna().unique())
                        if sorted_expiries:
                            next_expiry = sorted_expiries[0]
                            return next_expiry.strftime("%d%b%Y").upper()
        except Exception as e:
            # Fallback to standard logic if scrip master lookup fails
            pass

    # Standard Index Option Weekly logic
    target_weekday = instr.expiry_day  # 1=Tue, 2=Wed, etc.

    today = datetime.date.today()
    days_ahead = (target_weekday - today.weekday()) % 7
    next_expiry = today + datetime.timedelta(days=days_ahead)

    # Walk backwards until we land on a valid trading day.
    while not is_trading_day(next_expiry):
        next_expiry -= datetime.timedelta(days=1)

    return _format_expiry(next_expiry)
