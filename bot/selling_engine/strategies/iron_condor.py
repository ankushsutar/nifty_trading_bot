"""
Iron Condor Strategy — weekly, Monday entry.
"""
from datetime import time

class IronCondorStrategy:
    def __init__(self, config: dict):
        self.config = config

    def is_entry_window(self, now_time: time, day_name: str, dte: int) -> bool:
        entry_day = self.config["ic_entry_day"]
        start_str, end_str = self.config["ic_entry_window"]
        start_time = time(int(start_str.split(':')[0]), int(start_str.split(':')[1]))
        end_time = time(int(end_str.split(':')[0]), int(end_str.split(':')[1]))
        
        return (day_name == entry_day and 
                dte >= 4 and 
                start_time <= now_time <= end_time)
