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
    datetime.date(2026, 11, 10),  # Diwali-Balipratipada
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


def get_next_weekly_expiry(symbol_name=None, date=None):
    """
    Returns the next weekly expiry for the active or given symbol as 'DDMMMYYYY' (e.g. '06JAN2026').
    
    Checks live/cached Scrip Master first if available for actual exchange-listed expiry contracts.
    Falls back to holiday calendar calculations.
    """
    import os
    from bot.config.settings import Config
    from bot.config.instruments import get_instrument

    if symbol_name is None:
        symbol_name = Config.ACTIVE_SYMBOL

    today = date if date is not None else datetime.date.today()

    # Dynamic Scrip Master Check:
    # If scrip master is loaded/cached on disk and date is near today,
    # find the earliest active option expiry >= today.
    try:
        from bot.utils.token_lookup import TokenLookup
        tl = TokenLookup()
        cache_path = tl._get_cache_path()
        if tl.df is not None or os.path.exists(cache_path):
            if tl.df is None:
                tl.load_scrip_master()
            if tl.df is not None and not tl.df.empty:
                instr = get_instrument(symbol_name)
                inst_type = instr.instrument_type if instr else 'OPTIDX'
                sub = tl.df[(tl.df['name'] == symbol_name.upper()) & (tl.df['instrumenttype'] == inst_type)]
                if not sub.empty:
                    def _parse(val):
                        try:
                            return datetime.datetime.strptime(str(val), "%d%b%Y").date()
                        except:
                            return None
                    exp_dates = sub['expiry'].apply(_parse).dropna()
                    valid_exps = exp_dates[exp_dates >= today].sort_values()
                    if not valid_exps.empty:
                        earliest = valid_exps.iloc[0]
                        # Only return if within next 10 days (true weekly contract)
                        if (earliest - today).days <= 10:
                            return _format_expiry(earliest)
    except Exception:
        pass

    instr = get_instrument(symbol_name)
    target_weekday = instr.expiry_day  # 1=Tue, 2=Wed, etc.

    days_ahead = (target_weekday - today.weekday()) % 7
    next_expiry = today + datetime.timedelta(days=days_ahead)

    # Walk backwards until we land on a valid trading day.
    while not is_trading_day(next_expiry):
        next_expiry -= datetime.timedelta(days=1)

    return _format_expiry(next_expiry)
