"""
bot/core/metrics_exporter.py — Real-Time Observability Layer

Writes a JSON snapshot of key trading metrics every N seconds so that:
  - The Next.js frontend can poll it at /api/metrics.
  - External tools (Grafana, scripts) can tail the file.
  - Post-session analysis can replay the equity curve.

Metrics recorded:
  - available_capital       : Current available margin (₹)
  - unrealized_pnl          : Sum of all open-position unrealized P&L (₹)
  - realized_pnl_today      : Sum of all closed-trade P&L for today (₹)
  - alpha_per_strategy      : Per-strategy P&L breakdown (last 30 days)
  - slippage                : Average slippage stats (last 5 days)
  - active_trades           : Count and details of currently open positions
  - equity_snapshot         : Time-series list (last 200 datapoints)
  - session_stats           : Win rate, trade count, best/worst for today
  - timestamp               : ISO-8601 snapshot time

Usage:
    from bot.core.metrics_exporter import metrics_exporter
    metrics_exporter.start()          # Launches background thread
    metrics_exporter.push_unrealized(symbol, pnl)  # Call from strategy loops
    metrics_exporter.stop()           # Clean shutdown
"""

import json
import threading
import time
import datetime
import os
from collections import deque
from bot.utils.logger import logger


# Path for the JSON output file (served by FastAPI backend)
METRICS_FILE = os.path.join("data", "metrics.json")

# How often to write the file (seconds)
WRITE_INTERVAL = 10

# Max equity datapoints kept in memory (rolling)
MAX_EQUITY_POINTS = 200


