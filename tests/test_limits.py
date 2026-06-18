from bot.core.safety_checks import SafetyGatekeeper
from bot.core.decision_engine import DecisionEngine
from bot.core.mock_connect import MockSmartConnect, MockTokenLookup
from bot.core.trade_repo import trade_repo

if __name__ == "__main__":
    # Setup mock data for PAPER mode
    trade_repo.save_trade("NIFTY", "1234", "CE", 50, 100, 90, side="BUY", mode="PAPER")
    trade_repo.save_trade("NIFTY", "5678", "PE", 50, 100, 90, side="BUY", mode="PAPER")

    # test LIVE mode
    api = MockSmartConnect()
    gatekeeper = SafetyGatekeeper(api, dry_run=False) # LIVE
    engine = DecisionEngine(api, MockTokenLookup(), dry_run=False)

    print("--- LIVE MODE TESTS ---")
    pnl_live = gatekeeper.get_daily_realized_pnl()
    print(f"Daily Realized PnL (LIVE): {pnl_live}")
    print(f"Max Daily Loss Check (LIVE): {gatekeeper.check_max_daily_loss(0.0)}")

    # test SIMULATION mode 
    gatekeeper_sim = SafetyGatekeeper(api, dry_run=True) # PAPER
    engine_sim = DecisionEngine(api, MockTokenLookup(), dry_run=True)

    print("--- PAPER MODE TESTS ---")
    pnl_paper = gatekeeper_sim.get_daily_realized_pnl()
    print(f"Daily Realized PnL (PAPER): {pnl_paper}")
    print(f"Max Daily Loss Check (PAPER): {gatekeeper_sim.check_max_daily_loss(0.0)}")


