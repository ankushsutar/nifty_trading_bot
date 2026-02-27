from bot.core.decision_engine import DecisionEngine
from bot.core.mock_connect import MockSmartConnect, MockTokenLookup
from bot.core.trade_repo import trade_repo

# Setup mock data for PAPER mode
trade_repo.save_trade("NIFTY", "1234", "CE", 50, 100, 90, side="BUY", mode="PAPER")
trade_repo.save_trade("NIFTY", "5678", "PE", 50, 100, 90, side="BUY", mode="PAPER")

api = MockSmartConnect()

# test LIVE mode
engine = DecisionEngine(api, MockTokenLookup(), dry_run=False) # LIVE
print("LIVE MODE STRATEGY:", engine.analyze_and_select())

# test SIMULATION mode 
engine_sim = DecisionEngine(api, MockTokenLookup(), dry_run=True) # PAPER
print("PAPER MODE STRATEGY:", engine_sim.analyze_and_select())

