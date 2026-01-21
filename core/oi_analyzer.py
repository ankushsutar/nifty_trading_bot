import logging
import time

class OIAnalyzer:
    def __init__(self, api, token_loader):
        self.api = api
        self.loader = token_loader
        
    def get_pcr(self, expiry, atm_strike):
        """
        Calculates Put-Call Ratio (PCR) based on Open Interest (OI) 
        of 5 Strikes around ATM (ATM, +1, +2, -1, -2).
        
        PCR = Total Put OI / Total Call OI
        PCR > 1.0 => Bullish (More Puts writen/sold -> Support)
        PCR < 1.0 => Bearish (More Calls written/sold -> Resistance)
        """
        print(f">>> [AI] Scanning Option Chain (Live Volume/OI) for {expiry} around {atm_strike}...")
        
        strikes = [atm_strike, atm_strike+50, atm_strike+100, atm_strike-50, atm_strike-100]
        
        total_ce_oi = 0
        total_pe_oi = 0
        
        for strike in strikes:
            # 1. Get Tokens
            ce_token, ce_symbol = self.loader.get_token("NIFTY", expiry, strike, "CE")
            pe_token, pe_symbol = self.loader.get_token("NIFTY", expiry, strike, "PE")
            
        for strike in strikes:
            # 1. Get Tokens
            ce_token, _ = self.loader.get_token("NIFTY", expiry, strike, "CE")
            pe_token, _ = self.loader.get_token("NIFTY", expiry, strike, "PE")
            
            if not ce_token or not pe_token:
                continue

            # 2. Fetch OI/Volume Data
            time.sleep(0.4) # Rate Limit Protection
            ce_vol = self.fetch_oi_value(ce_token)
            
            time.sleep(0.4) # Rate Limit Protection
            pe_vol = self.fetch_oi_value(pe_token)
            
            total_ce_oi += ce_vol
            total_pe_oi += pe_vol
            
            # Print row for user visibility
            print(f"    Strike: {strike} | CE Vol: {int(ce_vol):,} | PE Vol: {int(pe_vol):,}")

        print(f"    ------------------------------------------------")
        print(f"    [Aggregated] CE Vol: {int(total_ce_oi):,} | PE Vol: {int(total_pe_oi):,}")
        
        if total_ce_oi == 0: return 1.0 # Avoid DivByZero
        
        pcr = total_pe_oi / total_ce_oi
        return round(pcr, 2)

    def fetch_oi_value(self, token):
        try:
            # REAL API LOGIC:
            # Fetch Daily Candle to get the latest Open Interest (OI)
            import datetime
            today = datetime.datetime.now()
            # Look back 3 days to ensure we get at least one candle even after weekends
            from_date = (today - datetime.timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
            to_date = today.strftime("%Y-%m-%d %H:%M")
            
            historicParam = {
                "exchange": "NFO",
                "symboltoken": token,
                "interval": "ONE_DAY",
                "fromdate": from_date,
                "todate": to_date
            }
            
            data = self.api.getCandleData(historicParam)
            
            # Response format: [timestamp, open, high, low, close, volume]
            if data and data.get('data'):
                latest = data['data'][-1]
                
                # Check for OI (Index 6) first
                if len(latest) > 6:
                     return float(latest[6])
                
                # Fallback: Use VOLUME (Index 5)
                # Volume PCR is a valid intraday sentiment indicator
                if len(latest) > 5:
                    return float(latest[5])
                    
                return 0
            
            return 0
            
        except Exception as e:
            # print(f">>> [Scan Error] {e}") 
            return 0

    def analyze_sentiment(self, pcr):
        """
        Returns: BULLISH, BEARISH, or NEUTRAL
        """
        if pcr > 1.2:
            return "BULLISH" # Strong Support
        elif pcr < 0.8:
            return "BEARISH" # Strong Resistance
        return "NEUTRAL"
