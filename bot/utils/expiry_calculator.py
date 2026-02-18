import datetime

# NSE Holidays 2026 — update annually from NSE circular
# Source: https://www.nseindia.com/resources/exchange-communication-holidays
NSE_HOLIDAYS_2026 = {
    datetime.date(2026, 1, 26),   # Republic Day
    datetime.date(2026, 2, 26),   # Mahashivratri
    datetime.date(2026, 3, 25),   # Holi
    datetime.date(2026, 4, 2),    # Ram Navami
    datetime.date(2026, 4, 3),    # Good Friday
    datetime.date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    datetime.date(2026, 5, 1),    # Maharashtra Day
    datetime.date(2026, 8, 15),   # Independence Day
    datetime.date(2026, 10, 2),   # Gandhi Jayanti
    datetime.date(2026, 10, 22),  # Dussehra
    datetime.date(2026, 11, 11),  # Diwali (Laxmi Pujan) — tentative
    datetime.date(2026, 11, 12),  # Diwali (Balipratipada) — tentative
    datetime.date(2026, 11, 25),  # Gurunanak Jayanti
    datetime.date(2026, 12, 25),  # Christmas
}


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


def get_next_weekly_expiry():
    """
    Returns the next Nifty weekly expiry date as 'DDMMMYYYY' (e.g. '20JAN2026').
    Nifty expiry is Tuesday (weekday=1) as of Sep 2025.
    If Tuesday is an NSE holiday, shifts to the previous Monday.
    """
    today = datetime.date.today()
    target_weekday = 1  # Tuesday
    days_ahead = (target_weekday - today.weekday()) % 7
    next_expiry = today + datetime.timedelta(days=days_ahead)

    # Holiday shift: if Tuesday is a holiday, use Monday
    if next_expiry in NSE_HOLIDAYS_2026:
        next_expiry -= datetime.timedelta(days=1)

    return next_expiry.strftime("%d%b%Y").upper()
