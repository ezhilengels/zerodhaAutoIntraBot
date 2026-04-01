"""
short_intraday_v4
─────────────────
Intraday Swing Exhaustion Short Scanner.

Core logic:
  - ignore noisy morning action before session_start
  - skip shorts when Nifty is in a strong breakout
  - require at least 2 exhaustion signals:
      * RSI divergence (price new swing high, RSI lower than RSI at prior swing high)
      * volume climax (current volume > N× recent average)
      * overextension above EMA20
  - trigger only after price closes below VWAP

Fix log vs v3:
  [1] RSI divergence now compares RSI at current swing high vs RSI at the PREVIOUS
      swing high inside the price_swing_lookback window — not just one candle back.
  [2] Stop loss scoped to volume_lookback window, not full-session max high.
  [3] Renamed from "ATH" to "Swing Exhaustion"; added optional ATH proximity guard
      using 252-day daily data when available.
  [4] Turnover filter now uses per-candle average (mean) not cumulative session sum,
      matching the intent of the config key min_avg_turnover_rs.
  [5] price_swing_lookback and volume_lookback are now independent config params.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from config.settings import strategy_cfg
from config.v4.short_intraday import short_intraday_v4_cfg
from core.session import SessionState
from core.signal import Signal
from data import nse_provider as nse
from strategy.indicators import completed_candles, position_size
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


# ─────────────────────────────────────────────
# Indicator helpers
# ─────────────────────────────────────────────

def _calculate_rsi(data: pd.DataFrame, window: int = 14) -> pd.Series:
    delta = data["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=window).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _calc_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    return (typical * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, pd.NA)


# ─────────────────────────────────────────────
# Market filter
# ─────────────────────────────────────────────

def _check_market_safe() -> bool:
    """
    Return True only when Nifty is NOT in a strong breakout.
    Fail open (return True) if index data is unavailable.
    """
    if not short_intraday_v4_cfg.market_filter_enabled:
        return True

    nifty_df = completed_candles(nse.get_index_candles(short_intraday_v4_cfg.market_symbol))
    if nifty_df.empty or len(nifty_df) < short_intraday_v4_cfg.ema_period + 2:
        return True

    nifty_df = nifty_df.copy()
    nifty_df["ema20"] = nifty_df["close"].ewm(
        span=short_intraday_v4_cfg.ema_period, adjust=False
    ).mean()

    curr = nifty_df.iloc[-1]
    prev = nifty_df.iloc[-2]
    curr_ema = float(curr["ema20"]) if pd.notna(curr["ema20"]) else 0.0

    # Nifty making higher highs above EMA → not safe to short individual stocks
    if curr["high"] > prev["high"] and curr["close"] > curr_ema:
        return False

    return True


# ─────────────────────────────────────────────
# FIX 3 (optional): ATH proximity guard
# ─────────────────────────────────────────────

def _is_near_ath(symbol: str, curr_close: float) -> bool:
    """
    Returns True if the stock is within ath_proximity_pct of its 252-day high.
    Falls back to True (assume near ATH) if daily data is unavailable,
    so callers can decide whether to require this check or not.
    """
    if not short_intraday_v4_cfg.ath_check_enabled:
        return True  # skip guard → always allow

    try:
        daily_df = nse.get_daily_candles(symbol, lookback_days=252)
        if daily_df is None or daily_df.empty:
            return True  # fail open
        ath = float(daily_df["high"].max())
        threshold = ath * (1 - short_intraday_v4_cfg.ath_proximity_pct)
        return curr_close >= threshold
    except Exception:
        log.warning(f"ATH check failed for {symbol}, failing open.")
        return True


# ─────────────────────────────────────────────
# FIX 1: Corrected RSI divergence
# ─────────────────────────────────────────────

def _detect_rsi_divergence(
    df: pd.DataFrame,
    curr_high: float,
    curr_rsi: float,
    recent_high: float,
    rsi_min: float,
    price_swing_lookback: int,
) -> bool:
    """
    True bearish RSI divergence:
      - Current candle is AT or above the recent swing high (new high attempt)
      - RSI at the current high is LOWER than RSI at the prior swing high candle
        within the price_swing_lookback window
      - RSI must still be above rsi_min (avoid already-oversold situations)

    Fix: previously compared curr_rsi < prev_rsi (one candle back), which fired
    on any single-candle RSI dip — not true divergence. Now we locate the candle
    that held the prior swing high and compare RSIs at those two price extremes.
    """
    if curr_high < recent_high:
        return False  # not even at the high, skip

    lookback = df.tail(price_swing_lookback)

    # The current candle is the last row; find the previous swing high
    # by looking at the highest high *excluding* the last candle
    prior_window = lookback.iloc[:-1]
    if prior_window.empty:
        return False

    prior_high_idx = prior_window["high"].idxmax()
    prior_swing_rsi = float(prior_window.loc[prior_high_idx, "rsi"])

    if not pd.notna(prior_swing_rsi):
        return False

    return (
        curr_rsi < prior_swing_rsi          # RSI lower at the new price high
        and curr_rsi > rsi_min              # not already oversold
    )


# ─────────────────────────────────────────────
# Core exhaustion detector
# ─────────────────────────────────────────────

def _detect_exhaustion(df: pd.DataFrame, symbol: str) -> dict:
    df = df.copy()
    df["rsi"] = _calculate_rsi(df, short_intraday_v4_cfg.rsi_period)
    df["ema20"] = df["close"].ewm(span=short_intraday_v4_cfg.ema_period, adjust=False).mean()
    df["vwap"] = _calc_vwap(df)
    df["turnover"] = df["close"] * df["volume"]

    curr = df.iloc[-1]
    prev = df.iloc[-2]  # kept for any future single-step checks

    # FIX 5: Use separate lookback windows for price swing vs volume averaging
    price_swing_lookback = short_intraday_v4_cfg.price_swing_lookback
    volume_lookback = short_intraday_v4_cfg.volume_lookback

    recent_high = float(df["high"].tail(price_swing_lookback).max())
    avg_vol = float(df["volume"].tail(volume_lookback).mean())

    # FIX 4: Use per-candle average turnover, not cumulative session sum
    avg_turnover = float(df["turnover"].tail(volume_lookback).mean())
    if avg_turnover < short_intraday_v4_cfg.min_avg_turnover_rs:
        return {
            "action": "WAIT",
            "signals": [],
            "ema_dist": 0.0,
            "vol_ratio": 0.0,
            "rsi": 0.0,
            "vwap": 0.0,
        }

    curr_rsi = float(curr["rsi"]) if pd.notna(curr["rsi"]) else 0.0
    curr_ema = float(curr["ema20"]) if pd.notna(curr["ema20"]) else 0.0
    curr_vwap = float(curr["vwap"]) if pd.notna(curr["vwap"]) else 0.0
    curr_close = float(curr["close"])
    curr_high = float(curr["high"])

    ema_dist = ((curr_close - curr_ema) / curr_ema) if curr_ema > 0 else 0.0
    vol_ratio = (float(curr["volume"]) / avg_vol) if avg_vol > 0 else 0.0

    signals: list[str] = []

    # FIX 1: Proper RSI divergence — compare RSI at current swing high
    #         vs RSI at the prior swing high candle in the lookback window
    if _detect_rsi_divergence(
        df=df,
        curr_high=curr_high,
        curr_rsi=curr_rsi,
        recent_high=recent_high,
        rsi_min=short_intraday_v4_cfg.rsi_divergence_min,
        price_swing_lookback=price_swing_lookback,
    ):
        signals.append("RSI Divergence")

    if avg_vol > 0 and float(curr["volume"]) > avg_vol * short_intraday_v4_cfg.volume_climax_mult:
        signals.append("Volume Climax")

    if ema_dist > short_intraday_v4_cfg.ema_dist_threshold:
        signals.append(f"Overextended ({ema_dist * 100:.2f}%)")

    is_below_vwap = curr_vwap > 0 and curr_close < curr_vwap

    # FIX 2: Stop loss scoped to price_swing_lookback window, not full df
    swing_high_stop = float(df["high"].tail(price_swing_lookback).max())
    stop_loss = swing_high_stop * (1 + short_intraday_v4_cfg.stop_buffer_pct)

    confirmed = len(signals) >= short_intraday_v4_cfg.min_confirmations and is_below_vwap

    return {
        "action": "SELL (MIS)" if confirmed else "WAIT",
        "signals": signals,
        "entry_price": curr_close,
        "stop_loss": stop_loss,
        "ema_target": curr_ema,
        "ema_dist": ema_dist,
        "vol_ratio": vol_ratio,
        "rsi": curr_rsi,
        "vwap": curr_vwap,
    }


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    now = current_hhmm()
    if now < short_intraday_v4_cfg.session_start or now > short_intraday_v4_cfg.session_end:
        return None

    if symbol.upper() in short_intraday_v4_cfg.blocklist:
        return None

    if not _check_market_safe():
        return None

    df = completed_candles(nse.get_candles(symbol))
    min_candles = max(
        short_intraday_v4_cfg.rsi_period,
        short_intraday_v4_cfg.price_swing_lookback,   # FIX 5: use the correct param
        short_intraday_v4_cfg.volume_lookback,
    ) + 2
    if df.empty or len(df) < min_candles:
        return None

    signal_data = _detect_exhaustion(df, symbol)
    if signal_data["action"] != "SELL (MIS)":
        return None

    entry = round(float(signal_data["entry_price"]), 2)
    stop_loss = round(float(signal_data["stop_loss"]), 2)

    if stop_loss <= entry:
        return None

    # FIX 3: Optionally gate on ATH proximity (configurable via ath_check_enabled)
    if not _is_near_ath(symbol, entry):
        log.debug(f"⏭ {symbol} skipped — not near 252-day ATH")
        return None

    risk = stop_loss - entry
    rr_target = entry - (risk * short_intraday_v4_cfg.target_rr_mult)
    ema_target = float(signal_data["ema_target"])
    buffered_ema_target = ema_target - (entry * short_intraday_v4_cfg.min_target_buffer_pct)
    target = round(min(rr_target, buffered_ema_target), 2)

    if target >= entry:
        return None

    qty = position_size(
        entry,
        stop_loss,
        strategy_cfg.account_capital,
        strategy_cfg.risk_pct_per_trade,
        strategy_cfg.max_capital_per_trade,
        strategy_cfg.max_exposure_multiple,
    )
    if qty <= 0:
        return None

    log.info(
        f"🔻 short_intraday_v4 | {symbol} | SHORT | entry=₹{entry} sl=₹{stop_loss} "
        f"target=₹{target} qty={qty} confirms={signal_data['signals']} "
        f"rsi={signal_data['rsi']:.1f} vol={signal_data['vol_ratio']:.2f}x "
        f"ema_dist={signal_data['ema_dist'] * 100:.2f}%"
    )

    return Signal(
        symbol=symbol,
        entry=entry,
        stop_loss=stop_loss,
        target=target,
        quantity=qty,
        capital=round(entry * qty, 2),
        vwap=round(float(signal_data["vwap"]), 2),
        rsi=round(float(signal_data["rsi"]), 1),
        vol_ratio=round(float(signal_data["vol_ratio"]), 2),
        ema_dist=round(float(signal_data["ema_dist"]), 4),
        direction="SHORT",
    )
