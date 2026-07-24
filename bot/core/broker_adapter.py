import time
import datetime
from bot.config.settings import Config
from bot.utils.logger import logger

_lookup = None

SPOT_TOKEN_MAP = {
    "99926000": {"kite_token": 256265, "symbol": "NIFTY 50", "exchange": "NSE"},
    "99926009": {"kite_token": 260105, "symbol": "NIFTY BANK", "exchange": "NSE"},
    "99926037": {"kite_token": 257801, "symbol": "NIFTY FIN SERVICE", "exchange": "NSE"},
    "99926017": {"kite_token": 264969, "symbol": "INDIA VIX", "exchange": "NSE"},
}

REVERSE_SPOT_TOKEN_MAP = {
    str(v["kite_token"]): k for k, v in SPOT_TOKEN_MAP.items()
}

def get_lookup():
    global _lookup
    if _lookup is None:
        from bot.utils.token_lookup import TokenLookup
        _lookup = TokenLookup()
    return _lookup


class BaseBrokerAdapter:
    """Base interface for all broker adapters."""
    def placeOrder(self, orderparams):
        raise NotImplementedError
        
    def modifyOrder(self, orderparams):
        raise NotImplementedError
        
    def cancelOrder(self, order_id, variety="NORMAL"):
        raise NotImplementedError
        
    def orderBook(self):
        raise NotImplementedError
        
    def position(self):
        raise NotImplementedError
        
    def rmsLimit(self):
        raise NotImplementedError
        
    def ltpData(self, exchange, symbol, token):
        raise NotImplementedError
        
    def getCandleData(self, historicParam):
        raise NotImplementedError
        
    def getProfile(self, refresh_token=None):
        raise NotImplementedError

    def tradeBook(self):
        raise NotImplementedError

    def getMarketData(self, mode, params):
        raise NotImplementedError

    def get_market_ticker(self):
        raise NotImplementedError

    def get_order_ticker(self):
        raise NotImplementedError

    def get_order_book_l1(self, exchange, symbol, token):
        raise NotImplementedError



class AngelBrokerAdapter(BaseBrokerAdapter):
    """Adapter for Angel One (SmartAPI)."""
    def __init__(self, api_client):
        self.api = api_client

    def __getattr__(self, name):
        if name == 'refreshToken':
            return getattr(self.api, 'refresh_token', getattr(self.api, 'refreshToken', ''))
        if name == 'feedToken':
            return getattr(self.api, 'feed_token', getattr(self.api, 'feedToken', ''))
        return getattr(self.api, name)

    @property
    def access_token(self):
        return getattr(self.api, 'access_token', getattr(self.api, 'accessToken', ''))

    @property
    def feed_token(self):
        return getattr(self.api, 'feed_token', getattr(self.api, 'feedToken', ''))

    @property
    def refresh_token(self):
        return getattr(self.api, 'refresh_token', getattr(self.api, 'refreshToken', ''))

    def placeOrder(self, orderparams):
        return self.api.placeOrder(orderparams)

    def modifyOrder(self, orderparams):
        return self.api.modifyOrder(orderparams)

    def cancelOrder(self, order_id, variety="NORMAL"):
        return self.api.cancelOrder(order_id, variety)

    def orderBook(self):
        return self.api.orderBook()

    def position(self):
        return self.api.position()

    def rmsLimit(self):
        return self.api.rmsLimit()

    def ltpData(self, exchange, symbol, token):
        return self.api.ltpData(exchange, symbol, token)

    def getCandleData(self, historicParam):
        return self.api.getCandleData(historicParam)

    def getProfile(self, refresh_token=None):
        tok = refresh_token or self.refresh_token
        return self.api.getProfile(tok)

    def tradeBook(self):
        return self.api.tradeBook()

    def getMarketData(self, mode, params):
        return self.api.getMarketData(mode, params)

    def get_market_ticker(self):
        try:
            # pyrefly: ignore [missing-import]
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        except ImportError:
            SmartWebSocketV2 = None
        if not SmartWebSocketV2:
            raise ImportError("SmartWebSocketV2 is not installed or available.")
        
        auth_token = self.access_token
        feed_token = self.feed_token
        return SmartWebSocketV2(auth_token, Config.API_KEY, Config.CLIENT_ID, feed_token)

    def get_order_ticker(self):
        try:
            # pyrefly: ignore [missing-import]
            from SmartApi.smartWebSocketOrderUpdate import SmartWebSocketOrderUpdate
        except ImportError:
            SmartWebSocketOrderUpdate = None
        if not SmartWebSocketOrderUpdate:
            raise ImportError("SmartWebSocketOrderUpdate is not installed or available.")
        
        auth_token = self.access_token
        if not auth_token.startswith("Bearer "):
            auth_token = f"Bearer {auth_token}"
        feed_token = self.feed_token
        return SmartWebSocketOrderUpdate(auth_token, Config.API_KEY, Config.CLIENT_ID, feed_token)

    def get_order_book_l1(self, exchange, symbol, token):
        try:
            resp = self.ltpData(exchange, symbol, token)
            if resp and resp.get('status'):
                ltp = float(resp['data']['ltp'])
                return {"bid": ltp, "ask": ltp, "ltp": ltp}
        except Exception as e:
            logger.error(f"Angel get_order_book_l1 error: {e}")
        return {"bid": 0.0, "ask": 0.0, "ltp": 0.0}



