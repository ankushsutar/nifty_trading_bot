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
    datetime.date(2026, 5, 26),   # Bakra Eid
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


def get_next_weekly_expiry(target_weekday=1):
    """
    Returns the next weekly expiry for a given weekday (0=Mon, 1=Tue, ..., 4=Fri)
    as 'DDMMMYYYY' (e.g. '06JAN2026').

    Holiday handling: if the target day is an NSE holiday OR a weekend, walk
    backwards one day at a time until a valid trading day is found.
    """
    today = datetime.date.today()

    # Calculate days until the next target_weekday
    days_ahead = (target_weekday - today.weekday()) % 7

    # If today is the target_weekday, check if we should look for next week
    # (Typically if market is already closed, but here we just return today's expiry if valid)
    next_expiry = today + datetime.timedelta(days=days_ahead)

    # Walk backwards until we land on a valid trading day.
    while not is_trading_day(next_expiry):
        next_expiry -= datetime.timedelta(days=1)

    return _format_expiry(next_expiry)


def get_next_monthly_expiry(expiry_day_of_month: int = 20) -> str:
    """
    Returns the next MCX-style monthly expiry as 'DDMMMYYYY'.

    MCX CRUDEOIL and GOLD expire on the 20th of the delivery month
    (or the previous business day if the 20th is a holiday/weekend).
    If today is past the expiry day this month, the next month's expiry
    is returned.

    Args:
        expiry_day_of_month: Day of month the contract expires (default 20 for MCX energy).
    """
    today = datetime.date.today()

    # Try this month's expiry first
    candidate = datetime.date(today.year, today.month, expiry_day_of_month)

    # If we're already past this month's expiry, move to next month
    if today > candidate:
        if today.month == 12:
            candidate = datetime.date(today.year + 1, 1, expiry_day_of_month)
        else:
            candidate = datetime.date(today.year, today.month + 1, expiry_day_of_month)

    # Walk backwards if it falls on a weekend or holiday
    while candidate.weekday() >= 5 or candidate in NSE_HOLIDAYS_2026:
        candidate -= datetime.timedelta(days=1)

    return _format_expiry(candidate)
