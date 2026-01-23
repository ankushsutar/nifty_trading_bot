import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logger(name="TradingBot", log_dir="logs"):
    """
    Sets up a logger with both Console and File handlers.
    File logs are rotated (max 5MB, keep 5 backups).
    """
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if setup is called multiple times
    if logger.hasHandlers():
        return logger

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - [%(levelname)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 1. File Handler (Rotating)
    log_file = os.path.join(log_dir, "trading_bot.log")
    file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 2. Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger

# Create a default instance for easy import
logger = setup_logger()
