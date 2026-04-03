"""
strategy/vwap_rsi.py
────────────────────
VWAP + RSI combo intraday strategy.

Interface contract:
  detect(symbol: str, state: SessionState) -> Signal | None
"""

from typing import Optional

from core.signal import Signal
from core.session import SessionState
from strategy.indicators import avg_volume, completed_candles, position_size, rsi as calc_rsi, vwap as calc_vwap
from data import upstox_provider as nse
from config.settings import strategy_cfg, vwap_rsi_cfg
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    """Return a signal when price is above VWAP and RSI is reclaiming strength."""
    now = current_hhmm()
    if now < vwap_rsi_cfg.start_time or now > vwap_rsi_cfg.end_time:
        return None

    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty or len(df) < 15:
        return None

    last = df.iloc[-1]
    close_price = float(last["close"])
    open_price = float(last["open"])
    candle_low = float(last["low"])

    vwap_val = calc_vwap(df)
    if vwap_val <= 0 or close_price <= vwap_val:
        return None

    distance_above_vwap_pct = ((close_price - vwap_val) / vwap_val) * 100
    if distance_above_vwap_pct > vwap_rsi_cfg.max_distance_above_vwap_pct:
        return None

    rsi_val = calc_rsi(df["close"])
    prev_rsi = calc_rsi(df["close"].iloc[:-1])
    if rsi_val <= vwap_rsi_cfg.rsi_threshold or prev_rsi >= vwap_rsi_cfg.rsi_threshold:
        return None

    if close_price <= open_price:
        return None

    vol_avg = avg_volume(df["volume"], exclude_last=1)
    vol_ratio = float(last["volume"]) / vol_avg if vol_avg > 0 else 0.0
    if vol_ratio < vwap_rsi_cfg.volume_multiplier_min:
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
        f"✅ VWAP+RSI Signal | {symbol} | "
        f"entry=₹{entry}  sl=₹{stop_loss}  target=₹{target}  "
        f"qty={qty}  vwap=₹{vwap_val:.2f}  rsi={rsi_val:.1f}  vol={vol_ratio:.2f}x"
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
        rsi=round(rsi_val, 1),
        vol_ratio=round(vol_ratio, 2),
    )
