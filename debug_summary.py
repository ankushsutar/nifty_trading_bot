from backend.bot_manager import bot_manager
import json

# Force mode to PAPER if needed
bot_manager.current_mode = "PAPER"
summary = bot_manager.get_daily_summary()
print(json.dumps(summary, indent=2))
