import sys
import os

# Standalone Risk Verification
class MockTier:
    def __init__(self, name, max_lots, margin_buffer_pct, min_capital_threshold, max_daily_loss_pct, sl_pct):
        self.name = name
        self.max_lots = max_lots
        self.margin_buffer_pct = margin_buffer_pct
        self.min_capital_threshold = min_capital_threshold
        self.max_daily_loss_pct = max_daily_loss_pct
        self.sl_pct = sl_pct

def get_compounded_lots_risk_test(capital, margin_per_lot, tier, multiplier=1.0):
    # Base affordability
    base_lots = int(capital / (margin_per_lot * (1 + tier.margin_buffer_pct)))
    lots = int(base_lots * multiplier)
    if lots < 1: lots = 1 if capital >= margin_per_lot else 0
    if tier.max_lots > 0: lots = min(lots, tier.max_lots)

    # Risk-based capping logic
    daily_loss_limit = capital * tier.max_daily_loss_pct
    risk_per_lot = margin_per_lot * tier.sl_pct
    
    if lots * risk_per_lot > daily_loss_limit:
        max_safe_lots = int(daily_loss_limit / risk_per_lot)
        print(f"   [RISK CAP] Reducing lots from {lots} to {max_safe_lots} (Daily Limit: {daily_loss_limit})")
        lots = max(1, max_safe_lots)
        
    return lots

def run_risk_test():
    print("--- RISK-BASED SIZING TEST ---")
    capital = 45000.0
    # SMALL Tier Config
    small_tier = MockTier("SMALL", max_lots=5, margin_buffer_pct=0.12, min_capital_threshold=8000, 
                          max_daily_loss_pct=0.12, sl_pct=0.20)
    
    # CASE 1: Normal Option (LTP 100)
    # Margin = 6500. Risk per lot = 1300. 5 lots risk = 6500. Limit = 5400.
    # Expected: Cap to int(5400/1300) = 4 lots (Risk = 5200)
    print("\nCase 1: Standard Option (LTP 100)")
    margin = 100 * 65
    lots = get_compounded_lots_risk_test(capital, margin, small_tier, multiplier=1.0)
    print(f"Final Lots: {lots}")

    # CASE 2: Expensive Option (LTP 200)
    # Margin = 13000. Risk per lot = 2600. Limit = 5400.
    # Expected: Cap to 2 lots (Risk = 5200)
    print("\nCase 2: Expensive Option (LTP 200)")
    margin = 200 * 65
    lots = get_compounded_lots_risk_test(capital, margin, small_tier, multiplier=1.0) 
    print(f"Final Lots: {lots}")

if __name__ == "__main__":
    run_risk_test()
