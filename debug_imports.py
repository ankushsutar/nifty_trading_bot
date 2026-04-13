import sys
import os

try:
    import bot
    print(f"bot location: {bot.__file__}")
except ImportError:
    print("bot not found")

try:
    from bot import config
    print(f"bot.config location: {config.__file__}")
except ImportError:
    print("bot.config not found")

try:
    from bot.config import settings
    print(f"bot.config.settings location: {settings.__file__}")
except ImportError:
    print("bot.config.settings not found")

try:
    from bot.config import instruments
    print(f"bot.config.instruments location: {instruments.__file__}")
except ImportError:
    print("bot.config.instruments not found")
