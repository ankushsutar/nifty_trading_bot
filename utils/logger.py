import logging
import os
import queue
from logging.handlers import RotatingFileHandler, QueueHandler

# Global Queue for Logs (Thread-Safe)
log_queue = queue.Queue()

def setup_logger(name="TradingBot", log_dir="logs"):
    """
    Sets up a logger with Console, File, and Queue handlers.
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

    # 3. Queue Handler (For WebSockets)
    queue_handler = QueueHandler(log_queue)
    # queue_handler doesn't strictly need a formatter if we format at consumption, 
    # but let's see if we can attach one or handle it later. 
    # Actually QueueHandler simply puts the LogRecord into the queue.
    logger.addHandler(queue_handler)

    return logger

# Create a default instance for easy import
logger = setup_logger()
