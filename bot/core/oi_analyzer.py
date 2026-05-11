import pandas as pd
import datetime
import time
import os
import json
from bot.utils.logger import logger
from bot.utils.rate_limiter import rate_limiter

class OIAnalyzer:
    def __init__(self, api, token_lookup):
        self.api = api
        self.token_lookup = token_lookup
        self.snapshot_file = os.path.join(os.getcwd(), "data", "oi_snapshot.json")
        self.history_file = os.path.join(os.getcwd(), "data", "oi_history.json")
        self._history = self._load_history()

    def _load_history(self):
        """Loads persistence history from disk."""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r") as f:
                    data = json.load(f)
                    today = datetime.date.today().isoformat()
                    if data.get("date") == today:
                        hist = data.get("history", [])
                        # Prune already-expired data (>10 mins old) right on load
                        now = time.time()
                        return [x for x in hist if now - x.get("time", 0) <= 600]
            except Exception as e:
                logger.debug(f"OI History load failed: {e}")
        return []

    def _save_history(self):
        """Persists the current telemetry history to disk."""
        try:
            today = datetime.date.today().isoformat()
            data = {"date": today, "history": self._history}
            os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
            with open(self.history_file, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"Failed to save OI history: {e}")

    def _get_oi_snapshot(self):
        """Loads today's 09:15 OI snapshot."""
        today = datetime.date.today().isoformat()
        if os.path.exists(self.snapshot_file):
            try:
                with open(self.snapshot_file, "r") as f:
                    data = json.load(f)
                    if data.get("date") == today:
                        return data.get("snapshots", {})
            except: pass
        return {}

    def _save_oi_snapshot(self, snapshots):
        """Saves today's 09:15 OI snapshot."""
        today = datetime.date.today().isoformat()
        data = {"date": today, "snapshots": snapshots}
        os.makedirs(os.path.dirname(self.snapshot_file), exist_ok=True)
        with open(self.snapshot_file, "w") as f:
            json.dump(data, f)

    def get_market_sentiment(self, expiry, atm_strike):
        """
        Analyzes OI sentiment using Batch Quote API.
        This replaces 10 historical fetches with 1 Quote fetch.
        """
        try:
            # 1. Determine Strikes (5 above, 5 below)
            base_strike = round(atm_strike / 50) * 50
            strikes = [base_strike + (i * 50) for i in range(-5, 6)]
            
            # 2. Map Strikes to Tokens
            tokens_to_fetch = []
            token_map = {} # token -> (strike, type)
            
            for strike in strikes:
                for opt_type in ['CE', 'PE']:
                    token, symbol = self.token_lookup.get_token("NIFTY", expiry, strike, opt_type)
                    if token:
                        tokens_to_fetch.append(token)
                        token_map[token] = {"strike": strike, "type": opt_type, "symbol": symbol}

            if not tokens_to_fetch:
                return {"bias": "NEUTRAL", "pcr": 1.0, "delta_ratio": 1.0}

            # 3. Batch Fetch Current Quotes (OI + LTP)
            batch_params = {"NFO": tokens_to_fetch}
            
            rate_limiter.wait()
            response = self.api.getMarketData("FULL", batch_params)
            
            if not response.get('status') or 'data' not in response:
                logger.error(f"OI Batch Fetch Failed: {response}")
                return {"bias": "NEUTRAL", "pcr": 1.0, "delta_ratio": 1.0}

            fetched_data = response['data']['fetched']
            
            # 4. Handle OI Snapshots for Intraday Change
            snapshots = self._get_oi_snapshot()
            if not snapshots:
                logger.info(">>> [System] Creating Daily OI Snapshot... 📸")
                for item in fetched_data:
                    # FIX Issue 3: Skip tokens with OI=0 — Angel One has ~5min lag at open
                    if item['opnInterest'] > 0:
                        snapshots[item['symbolToken']] = item['opnInterest']
                self._save_oi_snapshot(snapshots)

            # 5. Calculate Sentiment
            total_ce_oi = 0
            total_pe_oi = 0
            total_ce_delta = 0
            total_pe_delta = 0
            
            for item in fetched_data:
                token = item['symbolToken']
                current_oi = item['opnInterest']
                prev_oi = snapshots.get(token, current_oi)
                
                delta_oi = current_oi - prev_oi
                opt_info = token_map.get(token)
                # FIX Issue 2: Guard against None — API may return tokens not in our map
                if not opt_info:
                    continue

                if opt_info['type'] == 'CE':
                    total_ce_oi += current_oi
                    total_ce_delta += delta_oi
                else:
                    total_pe_oi += current_oi
                    total_pe_delta += delta_oi

            # Calculations
            pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0
            # FIX Issue 1: Use abs() — negative CE delta is bearish, not a fallback to 1.0
            delta_ratio = abs(total_pe_delta) / abs(total_ce_delta) if total_ce_delta != 0 else 1.0

            # FIX Issue 4: Tightened thresholds — 1.2/0.8 was too wide, rarely triggered
            # Normal Nifty PCR range: 0.85–1.15. These thresholds now fire on real signals.
            bias = "NEUTRAL"
            if pcr > 1.1 or delta_ratio > 1.2:
                bias = "BULLISH"
            elif pcr < 0.9 or delta_ratio < 0.8:
                bias = "BEARISH"
                
            logger.info(f">>> [Sentiment] PCR: {round(pcr, 2)} | Delta Ratio: {round(delta_ratio, 2)} | Bias: {bias}")
            
            return {
                "bias": bias,
                "pcr": round(pcr, 2),
                "delta_ratio": round(delta_ratio, 2),
                "total_ce_oi": total_ce_oi,
                "total_pe_oi": total_pe_oi
            }

        except Exception as e:
            logger.error(f"OI Analysis Error: {e}")
            return {"bias": "NEUTRAL", "pcr": 1.0, "delta_ratio": 1.0}

    def get_oi_velocity(self, expiry, atm_strike):
        """
        Calculates the Rate of Change (ROC) of the Put-Call Ratio (PCR).
        This detects rapid institutional position unwinding (Short Squeezes).
        """
        current_data = self.get_market_sentiment(expiry, atm_strike)
        current_time = time.time()
        
        self._history.append({"time": current_time, "pcr": current_data["pcr"]})
        
        # Keep only last 5 minutes (300 seconds) for calculation window
        self._history = [x for x in self._history if current_time - x["time"] <= 300]
        
        # Save updated state to disk to solve subprocess restart blindness
        self._save_history()
        
        pcr_velocity = 0.0
        if len(self._history) >= 2:
            oldest_pcr = self._history[0]["pcr"]
            newest_pcr = self._history[-1]["pcr"]
            # Velocity = change in PCR over the window. Positive means more bullish.
            pcr_velocity = newest_pcr - oldest_pcr
            
        current_data["pcr_velocity"] = round(pcr_velocity, 4)
        return current_data

