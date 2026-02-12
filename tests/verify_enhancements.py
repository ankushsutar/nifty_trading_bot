from bot.core.decision_engine import DecisionEngine
from bot.core.angel_connect import get_angel_session
from bot.utils.token_lookup import TokenLookup
import json

def verify_brain():
    api = get_angel_session()
    if not api:
        print("Failed to connect")
        return
        
    loader = TokenLookup()
    loader.load_scrip_master()
    
    engine = DecisionEngine(api, loader, dry_run=True)
    
    print("\n--- Decision Engine Verification ---")
    strategy = engine.analyze_and_select()
    
    print(f"\n[Result] Final Strategy Selected: {strategy}")
    
    # Check Regime explicitly
    from bot.core.data_fetcher import DataFetcher
    from bot.core.regime_classifier import RegimeClassifier
    
    fetcher = DataFetcher(api)
    classifier = RegimeClassifier()
    
    df = fetcher.fetch_latest_candles("99926000")
    if df is not None:
        regime = classifier.classify(df)
        print("\n--- Regime Metadata ---")
        print(json.dumps(regime, indent=2))

if __name__ == "__main__":
    verify_brain()
