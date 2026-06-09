# Backward-compatibility wrapper for session management.
# New code should import get_session from bot.core.session

from bot.core.session.factory import get_session

def get_angel_session(force_refresh=False):
    """Compatibility wrapper redirecting to get_session."""
    return get_session(force_refresh)

def get_kite_session(force_refresh=False):
    """Compatibility wrapper for Zerodha session."""
    from bot.core.session.kite import KiteSessionProvider
    return KiteSessionProvider().get_session(force_refresh)

def get_real_angel_session(force_refresh=False):
    """Compatibility wrapper for legacy Angel session."""
    from bot.core.session.angel import AngelSessionProvider
    return AngelSessionProvider().get_session(force_refresh)

def patch_sdk():
    """Compatibility wrapper for SDK patching."""
    from bot.core.session.angel import patch_sdk as real_patch
    real_patch()
