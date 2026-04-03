"""
short_intraday_v4 - Stabilized
───────────────────────────────
Intraday Swing Exhaustion Short Scanner.

Refined for stability:
  - Intraday Run Filter (Stock must have run ≥2% before shorting)
  - Bearish Confirmation (Trigger only on a bearish candle)
  - Market Safety (Skip if Nifty is in a strong breakout)
  - Session Cutoff (No entries after 13:30 to avoid EOD reversals)
"""

from __future__ import annotations

from typing import Optional
import pandas as pd
import numpy as np

from config.settings import strategy_cfg
from config.v4.short_intraday import short_intraday_v4_cfg
from core.session import SessionState
from core.signal import Signal
from data import upstox_provider as nse
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

    # Nifty making higher highs above EMA → not safe to short
    if curr["high"] > prev["high"] and curr["close"] > curr_ema:
        return False

    return True


def _detect_rsi_divergence(
    df: pd.DataFrame,
    curr_high: float,
    curr_rsi: float,
    recent_high: float,
    rsi_min: float,
    price_swing_lookback: int,
) -> bool:
    if curr_high < recent_high:
        return False

    lookback = df.tail(price_swing_lookback)
    prior_window = lookback.iloc[:-1]
    if prior_window.empty:
        return False

    prior_high_idx = prior_window["high"].idxmax()
    prior_swing_rsi = float(prior_window.loc[prior_high_idx, "rsi"])

    if not pd.notna(prior_swing_rsi):
        return False

    return (curr_rsi < prior_swing_rsi and curr_rsi > rsi_min)


def _detect_exhaustion(df: pd.DataFrame, symbol: str) -> dict:
    df = df.copy()
    df["rsi"] = _calculate_rsi(df, short_intraday_v4_cfg.rsi_period)
    df["ema20"] = df["close"].ewm(span=short_intraday_v4_cfg.ema_period, adjust=False).mean()
    df["vwap"] = _calc_vwap(df)
    df["turnover"] = df["close"] * df["volume"]

    # Stabilization: Intraday Run Check (min 2%)
    day_open = df.iloc[0]["open"]
    curr_close = df.iloc[-1]["close"]
    run_pct = (curr_close - day_open) / day_open
    
    if run_pct < 0.02: # Hard gate for stabilization
        return {"action": "WAIT", "signals": []}

    price_swing_lookback = short_intraday_v4_cfg.price_swing_lookback
    volume_lookback = short_intraday_v4_cfg.volume_lookback

    recent_high = float(df["high"].tail(price_swing_lookback).max())
    avg_vol = float(df["volume"].tail(volume_lookback).mean())

    avg_turnover = float(df["turnover"].tail(volume_lookback).mean())
    if avg_turnover < short_intraday_v4_cfg.min_avg_turnover_rs:
        return {"action": "WAIT", "signals": []}

    curr = df.iloc[-1]
    curr_rsi = float(curr["rsi"]) if pd.notna(curr["rsi"]) else 0.0
    curr_ema = float(curr["ema20"]) if pd.notna(curr["ema20"]) else 0.0
    curr_vwap = float(curr["vwap"]) if pd.notna(curr["vwap"]) else 0.0
    curr_close = float(curr["close"])
    curr_high = float(curr["high"])
    curr_open = float(curr["open"])

    ema_dist = ((curr_close - curr_ema) / curr_ema) if curr_ema > 0 else 0.0
    vol_ratio = (float(curr["volume"]) / avg_vol) if avg_vol > 0 else 0.0

    signals: list[str] = []

    if _detect_rsi_divergence(df, curr_high, curr_rsi, recent_high, 
                             short_intraday_v4_cfg.rsi_divergence_min, price_swing_lookback):
        signals.append("RSI Divergence")

    if avg_vol > 0 and float(curr["volume"]) > avg_vol * short_intraday_v4_cfg.volume_climax_mult:
        signals.append("Volume Climax")

    if ema_dist > short_intraday_v4_cfg.ema_dist_threshold:
        signals.append("Overextended")

    # Stabilization: Trigger only on Bearish candle
    is_bearish = curr_close < curr_open
    is_below_vwap = curr_vwap > 0 and curr_close < curr_vwap

    confirmed = len(signals) >= short_intraday_v4_cfg.min_confirmations and is_below_vwap and is_bearish

    swing_high_stop = float(df["high"].tail(price_swing_lookback).max())
    stop_loss = swing_high_stop * (1 + short_intraday_v4_cfg.stop_buffer_pct)

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


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    now = current_hhmm()
    # Stabilization: Cutoff at 13:30
    if now < short_intraday_v4_cfg.session_start or now > "13:30":
        return None

    if symbol.upper() in short_intraday_v4_cfg.blocklist:
        return None

    if not _check_market_safe():
        return None

    df = completed_candles(nse.get_candles(symbol))
    min_candles = max(short_intraday_v4_cfg.rsi_period, 
                      short_intraday_v4_cfg.price_swing_lookback, 
                      short_intraday_v4_cfg.volume_lookback) + 2
    
    if df.empty or len(df) < min_candles:
        return None

    signal_data = _detect_exhaustion(df, symbol)
    if signal_data.get("action") != "SELL (MIS)":
        return None

    entry = round(float(signal_data["entry_price"]), 2)
    stop_loss = round(float(signal_data["stop_loss"]), 2)

    if stop_loss <= entry:
        return None

    risk = stop_loss - entry
    rr_target = entry - (risk * short_intraday_v4_cfg.target_rr_mult)
    ema_target = float(signal_data["ema_target"])
    target = round(min(rr_target, ema_target), 2)

    if target >= entry:
        return None

    qty = position_size(
        entry, stop_loss,
        strategy_cfg.account_capital,
        strategy_cfg.risk_pct_per_trade,
        strategy_cfg.max_capital_per_trade,
        strategy_cfg.max_exposure_multiple,
    )
    if qty <= 0:
        return None

    log.info(f"🔻 Stabilized Short v4 | {symbol} | entry=₹{entry} sl=₹{stop_loss} target=₹{target}")

    return Signal(
        symbol=symbol, entry=entry, stop_loss=stop_loss, target=target, quantity=qty,
        capital=round(entry * qty, 2), vwap=round(float(signal_data["vwap"]), 2),
        rsi=round(float(signal_data["rsi"]), 1), 
        vol_ratio=round(float(signal_data["vol_ratio"]), 2),
        ema_dist=round(float(signal_data["ema_dist"]), 4),
        direction="SHORT",
    )
