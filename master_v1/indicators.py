"""
master_v1/indicators.py
────────────────────────
Fresh, pure math engine for the Master Combo strategy.
Contains all technical indicators and utility helpers.
"""

import pandas as pd
import numpy as np
from datetime import time

# ─────────────────────────────────────────────
#  INDICATORS (PURE MATH)
# ─────────────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's smoothed RSI."""
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP - Resets every day."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    tp_vol = typical_price * df["volume"]
    dates = df.index.normalize()
    vwap  = pd.Series(index=df.index, dtype=float)
    for day in dates.unique():
        mask = dates == day
        cumulative_tv = tp_vol[mask].cumsum()
        cumulative_v  = df.loc[mask, "volume"].cumsum()
        vwap[mask]    = cumulative_tv / cumulative_v.replace(0, np.nan)
    return vwap


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    h, l, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def compute_pivots(df: pd.DataFrame) -> dict:
    """
    Calculate Pivot Points (R1, S1, Pivot) based on YESTERDAY'S data.
    Input df should be daily candles.
    """
    if len(df) < 2:
        return {"P": 0, "R1": 0, "S1": 0}
    
    yesterday = df.iloc[-2] # Assuming -1 is today's live/incomplete candle
    h, l, c = yesterday["high"], yesterday["low"], yesterday["close"]
    
    p = (h + l + c) / 3
    r1 = (2 * p) - l
    s1 = (2 * p) - h
    
    return {
        "P":  round(float(p), 2),
        "R1": round(float(r1), 2),
        "S1": round(float(s1), 2)
    }


# ─────────────────────────────────────────────
#  ORB LOGIC (Opening Range Breakout)
# ─────────────────────────────────────────────

def get_orb_levels(df: pd.DataFrame, start_time="09:15", end_time="09:30") -> tuple[float, float]:
    """Returns (High, Low) of the opening 15 minutes."""
    # Ensure index is datetime
    day_start = df.index.normalize()[0]
    range_mask = (df.index >= day_start + pd.Timedelta(hours=9, minutes=15)) & \
                 (df.index <= day_start + pd.Timedelta(hours=9, minutes=30))
    
    opening_range = df.loc[range_mask]
    if opening_range.empty:
        return 0.0, 0.0
    
    return float(opening_range["high"].max()), float(opening_range["low"].min())


# ─────────────────────────────────────────────
#  UTILITIES
# ─────────────────────────────────────────────

def completed_candles(df: pd.DataFrame) -> pd.DataFrame:
    """Remove the last candle if it is incomplete."""
    if df.empty:
        return df
    return df.iloc[:-1]

def get_trailing_stop_level(df: pd.DataFrame, window: int = 3) -> float:
    """Returns the lowest low of the last X candles for trailing SL."""
    if len(df) < window:
        return 0.0
    return float(df["low"].tail(window).min())
