import datetime

def get_next_weekly_expiry():
    today = datetime.date.today()
    # Nifty Expiry is usually Thursday (weekday 3)
    # If today is Thursday, we need to check if market is closed (assume yes for simplicity, or check time)
    # For now, let's just say if today is Thursday, returns today.
    
    current_weekday = today.weekday()
    days_ahead = 3 - current_weekday
    if days_ahead < 0: # Today is Fri(4), Sat(5), Sun(6) -> next Thursday
        days_ahead += 7
        
    next_expiry = today + datetime.timedelta(days=days_ahead)
    
    # Format: DDMMMYYYY (e.g., 22JAN2026)
    return next_expiry.strftime("%d%b%Y").upper()

# Validation for 2026-01-20 (Tuesday)
# weekday = 1
# days_ahead = 3 - 1 = 2
# next_expiry = 20 + 2 = 22 Jan 2026 -> 22JAN2026. Correct.