class KiteBrokerAdapter(BaseBrokerAdapter):
    """Adapter for Zerodha (Kite Connect)."""
    def __init__(self, kite_client, access_token):
        self.kite = kite_client
        self._access_token = access_token
        self.refresh_token = "kite_refresh_token"

    def _handle_exception(self, method_name, exception):
        err_msg = str(exception)
        logger.error(f"Kite {method_name} error: {err_msg}")
        if "api_key" in err_msg.lower() or "token" in err_msg.lower() or "incorrect" in err_msg.lower():
            try:
                import os
                session_file = os.path.join(os.getcwd(), "data", "session_kite.json")
                if os.path.exists(session_file):
                    os.remove(session_file)
                    logger.warning(">>> [System] Stale session_kite.json file cleared.")
                
                # Flag session refresh immediately
                flag_file = os.path.join(os.getcwd(), "data", "session_refresh.flag")
                with open(flag_file, "w") as f:
                    f.write("REFRESH_REQUIRED")
                logger.warning(f">>> [System] Token error detected in {method_name}. Flagged session refresh 🔄")
            except Exception as fe:
                logger.error(f"Failed to flag session refresh: {fe}")
        return {"status": False, "message": err_msg}

    def __getattr__(self, name):
        if name == 'refreshToken' or name == 'refresh_token':
            return self.refresh_token
        if name == 'feedToken' or name == 'feed_token':
            return self.feed_token
        return getattr(self.kite, name)

    @property
    def access_token(self):
        return self._access_token

    @property
    def feed_token(self):
        return self._access_token

    def placeOrder(self, orderparams):
        try:
            # Map parameters
            variety = self.kite.VARIETY_REGULAR
            exchange = orderparams.get('exchange', 'NFO')
            tradingsymbol = orderparams.get('tradingsymbol')
            transaction_type = orderparams.get('transactiontype')
            quantity = int(orderparams.get('quantity'))
            
            # Product: INTRADAY maps to MIS, CARRYFORWARD maps to NRML
            product_type = orderparams.get('producttype', 'INTRADAY')
            product = self.kite.PRODUCT_MIS if product_type == 'INTRADAY' else self.kite.PRODUCT_NRML
            
            # Order type mapping
            ordertype = orderparams.get('ordertype', 'LIMIT')
            order_type = self.kite.ORDER_TYPE_LIMIT
            if ordertype == 'MARKET':
                order_type = self.kite.ORDER_TYPE_MARKET
            elif ordertype == 'STOPLOSS_LIMIT':
                order_type = self.kite.ORDER_TYPE_SL
            elif ordertype == 'STOPLOSS_MARKET':
                order_type = self.kite.ORDER_TYPE_SLM
                
            # Snap to 2 decimal places — Zerodha rejects prices with float artifacts
            # (e.g. 116.60000000000001 causes immediate REJECTED status)
            raw_price = orderparams.get('price')
            raw_trigger = orderparams.get('triggerprice')
            price = round(float(raw_price), 2) if raw_price is not None else None
            trigger_price = round(float(raw_trigger), 2) if raw_trigger is not None else None

            order_id = self.kite.place_order(
                variety=variety,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=quantity,
                product=product,
                order_type=order_type,
                price=price,
                trigger_price=trigger_price
            )
            return {
                "status": True,
                "message": "SUCCESS",
                "data": {"orderid": order_id}
            }
        except Exception as e:
            return self._handle_exception("placeOrder", e)

    def modifyOrder(self, orderparams):
        try:
            variety = self.kite.VARIETY_REGULAR
            order_id = orderparams.get('orderid')
            
            # Map ordertype if present
            order_type = None
            ordertype = orderparams.get('ordertype')
            if ordertype:
                if ordertype == 'LIMIT':
                    order_type = self.kite.ORDER_TYPE_LIMIT
                elif ordertype == 'MARKET':
                    order_type = self.kite.ORDER_TYPE_MARKET
                elif ordertype == 'STOPLOSS_LIMIT':
                    order_type = self.kite.ORDER_TYPE_SL
                elif ordertype == 'STOPLOSS_MARKET':
                    order_type = self.kite.ORDER_TYPE_SLM

            quantity = int(orderparams.get('quantity')) if orderparams.get('quantity') is not None else None
            price = float(orderparams.get('price')) if orderparams.get('price') is not None else None
            trigger_price = float(orderparams.get('triggerprice')) if orderparams.get('triggerprice') is not None else None

            resp = self.kite.modify_order(
                variety=variety,
                order_id=order_id,
                quantity=quantity,
                price=price,
                order_type=order_type,
                trigger_price=trigger_price
            )
            return {"status": True, "data": resp}
        except Exception as e:
            return self._handle_exception("modifyOrder", e)

    def cancelOrder(self, order_id, variety="NORMAL"):
        try:
            # Kite Connect uses variety='regular'
            resp = self.kite.cancel_order(
                variety=self.kite.VARIETY_REGULAR,
                order_id=order_id
            )
            return {"status": True, "data": resp}
        except Exception as e:
            return self._handle_exception("cancelOrder", e)

    def orderBook(self):
        try:
            orders = self.kite.orders()
            mapped = []
            for o in orders:
                # Map status: COMPLETE -> complete, REJECTED -> rejected, CANCELLED -> cancelled
                # Others map to open or pending
                status = o['status'].lower()
                if status == 'complete':
                    mapped_status = 'complete'
                elif status in ['rejected', 'cancelled']:
                    mapped_status = status
                elif status in ['open', 'validation pending', 'put order req received', 'trigger pending']:
                    mapped_status = 'open'
                else:
                    mapped_status = 'pending'

                mapped.append({
                    'orderid': o['order_id'],
                    'status': mapped_status,
                    'tradingsymbol': o['tradingsymbol'],
                    'symboltoken': str(o['instrument_token']),
                    'transactiontype': o['transaction_type'],
                    'quantity': o['quantity'],
                    'price': o['price'],
                    'averageprice': o.get('average_price', 0.0)
                })
            return {"status": True, "data": mapped}
        except Exception as e:
            return self._handle_exception("orderBook", e)

    def position(self):
        try:
            positions = self.kite.positions()
            # Kite returns net and day lists. We map the net positions.
            net_positions = positions.get('net', [])
            mapped = []
            for p in net_positions:
                buy_qty = int(p.get('buy_quantity', 0))
                sell_qty = int(p.get('sell_quantity', 0))
                net_qty = p.get('quantity', buy_qty - sell_qty)
                
                # Determine average net price
                if net_qty > 0:
                    avg_price = float(p.get('buy_price', 0.0))
                elif net_qty < 0:
                    avg_price = float(p.get('sell_price', 0.0))
                else:
                    avg_price = 0.0

                # Map product: MIS -> INTRADAY, NRML -> CARRYFORWARD
                product = p.get('product', 'MIS')
                product_type = 'INTRADAY' if product == 'MIS' else 'CARRYFORWARD'

                # Map symbol name: name (e.g. NIFTY) or parsed from tradingsymbol
                symbol_name = p.get('name')
                if not symbol_name:
                    ts = p.get('tradingsymbol', '')
                    if ts.startswith('NIFTY'):
                        symbol_name = 'NIFTY'
                    elif ts.startswith('BANKNIFTY'):
                        symbol_name = 'BANKNIFTY'
                    elif ts.startswith('FINNIFTY'):
                        symbol_name = 'FINNIFTY'
                    else:
                        symbol_name = ''

                mapped.append({
                    'tradingsymbol': p['tradingsymbol'],
                    'symboltoken': str(p['instrument_token']),
                    'buyqty': buy_qty,
                    'sellqty': sell_qty,
                    'buyavgprice': p.get('buy_price', 0.0),
                    'sellavgprice': p.get('sell_price', 0.0),
                    'realisedprice': float(p.get('realised', 0.0)),
                    'realisedpnl': float(p.get('m2m', 0.0)),
                    'netqty': net_qty,
                    'avgnetprice': avg_price,
                    'producttype': product_type,
                    'symbolname': symbol_name
                })
            return {"status": True, "data": mapped}
        except Exception as e:
            return self._handle_exception("position", e)

    def rmsLimit(self):
        try:
            margins = self.kite.margins()
            equity = margins.get('equity', {})
            # Map available cash and net margins
            net_margin = float(equity.get('net', 0.0))
            available_cash = float(equity.get('available', {}).get('cash', 0.0))
            return {
                "status": True,
                "data": {
                    "net": f"{net_margin:.2f}",
                    "availableCash": f"{available_cash:.2f}"
                }
            }
        except Exception as e:
            return self._handle_exception("rmsLimit", e)

    def ltpData(self, exchange, symbol, token):
        try:
            lookup = get_lookup()
            symbol_name, exch = lookup.get_instrument_by_token(token)
            if not symbol_name:
                tok_str = str(token)
                if tok_str in SPOT_TOKEN_MAP:
                    symbol_name, exch = SPOT_TOKEN_MAP[tok_str]["symbol"], SPOT_TOKEN_MAP[tok_str]["exchange"]
                else:
                    symbol_name, exch = symbol, exchange
            
            inst_str = f"{exch.upper()}:{symbol_name.upper()}"
            resp = self.kite.ltp([inst_str])
            if inst_str in resp:
                ltp = float(resp[inst_str]['last_price'])
                return {
                    "status": True,
                    "data": {
                        "ltp": ltp,
                        "exchange": exchange,
                        "tradingsymbol": symbol,
                        "symboltoken": str(token)
                    }
                }
            return {"status": False, "message": f"Symbol {inst_str} not found in Kite ltp response"}
        except Exception as e:
            return self._handle_exception("ltpData", e)

    def getCandleData(self, historicParam):
        try:
            token = historicParam['symboltoken']
            tok_str = str(token)
            if tok_str in SPOT_TOKEN_MAP:
                instrument_token = SPOT_TOKEN_MAP[tok_str]["kite_token"]
            else:
                instrument_token = int(token)

            from_dt = datetime.datetime.strptime(historicParam['fromdate'], "%Y-%m-%d %H:%M")
            to_dt = datetime.datetime.strptime(historicParam['todate'], "%Y-%m-%d %H:%M")
            
            # Map intervals: ONE_MINUTE -> minute, FIVE_MINUTE -> 5minute, FIFTEEN_MINUTE -> 15minute
            interval_map = {
                "ONE_MINUTE": "minute",
                "FIVE_MINUTE": "5minute",
                "FIFTEEN_MINUTE": "15minute"
            }
            kite_interval = interval_map.get(historicParam['interval'], "5minute")
            
            records = self.kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_dt,
                to_date=to_dt,
                interval=kite_interval
            )
            
            # Format to Angel One's structure: [[timestamp_str, open, high, low, close, volume], ...]
            # timestamp format: "2026-06-09T09:15:00+05:30"
            formatted = []
            for r in records:
                ts_str = r['date'].strftime("%Y-%m-%dT%H:%M:%S+05:30")
                formatted.append([
                    ts_str,
                    float(r['open']),
                    float(r['high']),
                    float(r['low']),
                    float(r['close']),
                    int(r['volume'])
                ])
            return {"status": True, "data": formatted}
        except Exception as e:
            return self._handle_exception("getCandleData", e)

    def getProfile(self, refresh_token=None):
        try:
            profile = self.kite.profile()
            return {
                "status": True,
                "data": {
                    "name": profile.get("user_name"),
                    "clientcode": profile.get("user_id"),
                    "exchanges": profile.get("exchanges", [])
                }
            }
        except Exception as e:
            return self._handle_exception("getProfile", e)

    def tradeBook(self):
        try:
            trades = self.kite.trades()
            mapped = []
            for t in trades:
                mapped.append({
                    'tradingsymbol': t['tradingsymbol'],
                    'transactiontype': t['transaction_type'],
                    'quantity': t['quantity'],
                    'averageprice': t['average_price'],
                    'orderid': t.get('order_id')
                })
            return {"status": True, "data": mapped}
        except Exception as e:
            return self._handle_exception("tradeBook", e)

    def getMarketData(self, mode, params):
        try:
            lookup = get_lookup()
            instruments = []
            token_to_symbol = {}
            for exch, tokens in params.items():
                for token in tokens:
                    symbol_name, exch_seg = lookup.get_instrument_by_token(token)
                    if not symbol_name:
                        tok_str = str(token)
                        if tok_str in SPOT_TOKEN_MAP:
                            symbol_name, exch_seg = SPOT_TOKEN_MAP[tok_str]["symbol"], SPOT_TOKEN_MAP[tok_str]["exchange"]
                        else:
                            continue
                    
                    inst_str = f"{exch_seg.upper()}:{symbol_name.upper()}"
                    instruments.append(inst_str)
                    token_to_symbol[inst_str] = token

            if not instruments:
                return {"status": False, "message": "No valid instruments to query"}

            quotes = self.kite.quote(instruments)
            fetched = []
            for inst_str, q in quotes.items():
                tok = token_to_symbol.get(inst_str)
                if tok:
                    fetched.append({
                        "symbolToken": str(tok),
                        "opnInterest": q.get("oi", 0),
                        "ltp": float(q.get("last_price", 0.0))
                    })
            return {
                "status": True,
                "message": "SUCCESS",
                "data": {"fetched": fetched}
            }
        except Exception as e:
            return self._handle_exception("getMarketData", e)

    def get_market_ticker(self):
        return KiteTickerMarketWrapper(Config.KITE_API_KEY, self.access_token)

    def get_order_ticker(self):
        return KiteTickerOrderWrapper(Config.KITE_API_KEY, self.access_token)

    def get_order_book_l1(self, exchange, symbol, token):
        try:
            lookup = get_lookup()
            symbol_name, exch = lookup.get_instrument_by_token(token)
            if not symbol_name:
                symbol_name, exch = symbol, exchange
            
            inst_str = f"{exch.upper()}:{symbol_name.upper()}"
            resp = self.kite.quote([inst_str])
            if inst_str in resp:
                q = resp[inst_str]
                ltp = float(q.get('last_price', 0.0))
                depth = q.get('depth', {})
                buy_depth = depth.get('buy', [])
                sell_depth = depth.get('sell', [])
                
                bid = float(buy_depth[0]['price']) if buy_depth else ltp
                ask = float(sell_depth[0]['price']) if sell_depth else ltp
                
                # Check for extreme zero cases
                if bid <= 0: bid = ltp
                if ask <= 0: ask = ltp
                
                return {"bid": bid, "ask": ask, "ltp": ltp}
        except Exception as e:
            self._handle_exception("get_order_book_l1", e)
        return {"bid": 0.0, "ask": 0.0, "ltp": 0.0}



