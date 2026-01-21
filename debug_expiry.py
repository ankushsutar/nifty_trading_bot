import datetime
from utils.expiry_calculator import get_next_weekly_expiry

print(f"Today: {datetime.date.today()}")
print(f"Weekday: {datetime.date.today().weekday()} (0=Mon, 1=Tue, ...)")
print(f"Calculated Expiry: {get_next_weekly_expiry()}")
