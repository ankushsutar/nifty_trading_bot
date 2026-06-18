"""
Short Strangle Strategy — weekly, Monday/Tuesday entry.
"""
from datetime import time

class ShortStrangleStrategy:
    def __init__(self, config: dict):
        self.config = config

    def is_entry_window(self, now_time: time, day_name: str, dte: int, vix: float) -> bool:
        entry_days = self.config["sc_entry_days"]
        start_str, end_str = self.config["sc_entry_window"]
        start_time = time(int(start_str.split(':')[0]), int(start_str.split(':')[1]))
        end_time = time(int(end_str.split(':')[0]), int(end_str.split(':')[1]))
        
        # Strangle usually preferred in lower VIX
        return (day_name in entry_days and 
                dte >= 3 and 
                start_time <= now_time <= end_time and
                vix <= 18.0)
