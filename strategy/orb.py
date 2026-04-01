"""
strategy/orb.py
────────────────
Opening Range Breakout intraday strategy.

Interface contract:
  detect(symbol: str, state: SessionState) -> Signal | None
"""

from typing import Optional

from core.signal import Signal
from core.session import SessionState
from strategy.indicators import avg_volume, completed_candles, position_size
from data import nse_provider as nse
from config.settings import orb_cfg, strategy_cfg
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    """Return a breakout signal when price clears the opening range with volume."""
    now = current_hhmm()
    if now < orb_cfg.range_end_time or now > orb_cfg.entry_end_time:
        return None

    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty or len(df) < 5:
        return None

    intraday = df.copy()
    intraday["hhmm"] = intraday["time"].dt.strftime("%H:%M")
    orb_window = intraday[
        (intraday["hhmm"] >= orb_cfg.range_start_time) &
        (intraday["hhmm"] <= orb_cfg.range_end_time)
    ]
    if orb_window.empty:
        return None

    last = intraday.iloc[-1]

    orb_high = float(orb_window["high"].max())
    breakout_threshold = orb_high * (1 + orb_cfg.breakout_buffer_pct / 100)
    if float(last["close"]) <= breakout_threshold:
        return None

    if float(last["close"]) <= float(last["open"]):
        return None

    vol_avg = avg_volume(intraday["volume"], exclude_last=1)
    vol_ratio = float(last["volume"]) / vol_avg if vol_avg > 0 else 0.0
    if vol_ratio < orb_cfg.volume_multiplier_min:
        return None

    entry = round(float(last["close"]), 2)
    stop_loss = round(float(last["low"]), 2)
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
        f"✅ ORB Signal | {symbol} | "
        f"entry=₹{entry}  sl=₹{stop_loss}  target=₹{target}  "
        f"qty={qty}  orb_high=₹{orb_high:.2f}  vol={vol_ratio:.2f}x"
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
