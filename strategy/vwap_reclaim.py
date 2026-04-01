"""
strategy/vwap_reclaim.py
────────────────────────
VWAP reclaim intraday strategy.

Interface contract:
  detect(symbol: str, state: SessionState) -> Signal | None
"""

from typing import Optional

from core.signal import Signal
from core.session import SessionState
from strategy.indicators import avg_volume, completed_candles, ema, position_size, vwap as calc_vwap
from data import nse_provider as nse
from config.settings import strategy_cfg, vwap_reclaim_cfg
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


def _market_supports_long() -> bool:
    """Return True when the configured market index is supportive for long trades."""
    if not vwap_reclaim_cfg.market_filter_enabled:
        return True

    market_df = nse.get_index_candles(vwap_reclaim_cfg.market_symbol)
    market_df = completed_candles(market_df)
    if market_df.empty or len(market_df) < 5:
        return False

    market_last = market_df.iloc[-1]
    market_vwap = calc_vwap(market_df)
    if market_vwap <= 0:
        return False

    close_price = float(market_last["close"])
    open_price = float(market_last["open"])
    return close_price > market_vwap and close_price > open_price


def _trend_supports_long(df) -> bool:
    """Return True when the stock is in a clean intraday uptrend."""
    if not vwap_reclaim_cfg.trend_filter_enabled:
        return True
    min_rows = max(vwap_reclaim_cfg.ema_fast_period, vwap_reclaim_cfg.ema_slow_period)
    if len(df) < min_rows:
        return False

    fast_ema = ema(df["close"], vwap_reclaim_cfg.ema_fast_period)
    slow_ema = ema(df["close"], vwap_reclaim_cfg.ema_slow_period)
    close_price = float(df["close"].iloc[-1])
    return close_price > fast_ema > slow_ema


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    """Return a signal when price reclaims VWAP with a green, high-volume candle."""
    now = current_hhmm()
    if now < vwap_reclaim_cfg.start_time or now > vwap_reclaim_cfg.end_time:
        return None

    if not _market_supports_long():
        return None

    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty or len(df) < 5:
        return None

    if not _trend_supports_long(df):
        return None

    session_volume = float(df["volume"].sum())
    if session_volume < vwap_reclaim_cfg.min_session_volume:
        return None

    last = df.iloc[-1]
    vwap_val = calc_vwap(df)
    if vwap_val <= 0:
        return None

    close_price = float(last["close"])
    open_price = float(last["open"])
    candle_low = float(last["low"])
    if close_price <= vwap_val or close_price <= open_price:
        return None

    distance_above_vwap_pct = ((close_price - vwap_val) / vwap_val) * 100
    if distance_above_vwap_pct < vwap_reclaim_cfg.reclaim_buffer_pct:
        return None
    if distance_above_vwap_pct > vwap_reclaim_cfg.max_distance_above_vwap_pct:
        return None

    vol_avg = avg_volume(df["volume"], exclude_last=1)
    vol_ratio = float(last["volume"]) / vol_avg if vol_avg > 0 else 0.0
    if vol_ratio < vwap_reclaim_cfg.volume_multiplier_min:
        return None

    entry = round(close_price, 2)
    stop_loss = round(candle_low, 2)
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
        f"✅ VWAP Reclaim Signal | {symbol} | "
        f"entry=₹{entry}  sl=₹{stop_loss}  target=₹{target}  "
        f"qty={qty}  vwap=₹{vwap_val:.2f}  vol={vol_ratio:.2f}x"
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
