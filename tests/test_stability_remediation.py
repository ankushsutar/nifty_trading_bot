import datetime
import pandas as pd
from unittest.mock import MagicMock, patch
import pytest

from bot.core.trade_repo import TradeRepository, trade_repo

def test_is_symbol_expired():
    # Patch TokenLookup to disable scrip master layer, forcing Layer 2 regex fallback
    with patch('bot.utils.token_lookup.TokenLookup') as mock_tl_cls:
        mock_tl = MagicMock()
        mock_tl.df = None
        mock_tl.load_scrip_master.side_effect = lambda: None
        mock_tl_cls.return_value = mock_tl

        # 1. Weekly contracts
        # Let's test a weekly contract that is definitely in the past: NIFTY2460322000CE (2024-06-03 CE)
        assert TradeRepository._is_symbol_expired('NIFTY2460322000CE') is True
        
        # Let's test a weekly contract that is in the future
        assert TradeRepository._is_symbol_expired('NIFTY3011522000CE') is False

        # 2. Monthly contracts
        # Before Sept 2025: NIFTY24JUN22000CE
        assert TradeRepository._is_symbol_expired('NIFTY24JUN22000CE') is True
        
        # Far future monthly contract (e.g. 2035 June)
        assert TradeRepository._is_symbol_expired('NIFTY35JUN22000CE') is False

        # 3. Last Thursday vs Tuesday calculation checks
        # September 2025: September last Tuesday or Thursday
        # September 1, 2025 is post Sep 1, 2025, so it should use Tuesday.
        # September last day of 2025 is Sep 30, which is a Tuesday.
        # If it was Thursday, it would be Sep 25.
        real_date = datetime.date
        with patch('datetime.date') as mock_date:
            mock_date.today.return_value = real_date(2025, 9, 26)
            mock_date.side_effect = lambda *args, **kw: real_date(*args, **kw)
            # NIFTY25SEP22000CE
            expired = TradeRepository._is_symbol_expired('NIFTY25SEP22000CE')
            # Tuesday Sep 30, 2025 > Sep 26, 2025, so not expired
            assert expired is False

            # Let's test a pre-Sep 2025 monthly: NIFTY25AUG22000CE (August 2025)
            # August last Thursday is August 28, 2025.
            # If we mock today as Aug 29, 2025, it should be expired.
            mock_date.today.return_value = real_date(2025, 8, 29)
            assert TradeRepository._is_symbol_expired('NIFTY25AUG22000CE') is True

def test_is_symbol_expired_holiday_shift():
    # Last Tuesday of June 2026 is June 30.
    # Suppose June 30 is a holiday.
    real_date = datetime.date
    with patch('datetime.date') as mock_date, \
         patch('bot.utils.expiry_calculator.is_trading_day') as mock_is_trading_day, \
         patch('bot.utils.token_lookup.TokenLookup') as mock_tl_cls:
         
         # Force fallback Layer 2 (no scrip master matches)
         mock_tl = MagicMock()
         mock_tl.df = None
         mock_tl.load_scrip_master.side_effect = lambda: None
         mock_tl_cls.return_value = mock_tl
         
         # Mock is_trading_day: return False for Tuesday June 30 (holiday)
         # and True for Monday June 29
         def side_effect_trading_day(d):
             if d == real_date(2026, 6, 30):
                 return False
             return True
         mock_is_trading_day.side_effect = side_effect_trading_day
         
         # Mock today as June 30, 2026
         mock_date.today.return_value = real_date(2026, 6, 30)
         mock_date.side_effect = lambda *args, **kw: real_date(*args, **kw)
         
         # Check NIFTY26JUN22000CE (Monthly June 2026 option)
         # Tuesday June 30 is holiday, so walk back to Monday June 29.
         # Today (June 30) > calculated expiry (June 29), so it should be expired!
         assert TradeRepository._is_symbol_expired('NIFTY26JUN22000CE') is True

