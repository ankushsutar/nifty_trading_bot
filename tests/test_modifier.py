from bot.core.order_manager import OrderManager
from bot.core.mock_connect import MockSmartConnect
from bot.config.settings import Config

if __name__ == "__main__":
    Config.LIVE_TRADE_ENABLED = False  # force safety dry run
    api = MockSmartConnect()

    manager = OrderManager(api, dry_run=False) # dry_run=False, safety=False -> DRY_SAFETY mode

    print("Testing Smart Limit (Should use limit order directly):")
    oid = manager.place_smart_limit("NIFTY", "123", 50, 100)
    print(f"Result ID: {oid}")

    print("\nTesting Modifying Order Price:")
    ans = manager.modify_order_price(oid, 105, "NIFTY", "123", 50)
    print(f"Success: {ans}")

    print("\nTesting Cancelling Order:")
    ans = manager.cancel_order(oid)
    print(f"Success: {ans}")

    print("\nTesting Modifying SL Order:")
    ans = manager.modify_sl_order(oid, 95, "NIFTY", "123", 50)
    print(f"Success: {ans}")

