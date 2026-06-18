"""
Iron Fly Strategy — expiry day only (Tuesday), 9:20–9:45 AM entry.
"""
from datetime import time

class IronFlyStrategy:
    def __init__(self, config: dict):
        self.config = config

    def is_entry_window(self, now_time: time, day_name: str, dte: int) -> bool:
        entry_day = self.config["if_entry_day"]
        start_str, end_str = self.config["if_entry_window"]
        start_time = time(int(start_str.split(':')[0]), int(start_str.split(':')[1]))
        end_time = time(int(end_str.split(':')[0]), int(end_str.split(':')[1]))
        
        # Iron Fly is expiry specific
        return (day_name == entry_day and 
                dte <= 1 and 
                start_time <= now_time <= end_time)