def test_reconcile_with_broker_sync():
    api = MagicMock()
    
    # Mock positions response with active Nifty position
    pos_data = [{
        'tradingsymbol': 'NIFTY26JUN1822000CE',
        'symboltoken': '99991',
        'netqty': '-50',
        'avgnetprice': '150.0',
        'symbolname': 'NIFTY'
    }, {
        'tradingsymbol': 'NIFTY26JUN1822100CE',
        'symboltoken': '99992',
        'netqty': '50',
        'avgnetprice': '50.0',
        'symbolname': 'NIFTY'
    }]
    
    api.orderBook.return_value = {'status': True, 'data': []}
    api.position.return_value = {'status': True, 'data': pos_data}
    
    # We patch the database collections and find/save methods
    with patch.object(trade_repo, 'client', MagicMock()), \
         patch.object(trade_repo, 'collection') as mock_col, \
         patch.object(trade_repo, 'save_trade') as mock_save_trade, \
         patch('bot.utils.token_lookup.TokenLookup') as mock_tl_cls:
         
         # Mock TokenLookup scrip master df
         mock_tl = MagicMock()
         mock_tl.df = pd.DataFrame([
             {'symbol': 'NIFTY26JUN1822000CE', 'token': '99991'},
             {'symbol': 'NIFTY26JUN1822100CE', 'token': '99992'}
         ])
         mock_tl_cls.return_value = mock_tl
         
         # No active trades in DB
         mock_col.find.return_value = []
         mock_col.find_one.return_value = None
         
         # Call reconcile
         trade_repo.reconcile_with_broker(api)
         
         # Verify save_trade was called twice to synchronize the two legs
         assert mock_save_trade.call_count == 2
         
         # Verify leg names assigned correctly
         calls = mock_save_trade.call_args_list
         call_symbols = {c.kwargs['symbol']: c.kwargs['leg'] for c in calls}
         assert call_symbols['NIFTY26JUN1822000CE'] == 'SC'
         assert call_symbols['NIFTY26JUN1822100CE'] == 'LC'


def test_data_fetcher_child_process_separation():
    from bot.core.data_fetcher import DataFetcher
    import os
    
    mock_api = MagicMock()
    # Reset singleton instance of DataFetcher
    DataFetcher._instance = None
    fetcher = DataFetcher(mock_api)
    
    with patch.object(fetcher, '_read_disk_cache') as mock_read_cache, \
         patch.object(fetcher, '_write_disk_cache') as mock_write_cache, \
         patch.object(fetcher, '_merge_live_candle') as mock_merge_live, \
         patch('os.getenv') as mock_getenv:
         
        # Setup child process environment
        def getenv_side_effect(key, default=None):
            if key == "PROCESS_TYPE":
                return "BOT"
            return default
        mock_getenv.side_effect = getenv_side_effect
        
        # When querying spot token, it should be BLOCKED and poll cache
        mock_df = pd.DataFrame([{"close": 100}])
        mock_read_cache.return_value = mock_df
        mock_merge_live.return_value = mock_df
        
        res = fetcher.fetch_latest_candles("99926000", interval="FIVE_MINUTE", days=1)
        assert res is not None
        mock_read_cache.assert_called()
        mock_api.getCandleData.assert_not_called()
        
        # Reset mocks
        mock_read_cache.reset_mock()
        mock_read_cache.return_value = None
        mock_api.getCandleData.reset_mock()
        
        # Query non-spot token (e.g. HDFCBANK option token "128046084")
        # It should bypass the block and request from the REST API directly
        mock_api.getCandleData.return_value = {
            "status": True,
            "data": [["2026-07-24T13:45:00+05:30", 10.0, 11.0, 9.0, 10.5, 1000]]
        }
        res = fetcher.fetch_latest_candles("128046084", interval="FIVE_MINUTE", days=1)
        assert res is not None
        mock_api.getCandleData.assert_called_once()


