import time
import datetime
import os
import json
from typing import Dict, List, Optional
from bot.utils.logger import logger
from bot.core.oi_analyzer import OIAnalyzer

class AlphaEngine:
    """
    The "X-Factor" Engine.
    Tracks Institutional Footprint by monitoring:
    1. OI Velocity (Rate of Change in Open Interest)
    2. PCR Momentum (Speed of Sentiment Shifts)
    3. Institutional Trap Zones (Short Covering / Long Unwinding)
    """

    def __init__(self, api, token_loader):
        self.oi_analyzer = OIAnalyzer(api, token_loader)
        self.history_file = os.path.join(os.getcwd(), "data", "alpha_history.json")
        self.history = self._load_history()
        self.MAX_HISTORY = 20 # Keep 20 snapshots (approx 60 mins if 3-min polling)

    def _load_history(self):
        """Loads the historical snapshots from disk to persist context across restarts."""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r") as f:
                    data = json.load(f)
                    today = datetime.date.today().isoformat()
                    if data.get("date") == today:
                        hist = data.get("history", {})
                        # Drop completely expired snapshots
                        now = time.time()
                        clean_hist = {}
                        for key, points in hist.items():
                            # Maximum persistence window for Alpha is 2 hours (7200 seconds)
                            clean_pts = [p for p in points if now - p.get('ts', 0) <= 7200]
                            if clean_pts:
                                clean_hist[key] = clean_pts
                        return clean_hist
            except Exception as e:
                logger.debug(f"AlphaEngine History Load Failed: {e}")
        return {}

    def _save_history(self):
        """Writes current telemetry snapshots to persistent disk store."""
        try:
            today = datetime.date.today().isoformat()
            data = {"date": today, "history": self.history}
            os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
            with open(self.history_file, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"AlphaEngine History Write Error: {e}")

    def analyze_panic(self, expiry: str, atm_strike: int) -> Dict:
        """
        Calculates the "Panic Score" (0-100) based on OI unwinding.
        - High Score (> 75): Extreme Institutional Panic (Short Covering / Long Unwinding)
        - Mid Score (40-60): Normal Trending
        - Low Score (< 30): Stagnant / Retail Only
        """
        try:
            current_oi = self.oi_analyzer.get_oi_velocity(expiry, atm_strike)
            timestamp = time.time()
            
            # Store history
            key = f"{expiry}_{atm_strike}"
            if key not in self.history:
                self.history[key] = []
            
            self.history[key].append({
                'ts': timestamp,
                'data': current_oi
            })
            
            # Keep history lean
            if len(self.history[key]) > self.MAX_HISTORY:
                self.history[key].pop(0)

            # Save to solve subprocess blindness
            self._save_history()

            # Calculate Velocity (ROC)
            if len(self.history[key]) < 2:
                return {"panic_score": 50, "confidence": "NEUTRAL", "reason": "Gathering Data"}

            prev = self.history[key][-2]['data']
            curr = self.history[key][-1]['data']
            
            call_oi_change = (curr['total_ce_oi'] - prev['total_ce_oi'])
            put_oi_change = (curr['total_pe_oi'] - prev['total_pe_oi'])
            
            # ── ALPHA LOGIC: SHORT COVERING DETECTION ─────────────────────
            # If Call OI is DROPPING while price is RISING = Institutional Panic.
            # This is the "X-Factor" move.
            panic_score = 50
            reason = "Market Normal"
            
            # Normalize changes based on total OI
            call_roc = call_oi_change / max(1, prev['total_ce_oi'])
            put_roc = put_oi_change / max(1, prev['total_pe_oi'])
            
            if call_roc < -0.05: # >5% Call Unwinding in 3-5 mins
                panic_score += 30
                reason = "🔥 SHORT COVERING SQUEEZE"
            elif put_roc < -0.05: # >5% Put Unwinding
                panic_score += 30
                reason = "🔥 LONG UNWINDING PANIC"
                
            # PCR Momentum
            pcr_vel = curr.get('pcr_velocity', 0)
            if abs(pcr_vel) > 0.02:
                panic_score += 15
                
            panic_score = min(100, max(0, panic_score))
            
            confidence = "NEUTRAL"
            if panic_score >= 80: confidence = "A+"
            elif panic_score >= 65: confidence = "A"
            elif panic_score >= 40: confidence = "B"
            else: confidence = "C"

            logger.info(f">>> [Alpha] Panic Score: {panic_score} | Confidence: {confidence} | {reason}")
            
            return {
                "panic_score": panic_score,
                "confidence": confidence,
                "reason": reason,
                "call_roc": round(call_roc, 4),
                "put_roc": round(put_roc, 4),
                "pcr_velocity": round(pcr_vel, 4)
            }

        except Exception as e:
            logger.error(f"AlphaEngine Analysis Error: {e}")
            return {"panic_score": 50, "confidence": "NEUTRAL", "reason": "Error"}

    def get_confidence_multiplier(self, score_data: Dict) -> float:
        """Returns a lot-size multiplier based on institutional confidence."""
        conf = score_data.get('confidence', 'NEUTRAL')
        if conf == "A+": return 1.5   # Bet 50% more on perfect setups
        if conf == "A":  return 1.2   # Bet 20% more
        if conf == "B":  return 0.8   # Reduce size on low-conviction
        if conf == "C":  return 0.5   # Half size
        return 1.0
