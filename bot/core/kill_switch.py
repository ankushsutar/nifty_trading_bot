import os
from bot.utils.logger import logger

LOCK_FILE = ".stop_signal"

def is_kill_switch_active():
    """Checks if the global kill switch is active (lock file exists)."""
    return os.path.exists(LOCK_FILE)

def activate_kill_switch():
    """Activates the kill switch by creating the lock file."""
    try:
        with open(LOCK_FILE, "w") as f:
            f.write("STOPPED")
        logger.critical("🛑 KILL SWITCH ACTIVATED: .stop_signal created.")
    except Exception as e:
        logger.error(f"Failed to activate kill switch: {e}")

def deactivate_kill_switch():
    """Deactivates the kill switch by removing the lock file."""
    if os.path.exists(LOCK_FILE):
        try:
            os.remove(LOCK_FILE)
            logger.info("✅ KILL SWITCH DEACTIVATED: .stop_signal removed.")
        except Exception as e:
            logger.error(f"Failed to deactivate kill switch: {e}")
