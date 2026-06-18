# /add-strategy — Scaffold a New Trading Strategy

Guide for adding a new strategy that integrates cleanly with the existing bot architecture.

## Steps

1. Ask the user:
   - Strategy name (e.g., "BreakoutScalp")
   - Signal logic (what triggers entry — describe in plain English)
   - Timeframe (1m / 5m / 15m candles)
   - Direction (CE only / PE only / both)
   - Target R:R (e.g., 2:1)

2. Read these files for reference patterns:
   - `bot/strategies/momentum_strategy.py` — primary template
   - `bot/strategies/gamma_blast_strategy.py` — for parabolic/high-ADX variant
   - `bot/core/safety_checks.py` — to understand all gatekeeper methods

3. Create `bot/strategies/<name_lower>_strategy.py` using this exact skeleton:

```python
import time
import datetime
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.data_fetcher import DataFetcher
from bot.core.regime_classifier import RegimeClassifier
from bot.core.order_manager import OrderManager
from bot.core.trade_repo import trade_repo
from bot.core.market_feed import market_feed
from bot.utils.logger import logger
from bot.utils.expiry_calculator import get_next_weekly_expiry
from bot.utils.notifier import notifier

class <Name>Strategy:
    STRATEGY_NAME = "<NAME>"

    def __init__(self, api, token_loader, dry_run=False):
        self.api           = api
        self.token_loader  = token_loader
        self.dry_run       = dry_run
        self.gatekeeper    = SafetyGatekeeper(api, dry_run=dry_run)
        self.order_manager = OrderManager(api, dry_run=dry_run)
        self.data_fetcher  = DataFetcher(api)
        self.classifier    = RegimeClassifier()
        self.running       = True
        self.active_position = None

    def stop(self):
        self.running = False
        if self.active_position:
            self.close_position("USER_STOPPED")

    def execute(self, expiry, action="BUY"):
        # 1. Safety guards
        if not self.gatekeeper.is_market_open(): return
        if self.gatekeeper.is_blackout_period(): return
        if not self.gatekeeper.check_max_daily_loss(0.0): return
        if not self.gatekeeper.check_funds(required_margin_per_lot=5000): return

        # 2. Signal generation
        # ... implement here using market_feed.get_5min_candles()

        # 3. Entry
        # self.enter_position(expiry, leg)

        # 4. Monitoring loop
        while self.running:
            # check SL / target / time exit
            time.sleep(0.5)

    def enter_position(self, expiry, leg):
        # Follow momentum_strategy.enter_position() pattern exactly
        pass

    def close_position(self, reason):
        pass
```

4. Register the strategy in `bot/main.py` strategy dispatch map.

5. Add the strategy to the decision engine's `MARGIN_MAP` in `bot/core/decision_engine.py`.

6. Add the strategy to `BacktestEngine.STRATEGIES` list in `bot/core/backtest_engine.py`
   and implement `_<name>_signals()` method.

7. Write a quick smoke test in `bot/tests/` to verify the strategy runs in dry_run mode
   without exceptions.

## Rules for New Strategies
- Must call `gatekeeper.is_market_open()` before any action
- Must use `order_manager.place_smart_limit()` — never raw API calls
- Must persist state to `trade_repo` — no in-memory-only trades
- Must honour `Config.MIN_ADX_TO_TRADE` (ADX gate) if TRENDING-dependent
- Must push unrealized P&L to `metrics_exporter.push_unrealized()` in monitor loop
