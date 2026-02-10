import logging
import time
import datetime
from utils.logger import logger

class OIAnalyzer:
    def __init__(self, api, token_loader):
        self.api = api
        self.loader = token_loader
        
    def get_market_sentiment(self, expiry, atm_strike):
        """
        Comprehensive Market Sentiment analysis using PCR and Delta OI.
        Returns: { 'pcr': float, 'sentiment': str, 'confidence': float, 'bias': str }
        """
        logger.info(f">>> [Analysis] 🔍 Scanning Option Chain Sentiment (ATM: {atm_strike})")
        
        # 5 Strikes around ATM
        strikes = [atm_strike - 100, atm_strike - 50, atm_strike, atm_strike + 50, atm_strike + 100]
        
        total_ce_oi = 0
        total_pe_oi = 0
        total_ce_delta = 0
        total_pe_delta = 0
        
        for strike in strikes:
            ce_token, ce_symbol = self.loader.get_token("NIFTY", expiry, strike, "CE")
            pe_token, pe_symbol = self.loader.get_token("NIFTY", expiry, strike, "PE")
            
            if not ce_token or not pe_token:
                continue

            # Fetch Intraday Data for Delta OI
            # Rate limit protection: angel usually allows 3 req/sec
            time.sleep(0.34) 
            ce_oi, ce_delta = self._fetch_oi_and_delta(ce_token)
            
            time.sleep(0.34)
            pe_oi, pe_delta = self._fetch_oi_and_delta(pe_token)
            
            total_ce_oi += ce_oi
            total_pe_oi += pe_oi
            total_ce_delta += ce_delta
            total_pe_delta += pe_delta
            
            logger.debug(f"    Strike {strike} | CE-OI: {ce_oi}, Δ: {ce_delta} | PE-OI: {pe_oi}, Δ: {pe_delta}")

        # 1. PCR Calculation
        pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0
        pcr = round(pcr, 2)
        
        # 2. Delta Sentiment (The "Pressure")
        # If PE Delta > CE Delta -> Put Writing is heavier -> Bullish Pressure
        delta_ratio = total_pe_delta / total_ce_delta if total_ce_delta > 0 else 1.0
        
        # 3. Overall Bias
        bias = "NEUTRAL"
        if pcr > 1.2 or delta_ratio > 1.5:
            bias = "BULLISH"
        elif pcr < 0.8 or delta_ratio < 0.6:
            bias = "BEARISH"
            
        logger.info(f">>> [Sentiment] PCR: {pcr} | DeltaPressure: {round(delta_ratio, 2)} | Bias: {bias}")
        
        return {
            "pcr": pcr,
            "delta_ratio": round(delta_ratio, 2),
            "bias": bias,
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi
        }

    def _fetch_oi_and_delta(self, token):
        """Fetches current OI and the change since the first intraday candle."""
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d 09:15")
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            
            param = {
                "exchange": "NFO",
                "symboltoken": token,
                "interval": "FIVE_MINUTE",
                "fromdate": today,
                "todate": now
            }
            
            data = self.api.getCandleData(param)
            if data and data.get('data') and len(data['data']) > 0:
                candles = data['data']
                # [timestamp, open, high, low, close, volume, oi]
                latest_oi = float(candles[-1][6]) if len(candles[-1]) > 6 else float(candles[-1][5])
                initial_oi = float(candles[0][6]) if len(candles[0]) > 6 else float(candles[0][5])
                
                delta_oi = latest_oi - initial_oi
                return latest_oi, delta_oi
            
            return 0, 0
        except Exception as e:
            return 0, 0
