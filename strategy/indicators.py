"""
strategy/indicators.py
───────────────────────
Pure, stateless indicator functions.
No imports from other project modules — only pandas/numpy.
Add new indicators here freely; they're usable by any strategy.
"""

import pandas as pd
from datetime import timedelta


def rsi(series: pd.Series, period: int = 14) -> float:
    """
    Wilder's RSI.  Returns 50.0 when there is not enough data.
    """
    if len(series) < period + 1:
        return 50.0
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return float((100 - (100 / (1 + rs))).iloc[-1])


def vwap(df: pd.DataFrame) -> float:
    """
    Cumulative VWAP from market open.
    Expects columns: high, low, close, volume.
    Returns 0.0 on empty input.
    """
    if df.empty:
        return 0.0
    tp  = (df["high"] + df["low"] + df["close"]) / 3
    return float((tp * df["volume"]).cumsum().iloc[-1] / df["volume"].cumsum().iloc[-1])


def ema(series: pd.Series, period: int) -> float:
    """Last value of an EMA. Returns NaN when data is insufficient."""
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])


def sma(series: pd.Series, period: int) -> float:
    """Last value of a simple moving average."""
    return float(series.rolling(period).mean().iloc[-1])


def avg_volume(series: pd.Series, exclude_last: int = 1) -> float:
    """
    Mean volume excluding the most recent `exclude_last` candles.
    Used to compare current-candle volume against historical context.
    """
    sliced = series.iloc[:-exclude_last] if exclude_last > 0 else series
    return float(sliced.mean()) if len(sliced) > 0 else 0.0


def completed_candles(df: pd.DataFrame, candle_minutes: int = 5) -> pd.DataFrame:
    """
    Return only completed candles.

    NSE's intraday chart feed may include the currently-forming candle; when it
    does, using that row creates false signals that disappear by candle close.
    This helper trims the last row until its bar duration has fully elapsed.
    """
    if df.empty or "time" not in df.columns:
        return df

    trimmed = df.copy()
    last_time = pd.Timestamp(trimmed["time"].iloc[-1])
    now = pd.Timestamp.now(tz=last_time.tz) if last_time.tzinfo else pd.Timestamp.now()

    if now < last_time + timedelta(minutes=candle_minutes):
        trimmed = trimmed.iloc[:-1]

    return trimmed.reset_index(drop=True)


def atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    Average True Range — useful for dynamic SL sizing in future strategies.
    Returns 0.0 when data is insufficient.
    """
    if len(df) < period + 1:
        return 0.0
    high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def adx(df: pd.DataFrame, period: int = 14) -> float:
    """
    Return current ADX value. >20 suggests trend strength, <20 suggests chop.
    Returns 0.0 when data is insufficient.
    """
    if len(df) < period + 1:
        return 0.0

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr_val = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr_val
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr_val
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di)).fillna(0.0)
    return float(dx.ewm(span=period, adjust=False).mean().iloc[-1])


def position_size(
    entry: float,
    stop_loss: float,
    account_capital: float,
    risk_pct_per_trade: float,
    max_capital_per_trade: float,
    max_exposure_multiple: float,
) -> int:
    """
    Quantity sized by both capital cap and max rupee risk.
    Returns at least 1 share when entry/stop values are valid.
    """
    if entry <= 0 or stop_loss <= 0 or stop_loss >= entry:
        return 1

    capital_qty = int(max_capital_per_trade / entry) if max_capital_per_trade > 0 else 0
    exposure_cap = account_capital * max_exposure_multiple
    exposure_qty = int(exposure_cap / entry) if exposure_cap > 0 else 0
    risk_budget = account_capital * (risk_pct_per_trade / 100.0)
    per_share_risk = entry - stop_loss
    risk_qty = int(risk_budget / per_share_risk) if per_share_risk > 0 else 0

    valid = [qty for qty in (capital_qty, exposure_qty, risk_qty) if qty > 0]
    return max(1, min(valid)) if valid else 1
