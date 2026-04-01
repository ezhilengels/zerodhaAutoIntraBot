"""
strategy/pivot_breakout.py
──────────────────────────
Session pivot breakout intraday strategy.

Interface contract:
  detect(symbol: str, state: SessionState) -> Signal | None
"""

from typing import Optional

from core.signal import Signal
from core.session import SessionState
from strategy.indicators import avg_volume, completed_candles, position_size
from data import nse_provider as nse
from config.settings import pivot_breakout_cfg, strategy_cfg
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    """Return a signal when price breaks above the session pivot R1 with volume."""
    now = current_hhmm()
    if now < pivot_breakout_cfg.start_time or now > pivot_breakout_cfg.end_time:
        return None

    quote = nse.get_quote(symbol)
    if not quote or quote["ltp"] <= 0:
        return None

    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty or len(df) < 5:
        return None

    intraday = df.copy()
    intraday["hhmm"] = intraday["time"].dt.strftime("%H:%M")
    pivot_window = intraday[
        (intraday["hhmm"] >= "09:15") &
        (intraday["hhmm"] <= "09:30")
    ]
    if pivot_window.empty:
        return None

    prev_close = state.prev_close_map.get(symbol, quote["prev_close"])
    session_high = float(pivot_window["high"].max())
    session_low = float(pivot_window["low"].min())
    if prev_close <= 0 or session_high <= 0 or session_low <= 0:
        return None

    pivot = (session_high + session_low + prev_close) / 3
    r1 = (2 * pivot) - session_low
    breakout_threshold = r1 * (1 + pivot_breakout_cfg.breakout_buffer_pct / 100)
    if quote["ltp"] <= breakout_threshold:
        return None

    last = intraday.iloc[-1]
    close_price = float(last["close"])
    open_price = float(last["open"])
    candle_low = float(last["low"])
    if close_price <= breakout_threshold or close_price <= open_price:
        return None

    vol_avg = avg_volume(df["volume"], exclude_last=1)
    vol_ratio = float(last["volume"]) / vol_avg if vol_avg > 0 else 0.0
    if vol_ratio < pivot_breakout_cfg.volume_multiplier_min:
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
        f"✅ Pivot Breakout Signal | {symbol} | "
        f"entry=₹{entry}  sl=₹{stop_loss}  target=₹{target}  "
        f"qty={qty}  pivot=₹{pivot:.2f}  r1=₹{r1:.2f}  "
        f"session_high=₹{session_high:.2f}  session_low=₹{session_low:.2f}  vol={vol_ratio:.2f}x"
    )

    return Signal(
        symbol=symbol,
        entry=entry,
        stop_loss=stop_loss,
        target=target,
        quantity=qty,
        capital=round(entry * qty, 2),
        gap_pct=0.0,
        vwap=0.0,
        rsi=0.0,
        vol_ratio=round(vol_ratio, 2),
    )
