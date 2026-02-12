from backend.bot_manager import bot_manager
from backend.market_service import market_service
import json

# Force refresh
market_service.last_fetch_time = 0 
data = market_service.get_market_data()
print(f"INTERNAL MARKET DATA: {json.dumps(data, indent=2)}")

bot_manager.current_mode = "PAPER"
summary = bot_manager.get_daily_summary()
print(f"INTERNAL SUMMARY: {json.dumps(summary, indent=2)}")