class MetricsExporter:
    """Thread-safe, singleton metrics exporter that writes JSON snapshots."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._running     = False
        self._thread: threading.Thread | None = None
        self._data_lock   = threading.Lock()

        # Per-symbol unrealized P&L pushed by strategy loops
        self._unrealized: dict[str, float] = {}

        # Rolling equity curve (appended on every write cycle)
        self._equity_series: deque[dict] = deque(maxlen=MAX_EQUITY_POINTS)

        # Ensure data directory exists
        os.makedirs("data", exist_ok=True)

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the background exporter thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._export_loop,
            name="MetricsExporter",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"[Metrics] Exporter started → {METRICS_FILE} (every {WRITE_INTERVAL}s)")

    def stop(self) -> None:
        """Signal the background thread to stop."""
        self._running = False
        logger.info("[Metrics] Exporter stopped.")

    # ------------------------------------------------------------------ #
    #  Real-Time Data Push (called from strategy loops)                    #
    # ------------------------------------------------------------------ #

    def push_unrealized(self, symbol: str, pnl: float) -> None:
        """Update the unrealized P&L for a single open position."""
        with self._data_lock:
            self._unrealized[symbol] = pnl

    def clear_unrealized(self, symbol: str) -> None:
        """Remove a position's unrealized P&L (called on trade close)."""
        with self._data_lock:
            self._unrealized.pop(symbol, None)

    # ------------------------------------------------------------------ #
    #  Background Write Loop                                               #
    # ------------------------------------------------------------------ #

    def _export_loop(self) -> None:
        while self._running:
            try:
                self._write_snapshot()
            except Exception as e:
                logger.error(f"[Metrics] Export error: {e}")
            time.sleep(WRITE_INTERVAL)

    def _write_snapshot(self) -> None:
        snapshot = self._build_snapshot()
        os.makedirs(os.path.dirname(METRICS_FILE), exist_ok=True)
        tmp_path = f"{METRICS_FILE}.{os.getpid()}.tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump(snapshot, f, default=str, indent=2)
            os.replace(tmp_path, METRICS_FILE)   # Atomic write
        except Exception as e:
            # Clean up the temp file if it was created but not replaced
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            raise e

    # ------------------------------------------------------------------ #
    #  Snapshot Builder                                                    #
    # ------------------------------------------------------------------ #

    def _build_snapshot(self) -> dict:
        now = datetime.datetime.now()

        # ---- Capital (from SafetyGatekeeper via trade_repo / RMS) ----
        available_capital  = self._fetch_capital()
        unrealized_pnl     = self._fetch_unrealized()
        realized_pnl_today = self._fetch_realized_today()
        alpha_per_strategy = self._fetch_alpha_per_strategy()
        slippage_stats     = self._fetch_slippage_stats()
        active_trades      = self._fetch_active_trades()
        session_stats      = self._compute_session_stats()

        # Equity snapshot point
        equity_point = {
            "ts":               now.isoformat(),
            "capital":          round(available_capital, 2),
            "unrealized_pnl":   round(unrealized_pnl, 2),
            "total_equity":     round(available_capital + unrealized_pnl, 2),
        }
        with self._data_lock:
            self._equity_series.append(equity_point)
            equity_list = list(self._equity_series)

        return {
            "timestamp":          now.isoformat(),
            "available_capital":  round(available_capital, 2),
            "unrealized_pnl":     round(unrealized_pnl, 2),
            "realized_pnl_today": round(realized_pnl_today, 2),
            "total_equity":       round(available_capital + unrealized_pnl, 2),
            "alpha_per_strategy": alpha_per_strategy,
            "slippage":           slippage_stats,
            "active_trades":      active_trades,
            "session_stats":      session_stats,
            "equity_curve":       equity_list,
        }

    # ------------------------------------------------------------------ #
    #  Data Fetchers                                                       #
    # ------------------------------------------------------------------ #

    def _fetch_capital(self) -> float:
        """Read capital from shared data/market_status.json (written by backend)."""
        try:
            path = os.path.join("data", "market_status.json")
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                capital = data.get("capital") or data.get("available_capital", 0.0)
                if capital:
                    return float(capital)
        except Exception:
            pass

        # Fallback: simulation capital from Config
        try:
            from bot.config.settings import Config
            return Config.SIMULATION_CAPITAL
        except Exception:
            return 0.0

    def _fetch_unrealized(self) -> float:
        """Sum all pushed unrealized P&L values."""
        with self._data_lock:
            return sum(self._unrealized.values())

    def _fetch_realized_today(self) -> float:
        """Aggregate realized P&L from today's closed trades."""
        try:
            from bot.core.trade_repo import trade_repo
            trades = trade_repo.get_today_trades(mode=None)   # Both LIVE and PAPER
            return sum(float(t.get("pnl", 0)) for t in trades if t.get("status") == "CLOSED")
        except Exception:
            return 0.0

    def _fetch_alpha_per_strategy(self) -> dict:
        """
        Compute P&L breakdown per strategy over the last 30 days.
        This is the "Alpha" — which strategy is actually generating edge.
        """
        try:
            from bot.core.trade_repo import trade_repo
            since = datetime.datetime.now() - datetime.timedelta(days=30)
            trades = trade_repo.get_recent_closed_trades(mode=None, since=since)

            alpha: dict[str, dict] = {}
            for t in trades:
                strat = t.get("strategy", "UNKNOWN") or "UNKNOWN"
                pnl   = float(t.get("pnl", 0))
                if strat not in alpha:
                    alpha[strat] = {
                        "total_pnl": 0.0,
                        "trades":    0,
                        "wins":      0,
                        "win_rate":  0.0,
                        "avg_pnl":   0.0,
                    }
                alpha[strat]["total_pnl"] += pnl
                alpha[strat]["trades"]    += 1
                if pnl > 0:
                    alpha[strat]["wins"] += 1

            # Compute derived metrics
            for strat, data in alpha.items():
                n = data["trades"]
                data["win_rate"] = round(data["wins"] / n * 100, 1) if n > 0 else 0.0
                data["avg_pnl"]  = round(data["total_pnl"] / n, 2)  if n > 0 else 0.0
                data["total_pnl"] = round(data["total_pnl"], 2)

            return alpha
        except Exception as e:
            logger.debug(f"[Metrics] alpha_per_strategy error: {e}")
            return {}

    def _fetch_slippage_stats(self) -> dict:
        """Read per-strategy slippage from trade_repo and auto-adjust recommendation."""
        try:
            from bot.core.trade_repo import trade_repo
            stats = trade_repo.get_slippage_stats(days=5)

            # Auto-adjust recommendation: if avg slippage > 0.5%, suggest more walk ticks
            avg_pct = stats.get("avg_slippage_pct", 0.0)
            if avg_pct > 1.0:
                recommended_walk_ticks = 8
            elif avg_pct > 0.5:
                recommended_walk_ticks = 6
            elif avg_pct > 0.2:
                recommended_walk_ticks = 5   # Current default
            else:
                recommended_walk_ticks = 3   # Low slippage → fewer walks needed

            stats["recommended_walk_ticks"] = recommended_walk_ticks
            return stats
        except Exception as e:
            logger.debug(f"[Metrics] slippage_stats error: {e}")
            return {
                "avg_slippage_pct": 0.0,
                "avg_slippage_points": 0.0,
                "sample_size": 0,
                "recommended_walk_ticks": 5,
            }

    def _fetch_active_trades(self) -> list:
        """Return list of currently OPEN trades with their unrealized P&L."""
        try:
            from bot.core.trade_repo import trade_repo
            open_trades = trade_repo.get_open_trades()

            result = []
            for t in open_trades:
                symbol = t.get("symbol", "")
                pnl    = self._unrealized.get(symbol, 0.0)
                result.append({
                    "id":            t.get("id"),
                    "symbol":        symbol,
                    "strategy":      t.get("strategy"),
                    "qty":           t.get("qty"),
                    "entry_price":   t.get("entry_price"),
                    "sl_price":      t.get("sl_price"),
                    "unrealized_pnl": round(pnl, 2),
                    "mode":          t.get("mode"),
                })
            return result
        except Exception:
            return []

    def _compute_session_stats(self) -> dict:
        """Today's session summary statistics."""
        try:
            from bot.core.trade_repo import trade_repo
            trades  = trade_repo.get_today_trades(mode=None)
            closed  = [t for t in trades if t.get("status") == "CLOSED"]
            open_t  = [t for t in trades if t.get("status") in ("OPEN", "PLACED")]

            pnls    = [float(t.get("pnl", 0)) for t in closed]
            wins    = [p for p in pnls if p > 0]
            losses  = [p for p in pnls if p <= 0]

            return {
                "total_trades":   len(trades),
                "open_positions": len(open_t),
                "closed_trades":  len(closed),
                "wins":           len(wins),
                "losses":         len(losses),
                "win_rate_pct":   round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
                "realized_pnl":   round(sum(pnls), 2),
                "best_trade":     round(max(pnls), 2) if pnls else 0.0,
                "worst_trade":    round(min(pnls), 2) if pnls else 0.0,
                "avg_trade_pnl":  round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
            }
        except Exception:
            return {}


# Singleton instance — import this in other modules
metrics_exporter = MetricsExporter()
