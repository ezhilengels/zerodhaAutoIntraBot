"""
data/upstox_provider.py
────────────────────────
Upstox API v2 provider. Official real-time data with no delay.
"""

import os
import pandas as pd
from typing import Optional, List
import upstox_client
from upstox_client.rest import ApiException
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  UPSTOX CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")
API_VERSION = "2.0"

# Instrument keys (Upstox History API uses pipes format)
_instrument_key_cache: dict[str, str] = {}

def load_instrument_keys():
    """Load symbol to instrument_key mapping from local CSV."""
    global _instrument_key_cache
    path = "data/upstox_instruments.csv"
    if not os.path.exists(path):
        log.warning(f"⚠️ {path} not found. Run scripts/download_upstox_instruments.py first!")
        return
    
    try:
        df = pd.read_csv(path)
        # tradingsymbol -> instrument_key
        _instrument_key_cache = dict(zip(df['tradingsymbol'], df['instrument_key']))
        log.info(f"✅ Loaded {len(_instrument_key_cache)} instrument keys")
    except Exception as e:
        log.error(f"Failed to load instrument keys: {e}")

def init_session() -> None:
    """Initialize the Upstox API session."""
    if not UPSTOX_ACCESS_TOKEN:
        log.error("❌ UPSTOX_ACCESS_TOKEN missing in .env. Run login script first!")
        return
    load_instrument_keys()
    log.info("✅ Upstox session ready")

def _get_api_client():
    configuration = upstox_client.Configuration()
    configuration.access_token = UPSTOX_ACCESS_TOKEN
    return upstox_client.MarketQuoteApi(upstox_client.ApiClient(configuration))

def _get_history_api():
    configuration = upstox_client.Configuration()
    configuration.access_token = UPSTOX_ACCESS_TOKEN
    return upstox_client.HistoryApi(upstox_client.ApiClient(configuration))

def _resolve_instrument_key(symbol: str) -> Optional[str]:
    """Search for the instrument key (pipes format) for a given symbol."""
    sym_upper = symbol.upper()
    if sym_upper in _instrument_key_cache:
        return _instrument_key_cache[sym_upper]
    
    # Try alternate: NIFTY 50 -> Nifty 50
    sym_alt = sym_upper.replace("NIFTY 50", "Nifty 50")
    if sym_alt in _instrument_key_cache:
        return _instrument_key_cache[sym_alt]

    log.debug(f"Instrument key not found for {symbol}")
    return None

def _resolve_quote_key(symbol: str) -> Optional[str]:
    """Upstox Market Quote API uses the same pipe-delimited instrument key."""
    return _resolve_instrument_key(symbol)

# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC INTERFACE
# ─────────────────────────────────────────────────────────────────────────────

def get_quote(symbol: str) -> Optional[dict]:
    """Fetch real-time quote via Upstox Market Quote API."""
    if not UPSTOX_ACCESS_TOKEN: return None
    key = _resolve_quote_key(symbol)
    if not key:
        log.error(f"Could not resolve instrument key for quote: {symbol}")
        return None

    try:
        api = _get_api_client()
        api_response = api.get_full_market_quote(key, API_VERSION)
        
        if api_response.status == 'success':
            # Use the first key in the response data if our key is missing
            if key not in api_response.data and api_response.data:
                res_key = list(api_response.data.keys())[0]
                log.info(f"Key {key} not found, using {res_key} instead")
                val = api_response.data[res_key]
            elif key in api_response.data:
                val = api_response.data[key]
            else:
                log.error(f"No data for {key} in response")
                return None
            
            return {
                "symbol": symbol,
                "ltp": float(val.last_price),
                "open": float(val.ohlc.open),
                "high": float(val.ohlc.high),
                "low": float(val.ohlc.low),
                "prev_close": float(val.ohlc.close),
                "volume": int(val.volume),
            }
        else:
            log.error(f"Upstox quote status failed: {api_response.status}")
    except Exception as e:
        log.error(f"Upstox quote error for {symbol}: {e}")
    return None

def get_candles(symbol: str, interval_min: int = 5) -> pd.DataFrame:
    """Fetch 1minute candles using instrument_key and resample."""
    key = _resolve_instrument_key(symbol)
    if not key:
        log.error(f"Could not resolve instrument key for candles: {symbol}")
        return pd.DataFrame()
    if not UPSTOX_ACCESS_TOKEN: 
        log.error("UPSTOX_ACCESS_TOKEN missing")
        return pd.DataFrame()

    try:
        api = _get_history_api()
        api_response = api.get_intra_day_candle_data(key, "1minute", API_VERSION)
        
        if api_response.status == 'success':
            candles = api_response.data.candles
            if not candles: 
                log.warning(f"No candles returned for {symbol}")
                return pd.DataFrame()

            df = pd.DataFrame(candles, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'oi'])
            df['time'] = pd.to_datetime(df['time'])
            df = df.sort_values('time').set_index('time')
            
            if interval_min == 1:
                return df.reset_index()[['time', 'open', 'high', 'low', 'close', 'volume']]

            resampled = df.resample(f'{interval_min}T', label='left', closed='left').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()
            
            return resampled.reset_index()
        else:
            log.error(f"Upstox candles status failed: {api_response.status}")
            
    except Exception as e:
        log.error(f"Upstox candles error for {symbol}: {e}")
    return pd.DataFrame()

def get_daily_candles(symbol: str, days: int = 5) -> pd.DataFrame:
    """Fetch daily data for gap/ATH logic."""
    key = _resolve_instrument_key(symbol)
    if not key or not UPSTOX_ACCESS_TOKEN: return pd.DataFrame()

    try:
        api = _get_history_api()
        end_date = pd.Timestamp.now().strftime('%Y-%m-%d')
        start_date = (pd.Timestamp.now() - pd.Timedelta(days=days*3)).strftime('%Y-%m-%d')
        
        api_response = api.get_historical_candle_data1(key, "day", end_date, start_date, API_VERSION)
        
        if api_response.status == 'success':
            candles = api_response.data.candles
            df = pd.DataFrame(candles, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'oi'])
            df['time'] = pd.to_datetime(df['time'])
            df = df.sort_values('time').reset_index(drop=True)
            return df.tail(days)[['time', 'open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        log.error(f"Upstox daily candles error for {symbol}: {e}")
    return pd.DataFrame()

def get_index_candles(index_name: str) -> pd.DataFrame:
    return get_candles(index_name)

def get_fo_ban_list() -> List[str]:
    return []
