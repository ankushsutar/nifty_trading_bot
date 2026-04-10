
import sys
import os

# Minimal Mocking for Verification
class MockTier:
    def __init__(self, name, max_lots, margin_buffer_pct, min_capital_threshold):
        self.name = name
        self.max_lots = max_lots
        self.margin_buffer_pct = margin_buffer_pct
        self.min_capital_threshold = min_capital_threshold

def get_compounded_lots_test(capital, margin_per_lot, tier, multiplier=1.0):
    if capital < tier.min_capital_threshold:
        return 0

    base_lots = int(capital / (margin_per_lot * (1 + tier.margin_buffer_pct)))
    lots = int(base_lots * multiplier)

    if lots < 1:
        if capital >= margin_per_lot:
            lots = 1
        else:
            return 0

    if tier.max_lots > 0:
        lots = min(lots, tier.max_lots)

    return lots

def run_test():
    print("--- DYNAMIC SIZING TEST ---")
    
    # User Capital 45,000
    capital = 45000.0
    # Nifty Lot Size 65
    lot_size = 65
    # Option LTP 100
    ltp = 100.0
    margin_per_lot = ltp * lot_size # 6500
    
    # SMALL Tier Config (Refined Scaling)
    small_tier = MockTier("SMALL", max_lots=5, margin_buffer_pct=0.12, min_capital_threshold=8000)
    
    scenarios = [
        ("🛡️ Low Confidence (0.25x)", 0.25),
        ("🟡 Normal Signal (0.50x)", 0.50),
        ("🚀 High Confidence (1.00x)", 1.00),
    ]
    
    for name, mult in scenarios:
        lots = get_compounded_lots_test(capital, margin_per_lot, small_tier, multiplier=mult)
        print(f"{name:<30} | Multiplier: {mult}x -> Lots: {lots}")

if __name__ == "__main__":
    run_test()