class KiteTickerMarketWrapper:
    """Emulates SmartWebSocketV2 for KiteTicker ticks."""
    def __init__(self, api_key, access_token):
        # pyrefly: ignore [missing-import]
        from kiteconnect import KiteTicker
        self.kws = KiteTicker(api_key, access_token)
        self.on_open = None
        self.on_data = None
        self.on_error = None
        self.on_close = None
        self.subscribed_tokens = set()
        
        self._should_run = False
        
        # Register internal callbacks
        self.kws.on_connect = self._on_connect
        self.kws.on_ticks = self._on_ticks
        self.kws.on_close = self._on_close
        self.kws.on_error = self._on_error

    def _on_connect(self, ws, response):
        if self.on_open:
            self.on_open(self)

    def _on_ticks(self, ws, ticks):
        if self.on_data:
            mapped_ticks = []
            for t in ticks:
                token_str = str(t['instrument_token'])
                if token_str in REVERSE_SPOT_TOKEN_MAP:
                    token_str = REVERSE_SPOT_TOKEN_MAP[token_str]
                    
                last_price = t.get('last_price')
                if last_price is not None:
                    mapped_tick = {
                        'token': token_str,
                        'last_traded_price': int(last_price * 100), # to Paise
                        'exchange_timestamp': int(t.get('timestamp', datetime.datetime.now()).timestamp()) if t.get('timestamp') else int(time.time()),
                        'volume_trade_for_the_day': t.get('volume_traded', t.get('volume', 0))
                    }
                    # Depth
                    depth = t.get('depth', {})
                    buy_depth = depth.get('buy', [])
                    sell_depth = depth.get('sell', [])
                    if buy_depth:
                        mapped_tick['best_5_buy_data'] = [{'price': int(buy_depth[0]['price'] * 100)}]
                    if sell_depth:
                        mapped_tick['best_5_sell_data'] = [{'price': int(sell_depth[0]['price'] * 100)}]
                        
                    mapped_ticks.append(mapped_tick)
            if mapped_ticks:
                self.on_data(self, mapped_ticks)

    def _on_close(self, ws, code, reason):
        self._should_run = False
        if self.on_close:
            self.on_close(self)

    def _on_error(self, ws, code, reason):
        self._should_run = False
        if self.on_error:
            self.on_error(self, f"{code}: {reason}")

    def connect(self):
        self._should_run = True
        self.kws.connect(threaded=True)
        while self._should_run:
            time.sleep(1)

    def close_connection(self):
        self._should_run = False
        try:
            self.kws.close()
        except:
            pass

    def subscribe(self, correlation_id, mode, token_list):
        tokens = []
        for item in token_list:
            for t in item.get('tokens', []):
                tok_str = str(t)
                if tok_str in SPOT_TOKEN_MAP:
                    tokens.append(SPOT_TOKEN_MAP[tok_str]["kite_token"])
                else:
                    tokens.append(int(t))
        if tokens:
            self.kws.subscribe(tokens)
            self.kws.set_mode(self.kws.MODE_FULL, tokens)
            for t in tokens:
                self.subscribed_tokens.add(t)

    def unsubscribe(self, correlation_id, mode, token_list):
        tokens = []
        for item in token_list:
            for t in item.get('tokens', []):
                tok_str = str(t)
                if tok_str in SPOT_TOKEN_MAP:
                    tokens.append(SPOT_TOKEN_MAP[tok_str]["kite_token"])
                else:
                    tokens.append(int(t))
        if tokens:
            self.kws.unsubscribe(tokens)
            for t in tokens:
                self.subscribed_tokens.discard(t)


