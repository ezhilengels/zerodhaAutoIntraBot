"""
data/shoonya_provider.py
─────────────────────────
Shoonya (Finvasia) API provider. Provides real-time quotes and candles.
Uses the NorenRestApi library.
"""

import os
import pandas as pd
from typing import Optional, List
from NorenRestApi.NorenApi import NorenApi
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  SHOONYA CONFIGURATION (Read from .env)
# ─────────────────────────────────────────────────────────────────────────────
SHOONYA_USER_ID    = os.getenv("SHOONYA_USER_ID", "")
SHOONYA_PASSWORD   = os.getenv("SHOONYA_PASSWORD", "")
SHOONYA_API_KEY    = os.getenv("SHOONYA_API_KEY", "")
SHOONYA_VENDOR_CODE = os.getenv("SHOONYA_VENDOR_CODE", "")
SHOONYA_IMEI       = os.getenv("SHOONYA_IMEI", "ABC1234") # Default/Dummy IMEI

# ─────────────────────────────────────────────────────────────────────────────
#  SESSION MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────
_api = None

class ShoonyaApi(NorenApi):
    def __init__(self):
        NorenApi.__init__(self, host='https://api.shoonya.com/NorenWAP/', websocket='wss://api.shoonya.com/NorenWAP/')

def init_session() -> None:
    """Initialize the Shoonya API session."""
    global _api
    if not SHOONYA_USER_ID or not SHOONYA_PASSWORD or not SHOONYA_API_KEY:
        log.error("❌ Shoonya credentials missing in .env")
        return
    
    try:
        api = ShoonyaApi()
        # Note: Shoonya requires a 2FA factor (TOTP or similar) usually.
        # This implementation assumes the user handles TOTP or uses a fixed factor if enabled.
        # For a full automated bot, you'd integrate 'pyotp'.
        ret = api.login(userid=SHOONYA_USER_ID, password=SHOONYA_PASSWORD, twoFA='12345', 
                        vendor_code=SHOONYA_VENDOR_CODE, api_secret=SHOONYA_API_KEY, imei=SHOONYA_IMEI)
        
        if ret.get('stat') == 'Ok':
            _api = api
            log.info("✅ Shoonya session initialised")
        else:
            log.error(f"Shoonya login failed: {ret.get('emsg')}")
    except Exception as exc:
        log.error(f"Shoonya session init failed: {exc}")

def _get_api():
    global _api
    if _api is None:
        init_session()
    return _api

# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC INTERFACE (Matches nse_provider.py)
# ─────────────────────────────────────────────────────────────────────────────

def get_quote(symbol: str) -> Optional[dict]:
    """
    Fetch real-time quote for a symbol.
    Shoonya uses 'NSE' exchange and symbols usually match 'RELIANCE-EQ'.
    """
    api = _get_api()
    if not api: return None

    try:
        # Shoonya expects 'NSE|SYMBOL-EQ' format or similar
        token_info = api.searchscrip(exchange='NSE', searchtext=symbol)
        if token_info and token_info.get('stat') == 'Ok':
            token = token_info['values'][0]['token']
            quote = api.get_quotes(exchange='NSE', token=token)
            
            if quote and quote.get('stat') == 'Ok':
                return {
                    "symbol": symbol,
                    "ltp": float(quote.get('lp', 0.0)),
                    "open": float(quote.get('o', 0.0)),
                    "high": float(quote.get('h', 0.0)),
                    "low": float(quote.get('l', 0.0)),
                    "prev_close": float(quote.get('c', 0.0)), # 'c' is usually prev close in Shoonya
                    "volume": int(quote.get('v', 0)),
                }
    except Exception as exc:
        log.error(f"Shoonya quote error for {symbol}: {exc}")
    
    return None

def get_candles(symbol: str, interval: int = 5) -> pd.DataFrame:
    """
    Fetch intraday candles for the current session.
    interval: in minutes (e.g., 5)
    """
    api = _get_api()
    if not api: return pd.DataFrame()

    try:
        token_info = api.searchscrip(exchange='NSE', searchtext=symbol)
        if token_info and token_info.get('stat') == 'Ok':
            token = token_info['values'][0]['token']
            # Shoonya uses epoch timestamps for time range
            import time
            start_time = int(time.time()) - (86400 * 2) # Last 2 days
            
            data = api.get_time_price_series(exchange='NSE', token=token, starttime=start_time, interval=interval)
            
            if data and isinstance(data, list):
                df = pd.DataFrame(data)
                # Shoonya columns: ssboe, into, inth, intl, intc, intv
                df = df.rename(columns={
                    'time': 'time', # Shoonya sometimes returns 'time' or 'ssboe'
                    'into': 'open',
                    'inth': 'high',
                    'intl': 'low',
                    'intc': 'close',
                    'v':    'volume'
                })
                df['time'] = pd.to_datetime(df['time'], format='%d-%m-%Y %H:%M:%S')
                return df[['time', 'open', 'high', 'low', 'close', 'volume']]
    except Exception as exc:
        log.error(f"Shoonya candles error for {symbol}: {exc}")

    return pd.DataFrame()

def get_daily_candles(symbol: str, days: int = 5) -> pd.DataFrame:
    """Shoonya historical data for daily candles."""
    # Shoonya's daily data is fetched similarly via get_time_price_series with interval='D'
    return pd.DataFrame() # Placeholder for complex range logic

def get_index_candles(index_name: str) -> pd.DataFrame:
    """Helper for Nifty filter."""
    return get_candles("Nifty 50")

def get_fo_ban_list() -> List[str]:
    return []
