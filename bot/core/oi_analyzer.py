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
        self.rolling_snapshots = {} # Key = Token, Value = List of (timestamp, oi)

    def get_oi_roc(self, expiry, atm_strike, symbol="NIFTY", window_mins=5):
        """
        Calculates the Rate of Change (%) in Open Interest over the last X minutes.
        Triggers "Gamma Blast" if ROC exceeds thresholds (short covering).
        """
        try:
            from bot.config.instruments import get_instrument
            instr = get_instrument(symbol)
            
            # Use the existing market sentiment logic to fetch current OI for the ATM strike
            # For ROC, we focus specifically on the ATM/OTM strikes likely to be covered.
            token_ce, _ = self.token_lookup.get_token(instr.name, expiry, atm_strike, "CE", instrument_type=instr.trading_type, exchange=instr.exchange)
            token_pe, _ = self.token_lookup.get_token(instr.name, expiry, atm_strike, "PE", instrument_type=instr.trading_type, exchange=instr.exchange)
            
            if not token_ce or not token_pe:
                return 0.0
                
            batch_params = {instr.exchange: [token_ce, token_pe]}
            rate_limiter.wait()
            response = self.api.getMarketData("FULL", batch_params)
            
            if not response.get('status') or 'data' not in response:
                return 0.0

            fetched_data = response['data']['fetched']
            now = time.time()
            
            total_current_oi = 0
            for item in fetched_data:
                token = item['symbolToken']
                oi = item['opnInterest']
                total_current_oi += oi
                
                # Update rolling snapshots
                if token not in self.rolling_snapshots:
                    self.rolling_snapshots[token] = []
                self.rolling_snapshots[token].append((now, oi))
                
                # Prune old snapshots (keep 15 mins of data)
                self.rolling_snapshots[token] = [(t, v) for t, v in self.rolling_snapshots[token] if now - t < 900]

            # Calculate ROC
            # Formula: (Current OI - Past OI) / Past OI
            # A negative ROC (OI decreasing while price rises) = Short Covering.
            # Master Sheet specifies ROC in OI > 10% (as an absolute speed of exit).
            
            total_past_oi = 0
            for token in [token_ce, token_pe]:
                snaps = self.rolling_snapshots.get(token, [])
                if len(snaps) < 2:
                    continue
                
                # Find the snapshot closest to 'window_mins' ago
                target_time = now - (window_mins * 60)
                past_snap = snaps[0] # Default to oldest
                for t, v in snaps:
                    if t >= target_time:
                        past_snap = (t, v)
                        break
                total_past_oi += past_snap[1]

            if total_past_oi == 0:
                return 0.0
                
            roc = (total_current_oi - total_past_oi) / total_past_oi
            # We return absolute ROC because Master Sheet cares about the *speed* of exit/entry
            return abs(roc)

        except Exception as e:
            logger.error(f"OI ROC Calculation Error: {e}")
            return 0.0

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

    def get_market_sentiment(self, expiry, atm_strike, symbol="NIFTY"):
        """
        Analyzes OI sentiment using Batch Quote API.
        This replaces 10 historical fetches with 1 Quote fetch.
        """
        try:
            from bot.config.settings import Config
            from bot.config.instruments import get_instrument
            instr = get_instrument(symbol)
            
            # 1. Determine Strikes (5 above, 5 below)
            base_strike = round(atm_strike / instr.strike_step) * instr.strike_step
            strikes = [base_strike + (i * instr.strike_step) for i in range(-5, 6)]
            
            # 2. Map Strikes to Tokens
            tokens_to_fetch = []
            token_map = {} # token -> (strike, type)
            
            for strike in strikes:
                for opt_type in ['CE', 'PE']:
                    token, s = self.token_lookup.get_token(instr.name, expiry, strike, opt_type, instrument_type=instr.trading_type, exchange=instr.exchange)
                    if token:
                        tokens_to_fetch.append(token)
                        token_map[token] = {"strike": strike, "type": opt_type, "symbol": s}

            if not tokens_to_fetch:
                return {"bias": "NEUTRAL", "pcr": 1.0, "delta_ratio": 1.0}

            # 3. Batch Fetch Current Quotes (OI + LTP)
            batch_params = {instr.exchange: tokens_to_fetch}
            
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