class KiteTickerOrderWrapper:
    """Emulates SmartWebSocketOrderUpdate for KiteTicker order updates."""
    def __init__(self, api_key, access_token):
        # pyrefly: ignore [missing-import]
        from kiteconnect import KiteTicker
        self.kws = KiteTicker(api_key, access_token)
        self.on_open = None
        self.on_message = None
        self.on_error = None
        self.on_close = None
        
        self._should_run = False
        
        # Register internal callbacks
        self.kws.on_connect = self._on_connect
        self.kws.on_order_update = self._on_order_update
        self.kws.on_close = self._on_close
        self.kws.on_error = self._on_error

    def _on_connect(self, ws, response):
        if self.on_open:
            self.on_open(self)

    def _on_order_update(self, ws, order):
        if self.on_message:
            status = order.get('status', '').lower()
            if status == 'complete':
                mapped_status = 'complete'
            elif status in ['rejected', 'cancelled']:
                mapped_status = status
            elif status in ['open', 'trigger pending', 'validation pending']:
                mapped_status = 'open'
            else:
                mapped_status = 'pending'
                
            mapped_msg = {
                'orderid': order['order_id'],
                'status': mapped_status,
                'averageprice': float(order.get('average_price', 0.0) or 0.0),
                'text': order.get('status_message', '')
            }
            self.on_message(self, mapped_msg)

    def _on_close(self, ws, code, reason):
        self._should_run = False
        if self.on_close:
            self.on_close(self, code, reason)

    def _on_error(self, ws, code, reason):
        self._should_run = False
        if self.on_error:
            self.on_error(self, f"{code}: {reason}")

    def connect(self):
        self._should_run = True
        self.kws.connect(threaded=True)
        while self._should_run:
            time.sleep(1)

    def close_connection(self):
        self._should_run = False
        try:
            self.kws.close()
        except:
            pass
