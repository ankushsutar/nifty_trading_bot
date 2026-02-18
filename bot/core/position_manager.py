# [DEPRECATED — DO NOT USE]
# This module has been retired. All position monitoring is handled by
# individual strategy monitor loops using OrderManager + DataFetcher.
#
# Reason for removal:
#   - get_ltp() passed literal string "token_lookup" to Angel One API (always fails)
#   - TSL logic duplicates what each strategy already implements
#   - No rate limiting on the 0.5s poll loop
#
# If you need TSL logic, see: bot/strategies/momentum_strategy.py (trailing SL implementation)
raise ImportError(
    "PositionManager is deprecated and has been removed. "
    "Use OrderManager + strategy monitor loops instead."
)
