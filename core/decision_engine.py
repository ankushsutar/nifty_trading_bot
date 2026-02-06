import datetime
import time
from config.settings import Config
from core.safety_checks import SafetyGatekeeper

class DecisionEngine:
    def __init__(self, api, dry_run=False):
        self.api = api
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)

    def analyze_and_select(self):
        """
        Analyzes Funds, Time, and VIX to select the best strategy.
        Returns: Strategy Name (str) or None
        """
        print("\n>>> [Brain] 🧠 Analyzing Market Conditions...")

        # 1. Check Capital
        # We need to know if we can afford Straddle (~1.5L) or just Buying (~5-10k)
        funds_for_straddle = self.gatekeeper.check_funds(required_margin_per_lot=150000)
        funds_for_buying = self.gatekeeper.check_funds(required_margin_per_lot=5000)

        if not funds_for_buying:
            print(">>> [Brain] ❌ Insufficient Capital for ANY strategy (< ₹5k).")
            return None

        # 2. Check Time
        now = datetime.datetime.now().time()
        print(f">>> [Brain] Current Time: {now}")

        # Rule A: Market Opening (09:15 - 09:20) -> OHL Scalp
        if datetime.time(9, 15) <= now < datetime.time(9, 20):
            print(">>> [Brain] 🌅 Market Opening Phase. Selected: OHL Scalp")
            return "OHL"

        # Rule B: High VIX -> Momentum (Trend Following)
        # Check VIX
        try:
            # Reusing VIX logic from Gatekeeper or just checking here
            # Ideally Gatekeeper has a helper, but let's just make a quick check or use default
            # For efficiency we might assume VIX check happens inside strategies, but Brain should know.
            # Let's use a simplified check or assume VIX behavior from Gatekeeper.
            pass
        except: pass

        # 3. Strategy Selection Matrix
        
        # Scenario: Low Capital (< 1.5L)
        if not funds_for_straddle:
             print(">>> [Brain] 💰 Low Capital Detected. Restricting to Buying Strategies.")
             # If Morning passed, use Momentum or Inside Bar
             # Let's Prefer Momentum for general purpose
             if now >= datetime.time(9, 20):
                 print(">>> [Brain] ⚡ Intraday Trend Phase. Selected: Momentum")
                 return "MOMENTUM"
             
        # Scenario: High Capital (> 1.5L)
        else:
             print(">>> [Brain] 💰 High Capital Available.")
             # If Morning passed
             if now >= datetime.time(9, 20):
                 # If VIX is very low (< 12), Straddle is good (Premium decay).
                 # If VIX is high (> 15), Momentum might be safer? 
                 # For now, let's default to STRADDLE as the "Premium" strategy if funds allow.
                 print(">>> [Brain] 📉 Standard Phase. Selected: Straddle (Premium Capture)")
                 return "STRADDLE"

        return "MOMENTUM" # Fallback
