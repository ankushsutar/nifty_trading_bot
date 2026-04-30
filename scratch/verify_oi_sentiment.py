import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from bot.core.oi_analyzer import OIAnalyzer
from bot.utils.logger import logger
import json

class MockAPI:
    def getMarketData(self, mode, params):
        # Mocking data for 2 strikes (ATM and 1 strike OTM)
        # 1. CE Covering: OI decreasing (-100,000)
        # 2. PE Writing: OI increasing (+50,000)
        return {
            "status": True,
            "data": {
                "fetched": [
                    {"symbolToken": "T1", "opnInterest": 900000}, # CE (Was 1M) -> -100k
                    {"symbolToken": "T2", "opnInterest": 550000}, # PE (Was 500k) -> +50k
                ]
            }
        }

class MockTokenLookup:
    def get_token(self, symbol, expiry, strike, opt_type):
        if opt_type == 'CE': return "T1", "NIFTY_CE"
        return "T2", "NIFTY_PE"

def test_sentiment():
    api = MockAPI()
    lookup = MockTokenLookup()
    analyzer = OIAnalyzer(api, lookup)
    
    # Pre-set snapshots to simulate history
    # CE was 1,000,000
    # PE was 500,000
    analyzer._save_oi_snapshot({"T1": 1000000, "T2": 500000})
    
    sentiment = analyzer.get_market_sentiment("ANY", 24000)
    
    print("\n--- OI Sentiment Test Results ---")
    print(f"Bias: {sentiment['bias']}")
    print(f"PCR: {sentiment['pcr']}")
    print(f"Power Ratio: {sentiment['delta_ratio']}")
    print(f"Total CE OI: {sentiment['total_ce_oi']}")
    print(f"Total PE OI: {sentiment['total_pe_oi']}")
    
    # Expected: 
    # Bull Power = PE Writing (50k) + CE Covering (100k) = 150k
    # Bear Power = 0
    # Bias: BULLISH
    
    if sentiment['bias'] == "BULLISH":
        print("\n✅ SUCCESS: Logic correctly identified Bullish sentiment from Short Covering.")
    else:
        print("\n❌ FAILURE: Logic failed to identify Bullish sentiment.")

if __name__ == "__main__":
    test_sentiment()
