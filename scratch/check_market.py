import sys
import os
import time

sys.path.append(os.getcwd())

# Force process type to BACKEND so analysis is allowed to run
os.environ["PROCESS_TYPE"] = "BACKEND"

from backend.market_service import market_service
from bot.core.levels_provider import levels_provider

print(">>> Initializing connection...")
market_service._ensure_connection()

if not market_service.api:
    print("Failed to connect!")
    sys.exit(1)

print(">>> Running Levels Provider...")
market_service.levels_data = levels_provider.get_levels() or {}

print(">>> Fetching candles...")
from bot.config.settings import Config
from bot.config.instruments import get_instrument
instr = get_instrument(Config.ACTIVE_SYMBOL)
df = market_service.data_fetcher.fetch_latest_candles(instr.analysis_token)

if df is not None:
    print(">>> Classifying Regime...")
    market_service.analysis_data = market_service.regime_engine.classify(df)
    
    # Calculate HOD/LOD
    import datetime
    import pandas as pd
    now_dt = datetime.datetime.now()
    if 'timestamp' in df.columns:
        dates = pd.to_datetime(df['timestamp']).dt.date
    else:
        dates = pd.Series(df.index).dt.date if not isinstance(df.index, pd.DatetimeIndex) else df.index.date
    mask = (dates == now_dt.date())
    today_df = df[mask] if not df.empty else None
    if today_df is not None and not today_df.empty:
        if len(today_df) > 1:
            ref_df = today_df.iloc[:-1]
        else:
            ref_df = today_df
        market_service.analysis_data['hod'] = float(ref_df['high'].max())
        market_service.analysis_data['lod'] = float(ref_df['low'].min())
    
    # Fetch OI
    ltp = df.iloc[-1]['close']
    base_atm = int(round(ltp / instr.strike_step) * instr.strike_step)
    from bot.utils.expiry_calculator import get_next_weekly_expiry
    expiry = get_next_weekly_expiry()
    
    from bot.core.oi_analyzer import OIAnalyzer
    market_service.oi_engine = OIAnalyzer(market_service.api, market_service.token_lookup)
    
    print(">>> Running OI Velocity...")
    analysis = market_service.oi_engine.get_oi_velocity(expiry, ltp)
    market_service.oi_data = analysis
    
    # Run heavyweights
    print(">>> Analyzing heavyweights...")
    from bot.core.heavyweight_tracker import heavyweight_tracker
    heavyweight_tracker.data_fetcher = market_service.data_fetcher
    hw_data = heavyweight_tracker.analyze_heavyweights()
    
    print("\n==========================================")
    print("📊 LIVE MARKET REPORT FOR TODAY")
    print("==========================================")
    print(f"Nifty Spot LTP: {ltp:.2f}")
    print(f"Daily Gap-Down: {((ltp - market_service.levels_data.get('pdc', ltp)) / market_service.levels_data.get('pdc', 1)) * 100:.2f}%")
    print(f"Regime Class: {market_service.analysis_data.get('regime')}")
    print(f"ADX Value: {market_service.analysis_data.get('adx'):.2f}")
    print(f"ADX Slope: {market_service.analysis_data.get('adx_slope'):.4f}")
    print(f"OI Bias: {analysis.get('bias')}")
    print(f"PCR ratio: {analysis.get('pcr'):.2f}")
    print(f"PCR Velocity: {analysis.get('pcr_velocity'):.4f}")
    print(f"Heavyweights State:")
    for hw in hw_data.get('details', []):
        print(f"  - {hw}")
    print("==========================================\n")
else:
    print("Failed to fetch candle data!")
