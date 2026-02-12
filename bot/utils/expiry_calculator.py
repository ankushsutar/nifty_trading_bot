import datetime

def get_next_weekly_expiry():
    today = datetime.date.today()
    # Nifty Expiry is now Tuesday (weekday 1) as of Sep 2025
    
    current_weekday = today.weekday()
    # Calculate days ahead for next Tuesday (1)
    # If today is Tuesday (1), days_ahead will be 0 (Target - Current)
    # If today is Wed (2), days_ahead = (1-2) % 7 = 6
    target_weekday = 1
    days_ahead = (target_weekday - current_weekday) % 7
    
    next_expiry = today + datetime.timedelta(days=days_ahead)
    
    # TODO: Handle NSE Holidays (shift to previous trading day if Tuesday is a holiday)
    
    # Format: DDMMMYYYY (e.g., 20JAN2026)
    return next_expiry.strftime("%d%b%Y").upper()
    
    # Format: DDMMMYYYY (e.g., 22JAN2026)
    return next_expiry.strftime("%d%b%Y").upper()

# Validation for 2026-01-20 (Tuesday)
# weekday = 1
# days_ahead = 3 - 1 = 2
# next_expiry = 20 + 2 = 22 Jan 2026 -> 22JAN2026. Correct.
