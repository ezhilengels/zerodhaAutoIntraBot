"""
strategy/v3/vwap_rsi.py  (v3 — VWAP Pullback + RSI Trend + ADX)
────────────────────────────────────────────────────────────────
Adds:
  - ADX trending filter
  - Symbol-aware overrides (e.g. stricter COALINDIA)
  - Trending-day bias check
"""

from typing import Optional
import pandas as pd

from core.signal import Signal
from core.session import SessionState
from strategy.indicators import (
    adx as calc_adx,
    avg_volume,
    completed_candles,
    position_size,
    rsi as calc_rsi,
    vwap as calc_vwap,
)
from data import nse_provider as nse
from config.settings import strategy_cfg
from config.v3.vwap_rsi import vwap_rsi_v3_cfg
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


def _ema(series: pd.Series, period: int) -> float:
    """Return the last EMA value."""
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])


def _is_pullback_candle(df: pd.DataFrame, vwap_val: float, tolerance_pct: float = 0.20) -> bool:
    """True when the previous candle touched near VWAP and the current candle reclaimed it."""
    if len(df) < 2:
        return False
    prev = df.iloc[-2]
    curr = df.iloc[-1]
    low_touched_vwap = float(prev["low"]) <= vwap_val * (1 + tolerance_pct / 100)
    curr_closed_above = float(curr["close"]) > vwap_val
    return low_touched_vwap and curr_closed_above


def _consecutive_green(df: pd.DataFrame, max_allowed: int = 3) -> bool:
    """True if there are more than max_allowed consecutive green candles."""
    count = 0
    for i in range(len(df) - 1, max(len(df) - 6, -1), -1):
        row = df.iloc[i]
        if float(row["close"]) > float(row["open"]):
            count += 1
        else:
            break
    return count > max_allowed


def _is_trending_day(df: pd.DataFrame, vwap_val: float) -> bool:
    """
    True when the stock is spending most of the recent half-hour above VWAP.
    This avoids sideways sessions that oscillate around VWAP.
    """
    if len(df) < 6:
        return False
    recent = df.iloc[-6:]
    above_count = sum(1 for _, row in recent.iterrows() if float(row["close"]) > vwap_val)
    return above_count >= 4


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    """VWAP Pullback + RSI Trend strategy v3."""
    cfg = vwap_rsi_v3_cfg.for_symbol(symbol)

    now = current_hhmm()
    if now < cfg.start_time or now > cfg.end_time:
        return None

    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty or len(df) < 20:
        return None

    last = df.iloc[-1]
    close = float(last["close"])
    open_ = float(last["open"])
    low = float(last["low"])

    ema20 = _ema(df["close"], 20)
    if close < ema20:
        return None

    vwap_val = calc_vwap(df)
    if vwap_val <= 0 or close <= vwap_val:
        return None

    if not _is_trending_day(df, vwap_val):
        log.debug(f"⛔ Non-trending day for {symbol}, skipping")
        return None

    if not _is_pullback_candle(df, vwap_val, tolerance_pct=0.25):
        return None

    dist_pct = ((close - vwap_val) / vwap_val) * 100
    if dist_pct > cfg.max_distance_above_vwap_pct:
        return None

    rsi_now = calc_rsi(df["close"])
    rsi_prev = calc_rsi(df["close"].iloc[:-1])
    if rsi_now <= cfg.rsi_threshold:
        return None
    if rsi_now >= cfg.rsi_overbought:
        return None
    if rsi_now <= rsi_prev:
        return None

    adx_val = calc_adx(df)
    if adx_val < cfg.adx_min_threshold:
        log.debug(f"⛔ ADX too low ({adx_val:.1f}) — ranging market, skip {symbol}")
        return None

    if close <= open_:
        return None

    vol_avg = avg_volume(df["volume"], exclude_last=1)
    vol_ratio = float(last["volume"]) / vol_avg if vol_avg > 0 else 0.0
    if vol_ratio < cfg.volume_multiplier_min:
        return None

    if _consecutive_green(df, max_allowed=3):
        return None

    entry = round(close, 2)
    stop_loss = round(min(low, vwap_val * 0.998), 2)
    if stop_loss >= entry:
        return None

    risk = entry - stop_loss
    target = round(entry + risk * strategy_cfg.reward_ratio, 2)
    qty = position_size(
        entry,
        stop_loss,
        strategy_cfg.account_capital,
        strategy_cfg.risk_pct_per_trade,
        strategy_cfg.max_capital_per_trade,
        strategy_cfg.max_exposure_multiple,
    )

    log.info(
        f"✅ VWAP-Pullback v3 | {symbol} | "
        f"entry=₹{entry}  sl=₹{stop_loss}  target=₹{target}  "
        f"qty={qty}  vwap=₹{vwap_val:.2f}  ema20=₹{ema20:.2f}  "
        f"adx={adx_val:.1f}  rsi={rsi_now:.1f}↑{rsi_prev:.1f}  "
        f"vol={vol_ratio:.2f}x  dist={dist_pct:.2f}%"
    )

    return Signal(
        symbol=symbol,
        entry=entry,
        stop_loss=stop_loss,
        target=target,
        quantity=qty,
        capital=round(entry * qty, 2),
        gap_pct=0.0,
        vwap=round(vwap_val, 2),
        rsi=round(rsi_now, 1),
        vol_ratio=round(vol_ratio, 2),
    )
