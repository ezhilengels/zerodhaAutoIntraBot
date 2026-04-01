"""
data/nse_provider.py
─────────────────────
All NSE HTTP interactions: session management, live quotes, intraday candles.

To swap data sources later (e.g. add a Kite WebSocket feed or a paid vendor),
create a new file in this package with the same public interface:
  get_quote(symbol) -> dict | None
  get_candles(symbol) -> pd.DataFrame
"""

import os
from urllib.parse import quote_plus
import requests
import pandas as pd
from typing import Optional
import yfinance as yf
from config.settings import execution_cfg, paths_cfg
from utils.logger import get_logger

log = get_logger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
}

_session = requests.Session()
_session.headers.update(_HEADERS)
_seen_candle_keys: set[tuple[str, str]] = set()
_prev_close_cache: dict[str, float] = {}


# ─────────────────────────────────────────────────────────────────────────────
#  Session management
# ─────────────────────────────────────────────────────────────────────────────

def init_session() -> None:
    """Prime the NSE cookie. Call once at startup."""
    try:
        _session.get("https://www.nseindia.com", timeout=10)
        log.info("✅ NSE session initialised")
    except Exception as exc:
        log.error(f"NSE session init failed: {exc}")


def _refresh_and_retry(url: str) -> requests.Response:
    init_session()
    return _session.get(url, timeout=10)


def _persist_candles(symbol: str, df: pd.DataFrame) -> None:
    """Append newly seen candles to a combined replay CSV for later testing."""
    if df.empty or not execution_cfg.save_replay_candles:
        return

    csv_path = paths_cfg.replay_csv
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    out = df.copy()
    out["symbol"] = symbol
    out["time"] = pd.to_datetime(out["time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    out["prev_close"] = _prev_close_cache.get(symbol)
    out = out[["symbol", "time", "open", "high", "low", "close", "volume", "prev_close"]]

    fresh_rows = []
    for row in out.itertuples(index=False):
        key = (row.symbol, row.time)
        if key in _seen_candle_keys:
            continue
        _seen_candle_keys.add(key)
        fresh_rows.append(row)

    if not fresh_rows:
        return

    fresh_df = pd.DataFrame(fresh_rows, columns=out.columns)
    fresh_df["prev_close"] = fresh_df["prev_close"].astype("Float64")
    file_exists = os.path.exists(csv_path)
    fresh_df.to_csv(csv_path, mode="a", header=not file_exists, index=False)
    log.info(f"🗂️ Saved {len(fresh_df)} candle(s) for {symbol} → {csv_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  Public interface
# ─────────────────────────────────────────────────────────────────────────────

def get_quote(symbol: str) -> Optional[dict]:
    """
    Live NSE equity quote.
    Returns a normalised dict or None on failure.
    """
    url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
    try:
        resp = _session.get(url, timeout=10)
        if resp.status_code == 401:
            resp = _refresh_and_retry(url)

        data       = resp.json()
        pi         = data.get("priceInfo", {})
        intraday   = pi.get("intraDayHighLow", {})
        trade_info = data.get("marketDeptOrderBook", {}).get("tradeInfo", {})

        quote = {
            "symbol":     symbol,
            "ltp":        float(pi.get("lastPrice",     0)),
            "open":       float(pi.get("open",          0)),
            "high":       float(intraday.get("max",     0)),
            "low":        float(intraday.get("min",     0)),
            "prev_close": float(pi.get("previousClose", 0)),
            "volume":     int(trade_info.get("totalTradedVolume", 0)),
        }
        if quote["prev_close"] > 0:
            _prev_close_cache[symbol] = quote["prev_close"]
        return quote
    except Exception as exc:
        log.error(f"{symbol}: NSE quote failed — {exc}")
        return None


def get_candles(symbol: str) -> pd.DataFrame:
    """
    Intraday OHLCV candles from NSE chart API.
    Returns DataFrame[time, open, high, low, close, volume] or empty DataFrame.
    """
    url = (
        f"https://www.nseindia.com/api/chart-databyindex?"
        f"index={symbol}EQN&indices=false"
    )
    try:
        resp = _session.get(url, timeout=10)
        if resp.status_code == 401:
            resp = _refresh_and_retry(url)

        graph = resp.json().get("grapData", [])
        if not graph:
            return pd.DataFrame()

        df = pd.DataFrame(graph, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.drop(columns=["timestamp"])
        df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
        df = df.reset_index(drop=True)
        _persist_candles(symbol, df)
        return df

    except Exception as exc:
        log.error(f"{symbol}: Candle fetch failed — {exc}")
        return pd.DataFrame()


def get_index_candles(index_name: str) -> pd.DataFrame:
    """
    Intraday OHLCV candles for a market index from NSE chart API.
    Returns DataFrame[time, open, high, low, close, volume] or empty DataFrame.
    """
    url = (
        f"https://www.nseindia.com/api/chart-databyindex?"
        f"index={quote_plus(index_name)}&indices=true"
    )
    try:
        resp = _session.get(url, timeout=10)
        if resp.status_code == 401:
            resp = _refresh_and_retry(url)

        graph = resp.json().get("grapData", [])
        if not graph:
            return pd.DataFrame()

        df = pd.DataFrame(graph, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.drop(columns=["timestamp"])
        df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
        return df.reset_index(drop=True)

    except Exception as exc:
        log.error(f"{index_name}: Index candle fetch failed — {exc}")
        return pd.DataFrame()


def get_daily_candles(symbol: str, days: int = 5) -> pd.DataFrame:
    """
    Daily OHLCV candles for pivot-style strategies.

    NSE does not expose a simple stable daily OHLC endpoint through the current
    provider code, so this uses Yahoo Finance as a lightweight daily fallback.
    Returns DataFrame[time, open, high, low, close, volume] or empty DataFrame.
    """
    period_days = max(days + 5, 10)
    yahoo_symbol = f"{symbol}.NS"
    try:
        df = yf.download(
            yahoo_symbol,
            period=f"{period_days}d",
            interval="1d",
            auto_adjust=False,
            progress=False,
        )
        if df.empty:
            return pd.DataFrame()

        df = df.reset_index()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

        time_col = "Datetime" if "Datetime" in df.columns else "Date"
        df = df.rename(columns={time_col: "time"})
        df = df.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        df["time"] = pd.to_datetime(df["time"])
        cols = ["time", "open", "high", "low", "close", "volume"]
        return df[cols].astype(
            {"open": float, "high": float, "low": float, "close": float, "volume": float}
        ).reset_index(drop=True)
    except Exception as exc:
        log.error(f"{symbol}: Daily candle fetch failed — {exc}")
        return pd.DataFrame()


def get_fo_ban_list() -> list[str]:
    """
    Fetch the official NSE F&O ban list from the published CSV archive.
    Returns uppercase symbols. Returns [] on failure.
    """
    url = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
    try:
        resp = _session.get(url, timeout=10)
        if resp.status_code == 401:
            resp = _refresh_and_retry(url)
        resp.raise_for_status()

        lines = [line.strip() for line in resp.text.splitlines() if line.strip()]
        symbols: list[str] = []
        for line in lines[1:]:
            symbol = line.split(",")[0].strip().upper()
            if symbol:
                symbols.append(symbol)
        return symbols
    except Exception as exc:
        log.error(f"F&O ban list fetch failed — {exc}")
        return []
