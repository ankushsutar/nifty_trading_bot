from core.safety_checks import SafetyGatekeeper
from backend.market_service import market_service
import datetime


class DecisionEngine:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.dry_run = dry_run
        self.loader = token_loader
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

        # 3. Market Regime Analysis
        print(">>> [Brain] 📊 Fetching Market Data from Service Layer...")
        market_data = market_service.get_market_data()
        regime_data = market_data.get('analysis', {})
        regime = regime_data.get('regime', 'UNKNOWN')
        trend = regime_data.get('trend', 'NEUTRAL')
        
        print(f">>> [Brain] Detected Regime: {regime} | Trend: {trend} | ADX: {regime_data.get('adx', 0)}")

        # 4. Sentiment Analysis (OI/PCR)
        sentiment = market_data.get('oi_data', {})
        bias = sentiment.get('bias', 'NEUTRAL')
        print(f">>> [Brain] Option Chain Bias: {bias} (PCR: {sentiment.get('pcr', 0)})")

        # 5. Smart Selection Matrix
        
        # RULE: If Volatile, STAY CASH
        if regime == "VOLATILE":
            print(">>> [Brain] ⚠️ Market is VOLATILE. Staying in CASH to avoid whipsaws.")
            return None

        # Scenario: Trending Market
        if regime == "TRENDING":
            adx = regime_data.get('adx', 0)
            
            # A. High Momentum (Super Trend) -> Reactive EMA Crossover
            if adx > 30:
                print(f">>> [Brain] ⚡ Strong Trend (ADX: {adx:.1f}). Selected: Momentum (Reactive Mode)")
                return "MOMENTUM"

            # B. Early Morning (09:30 - 10:00) -> Range Breakouts
            if datetime.time(9, 30) <= now < datetime.time(10, 0):
                print(">>> [Brain] 🚀 Early Trend detected. Selected: ORB (Range Breakout)")
                return "ORB"
            
            # C. Post-Stability (10:00+) -> Institutional VWAP
            elif now >= datetime.time(10, 0):
                print(">>> [Brain] 🏛️ Institutional Trend confirmed. Selected: VWAP (Institutional Mode)")
                return "VWAP"
            
            # Default fallback for trend
            return "MOMENTUM"

        # Scenario: Rangebound / Sideways Market
        if regime in ["SIDEWAYS", "CHOP"]:
            if funds_for_straddle and bias == "NEUTRAL":
                print(">>> [Brain] 💠 Rangebound Market + Neutral OI. Selected: Straddle (Premium Capture)")
                return "STRADDLE"
            elif bias != "NEUTRAL":
                print(f">>> [Brain] 🎯 Rangebound but OI has {bias} bias. Selected: Inside Bar Scalp")
                return "INSIDE_BAR"
            else:
                # If neutral but low funds, or just want a single-leg trade
                print(">>> [Brain] 🕯️ Sideways. Selected: Inside Bar (Limited Risk)")
                return "INSIDE_BAR"

        # Fallback
        return "MOMENTUM"
