"""
strategy/ema_crossover.py
─────────────────────────
EMA crossover intraday strategy.

Interface contract:
  detect(symbol: str, state: SessionState) -> Signal | None
"""

from typing import Optional

from core.signal import Signal
from core.session import SessionState
from strategy.indicators import avg_volume, completed_candles, position_size
from data import nse_provider as nse
from config.settings import ema_crossover_cfg, strategy_cfg
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    """Return a signal when the fast EMA crosses above the slow EMA with volume."""
    now = current_hhmm()
    if now < ema_crossover_cfg.start_time or now > ema_crossover_cfg.end_time:
        return None

    df = nse.get_candles(symbol)
    df = completed_candles(df)
    min_len = ema_crossover_cfg.slow_period + 2
    if df.empty or len(df) < min_len:
        return None

    close = df["close"]
    fast_series = close.ewm(span=ema_crossover_cfg.fast_period, adjust=False).mean()
    slow_series = close.ewm(span=ema_crossover_cfg.slow_period, adjust=False).mean()

    prev_fast = float(fast_series.iloc[-2])
    prev_slow = float(slow_series.iloc[-2])
    curr_fast = float(fast_series.iloc[-1])
    curr_slow = float(slow_series.iloc[-1])
    if not (prev_fast <= prev_slow and curr_fast > curr_slow):
        return None

    separation_pct = ((curr_fast - curr_slow) / curr_slow) * 100 if curr_slow > 0 else 0.0
    if separation_pct < ema_crossover_cfg.min_separation_pct:
        return None

    last = df.iloc[-1]
    close_price = float(last["close"])
    open_price = float(last["open"])
    candle_low = float(last["low"])
    if close_price <= open_price:
        return None

    body_pct = ((close_price - open_price) / open_price) * 100 if open_price > 0 else 0.0
    if body_pct < ema_crossover_cfg.min_body_pct:
        return None

    vol_avg = avg_volume(df["volume"], exclude_last=1)
    vol_ratio = float(last["volume"]) / vol_avg if vol_avg > 0 else 0.0
    if vol_ratio < ema_crossover_cfg.volume_multiplier_min:
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
        f"✅ EMA Crossover Signal | {symbol} | "
        f"entry=₹{entry}  sl=₹{stop_loss}  target=₹{target}  "
        f"qty={qty}  fast={curr_fast:.2f}  slow={curr_slow:.2f}  "
        f"sep={separation_pct:.2f}%  body={body_pct:.2f}%  vol={vol_ratio:.2f}x"
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
