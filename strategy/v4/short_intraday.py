"""
short_intraday_v4
─────────────────
ATH exhaustion short scanner.

Core logic:
  - ignore noisy morning action before 10:00
  - skip shorts when Nifty is in a strong breakout
  - require at least 2 exhaustion signals:
      * RSI divergence at a recent high
      * volume climax
      * overextension above EMA20
  - trigger only after price closes below VWAP
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


def _calculate_rsi(data: pd.DataFrame, window: int = 14) -> pd.Series:
    delta = data["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=window).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _calc_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    return (typical * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, pd.NA)


def _check_market_safe() -> bool:
    """
    Return True only when Nifty is not in a strong breakout.
    Fail open if index data is unavailable.
    """
    if not short_intraday_v4_cfg.market_filter_enabled:
        return True

    nifty_df = completed_candles(nse.get_index_candles(short_intraday_v4_cfg.market_symbol))
    if nifty_df.empty or len(nifty_df) < short_intraday_v4_cfg.ema_period + 2:
        return True

    nifty_df = nifty_df.copy()
    nifty_df["ema20"] = nifty_df["close"].ewm(span=short_intraday_v4_cfg.ema_period, adjust=False).mean()
    curr = nifty_df.iloc[-1]
    prev = nifty_df.iloc[-2]

    curr_ema = float(curr["ema20"]) if pd.notna(curr["ema20"]) else 0.0
    if curr["high"] > prev["high"] and curr["close"] > curr_ema:
        return False
    return True


def _detect_exhaustion(df: pd.DataFrame) -> dict:
    df = df.copy()
    df["rsi"] = _calculate_rsi(df, short_intraday_v4_cfg.rsi_period)
    df["ema20"] = df["close"].ewm(span=short_intraday_v4_cfg.ema_period, adjust=False).mean()
    df["vwap"] = _calc_vwap(df)
    df["turnover"] = df["close"] * df["volume"]

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    recent_high = float(df["high"].tail(short_intraday_v4_cfg.volume_lookback).max())
    avg_vol = float(df["volume"].tail(short_intraday_v4_cfg.volume_lookback).mean())
    session_turnover = float(df["turnover"].sum())
    signals: list[str] = []

    if session_turnover < short_intraday_v4_cfg.min_avg_turnover_rs:
        return {"action": "WAIT", "signals": [], "ema_dist": 0.0, "vol_ratio": 0.0, "rsi": 0.0, "vwap": 0.0}

    curr_rsi = float(curr["rsi"]) if pd.notna(curr["rsi"]) else 0.0
    prev_rsi = float(prev["rsi"]) if pd.notna(prev["rsi"]) else 0.0
    curr_ema = float(curr["ema20"]) if pd.notna(curr["ema20"]) else 0.0
    curr_vwap = float(curr["vwap"]) if pd.notna(curr["vwap"]) else 0.0
    ema_dist = ((float(curr["close"]) - curr_ema) / curr_ema) if curr_ema > 0 else 0.0
    vol_ratio = (float(curr["volume"]) / avg_vol) if avg_vol > 0 else 0.0

    if float(curr["high"]) >= recent_high and curr_rsi < prev_rsi and curr_rsi > short_intraday_v4_cfg.rsi_divergence_min:
        signals.append("RSI Divergence")

    if avg_vol > 0 and float(curr["volume"]) > avg_vol * short_intraday_v4_cfg.volume_climax_mult:
        signals.append("Volume Climax")

    if ema_dist > short_intraday_v4_cfg.ema_dist_threshold:
        signals.append(f"Overextended ({ema_dist * 100:.2f}%)")

    is_below_vwap = curr_vwap > 0 and float(curr["close"]) < curr_vwap

    return {
        "action": "SELL (MIS)" if len(signals) >= short_intraday_v4_cfg.min_confirmations and is_below_vwap else "WAIT",
        "signals": signals,
        "entry_price": float(curr["close"]),
        "stop_loss": float(df["high"].max()) * (1 + short_intraday_v4_cfg.stop_buffer_pct),
        "target": curr_ema,
        "ema_dist": float(ema_dist),
        "vol_ratio": float(vol_ratio),
        "rsi": curr_rsi,
        "vwap": curr_vwap,
    }


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    now = current_hhmm()
    if now < short_intraday_v4_cfg.session_start or now > short_intraday_v4_cfg.session_end:
        return None

    if symbol.upper() in short_intraday_v4_cfg.blocklist:
        return None

    if not _check_market_safe():
        return None

    df = completed_candles(nse.get_candles(symbol))
    if df.empty or len(df) < max(short_intraday_v4_cfg.rsi_period, short_intraday_v4_cfg.volume_lookback) + 2:
        return None

    signal_data = _detect_exhaustion(df)
    if signal_data["action"] != "SELL (MIS)":
        return None

    entry = round(float(signal_data["entry_price"]), 2)
    stop_loss = round(float(signal_data["stop_loss"]), 2)
    target = round(float(signal_data["target"]), 2)

    if stop_loss <= entry or target >= entry:
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
        f"target=₹{target} qty={qty} confirms={len(signal_data['signals'])} "
        f"rsi={signal_data['rsi']:.1f} vol={signal_data['vol_ratio']:.2f}x"
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
