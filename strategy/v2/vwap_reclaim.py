"""
strategy/v2/vwap_reclaim.py  (v2 — HINDALCO-optimised)
───────────────────────────────────────────────────────
Key improvements:
  1. VWAP reclaim count guard (avoid 3rd+ reclaim — weakens each time)
  2. EMA slope filter (trend must be rising, not just ordered)
  3. ATR-based SL cap (avoid wide-SL trades killing position size)
  4. Candle body quality check (strong body, not just close > open)
  5. Prior candle context (the dip before reclaim must be clean)
  6. Time-of-day scoring (best reclaims happen 09:30–11:00 and 13:00–14:00)
"""

from typing import Optional
import pandas as pd

from core.signal import Signal
from core.session import SessionState
from strategy.indicators import (
    atr as calc_atr,
    avg_volume,
    completed_candles,
    position_size,
    vwap as calc_vwap,
)
from data import nse_provider as nse
from config.settings import strategy_cfg
from config.v2.vwap_reclaim import vwap_reclaim_v2_cfg
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


def _market_supports_long() -> bool:
    """Broad market must be green and above VWAP."""
    if not vwap_reclaim_v2_cfg.market_filter_enabled:
        return True
    market_df = nse.get_index_candles(vwap_reclaim_v2_cfg.market_symbol)
    market_df = completed_candles(market_df)
    if market_df.empty or len(market_df) < 5:
        return False
    market_vwap = calc_vwap(market_df)
    if market_vwap <= 0:
        return False
    last = market_df.iloc[-1]
    return float(last["close"]) > market_vwap and float(last["close"]) > float(last["open"])


def _trend_supports_long(df: pd.DataFrame) -> bool:
    """
    Improved trend check:
    - Price > fast EMA > slow EMA
    - fast EMA must have positive slope over recent candles
    """
    if not vwap_reclaim_v2_cfg.trend_filter_enabled:
        return True

    min_rows = max(vwap_reclaim_v2_cfg.ema_fast_period, vwap_reclaim_v2_cfg.ema_slow_period) + 3
    if len(df) < min_rows:
        return False

    fast_ema_series = df["close"].ewm(span=vwap_reclaim_v2_cfg.ema_fast_period, adjust=False).mean()
    slow_ema_val = float(df["close"].ewm(span=vwap_reclaim_v2_cfg.ema_slow_period, adjust=False).mean().iloc[-1])
    fast_ema_now = float(fast_ema_series.iloc[-1])
    fast_ema_prev = float(fast_ema_series.iloc[-3])
    close_price = float(df["close"].iloc[-1])

    ordering_ok = close_price > fast_ema_now > slow_ema_val
    slope_rising = fast_ema_now > fast_ema_prev
    return ordering_ok and slope_rising


def _count_vwap_reclaims(df: pd.DataFrame, vwap_val: float) -> int:
    """
    Count how many times price has crossed VWAP from below today.
    Best reclaims are usually the 1st or 2nd.
    """
    crossings = 0
    prev_below = None
    for close in df["close"].astype(float):
        below = close < vwap_val
        if prev_below is True and not below:
            crossings += 1
        prev_below = below
    return crossings


def _candle_body_quality(last: pd.Series, min_body_pct: float = 0.30) -> bool:
    """Body must be at least min_body_pct of total candle range."""
    candle_range = float(last["high"]) - float(last["low"])
    if candle_range <= 0:
        return False
    body = abs(float(last["close"]) - float(last["open"]))
    return (body / candle_range) >= min_body_pct


def _clean_dip_before_reclaim(df: pd.DataFrame, vwap_val: float) -> bool:
    """
    Prior candle should have touched near VWAP without panic-selling too deep below it.
    """
    if len(df) < 2:
        return False
    prev = df.iloc[-2]
    prev_low = float(prev["low"])
    prev_close = float(prev["close"])
    touched_vwap = prev_low <= vwap_val * 1.003
    not_panic_sold = prev_close >= vwap_val * 0.992
    return touched_vwap and not_panic_sold


def _in_best_time_window(now: str) -> bool:
    """Best reclaim windows: 09:30–11:15 and 13:00–14:15."""
    return ("09:30" <= now <= "11:15") or ("13:00" <= now <= "14:15")


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    """VWAP Reclaim v2 — higher quality entries only."""
    now = current_hhmm()
    if now < vwap_reclaim_v2_cfg.start_time or now > vwap_reclaim_v2_cfg.end_time:
        return None

    if not _market_supports_long():
        return None

    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty or len(df) < 10:
        return None

    if not _trend_supports_long(df):
        return None

    session_volume = float(df["volume"].sum())
    if session_volume < vwap_reclaim_v2_cfg.min_session_volume:
        return None

    last = df.iloc[-1]
    close_price = float(last["close"])
    open_price = float(last["open"])
    candle_low = float(last["low"])

    vwap_val = calc_vwap(df)
    if vwap_val <= 0:
        return None

    if close_price <= vwap_val or close_price <= open_price:
        return None

    dist_pct = ((close_price - vwap_val) / vwap_val) * 100
    if dist_pct < vwap_reclaim_v2_cfg.reclaim_buffer_pct:
        return None
    if dist_pct > vwap_reclaim_v2_cfg.max_distance_above_vwap_pct:
        return None

    reclaim_count = _count_vwap_reclaims(df, vwap_val)
    if reclaim_count > vwap_reclaim_v2_cfg.max_vwap_reclaims:
        log.debug(f"⛔ {symbol} VWAP reclaimed {reclaim_count}x today — too weak, skip")
        return None

    if not _clean_dip_before_reclaim(df, vwap_val):
        log.debug(f"⛔ {symbol} No clean dip before reclaim — skip")
        return None

    if not _candle_body_quality(last, min_body_pct=0.30):
        log.debug(f"⛔ {symbol} Weak candle body (doji-like) — skip")
        return None

    vol_avg = avg_volume(df["volume"], exclude_last=1)
    vol_ratio = float(last["volume"]) / vol_avg if vol_avg > 0 else 0.0
    if vol_ratio < vwap_reclaim_v2_cfg.volume_multiplier_min:
        return None

    atr_val = calc_atr(df, period=10)
    entry = round(close_price, 2)
    stop_loss = round(candle_low, 2)
    if stop_loss >= entry:
        return None

    risk = entry - stop_loss
    if atr_val > 0 and risk > atr_val * vwap_reclaim_v2_cfg.max_sl_atr_multiple:
        log.debug(
            f"⛔ {symbol} SL too wide ({risk:.2f} > "
            f"{atr_val * vwap_reclaim_v2_cfg.max_sl_atr_multiple:.2f} ATR) — skip"
        )
        return None

    in_best_window = _in_best_time_window(now)

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
        f"✅ VWAP Reclaim v2 | {symbol} | "
        f"entry=₹{entry}  sl=₹{stop_loss}  target=₹{target}  qty={qty} | "
        f"vwap=₹{vwap_val:.2f}  dist={dist_pct:.2f}%  vol={vol_ratio:.2f}x  "
        f"reclaims={reclaim_count}  atr={atr_val:.2f}  "
        f"{'🌟 best-window' if in_best_window else '🕐 off-peak'}"
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
        rsi=0.0,
        vol_ratio=round(vol_ratio, 2),
    )
