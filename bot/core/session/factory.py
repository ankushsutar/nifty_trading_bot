from bot.config.settings import Config
from bot.core.session.angel import AngelSessionProvider
from bot.core.session.kite import KiteSessionProvider

def get_session(force_refresh: bool = False):
    """Factory resolving session based on configured broker toggle."""
    if Config.BROKER == "ZERODHA":
        provider = KiteSessionProvider()
    else:
        provider = AngelSessionProvider()
    return provider.get_session(force_refresh)
