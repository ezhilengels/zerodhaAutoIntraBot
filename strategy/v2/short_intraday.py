"""
short_intraday_v2
─────────────────
Simple mean-reversion short based on:
  1. RSI cooling from overbought
  2. Price overextended above EMA20
  3. Volume climax
  4. Shooting star rejection

Requires at least 2 confirmations.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from config.settings import strategy_cfg
from config.v2.short_intraday import short_intraday_v2_cfg
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


def _check_short_signal(df: pd.DataFrame) -> dict:
    df = df.copy()
    df["rsi"] = _calculate_rsi(df, short_intraday_v2_cfg.rsi_period)
    df["ema_20"] = df["close"].ewm(span=short_intraday_v2_cfg.ema_period, adjust=False).mean()

    current = df.iloc[-1]
    previous = df.iloc[-2]
    signals = []

    if current["rsi"] > short_intraday_v2_cfg.rsi_overbought and current["rsi"] < previous["rsi"]:
        signals.append("RSI Cooling from Overbought")

    ema_dist = (current["close"] - current["ema_20"]) / current["ema_20"] if current["ema_20"] else 0.0
    if ema_dist > short_intraday_v2_cfg.ema_dist_threshold:
        signals.append(f"Overextended: {(ema_dist * 100):.1f}% from EMA")

    avg_vol = df["volume"].tail(short_intraday_v2_cfg.volume_lookback).mean()
    if avg_vol > 0 and current["volume"] > (avg_vol * short_intraday_v2_cfg.volume_climax_mult):
        signals.append("Volume Climax detected")

    body = abs(current["close"] - current["open"])
    upper_wick = current["high"] - max(current["open"], current["close"])
    if upper_wick > (2 * body) and body > 0:
        signals.append("Shooting Star Pattern")

    is_short = len(signals) >= short_intraday_v2_cfg.min_confirmations

    return {
        "action": "SELL (MIS)" if is_short else "WAIT",
        "confirmations": signals,
        "entry_price": float(current["close"]),
        "stop_loss": float(current["high"]) * (1 + short_intraday_v2_cfg.stop_buffer_pct),
        "target": float(current["ema_20"]),
        "rsi": float(current["rsi"]) if pd.notna(current["rsi"]) else 0.0,
        "vol_ratio": float(current["volume"] / avg_vol) if avg_vol and avg_vol > 0 else 0.0,
        "ema_20": float(current["ema_20"]) if pd.notna(current["ema_20"]) else 0.0,
    }


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    now = current_hhmm()
    if now < short_intraday_v2_cfg.session_start or now > short_intraday_v2_cfg.session_end:
        return None

    if symbol.upper() in short_intraday_v2_cfg.blocklist:
        return None

    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty or len(df) < max(short_intraday_v2_cfg.rsi_period, short_intraday_v2_cfg.volume_lookback) + 2:
        return None

    signal_data = _check_short_signal(df)
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
        f"🔻 short_intraday_v2 | {symbol} | SHORT | entry=₹{entry} sl=₹{stop_loss} "
        f"target=₹{target} qty={qty} confirms={len(signal_data['confirmations'])} "
        f"rsi={signal_data['rsi']:.1f} vol={signal_data['vol_ratio']:.2f}x"
    )

    return Signal(
        symbol=symbol,
        entry=entry,
        stop_loss=stop_loss,
        target=target,
        quantity=qty,
        capital=round(entry * qty, 2),
        rsi=round(signal_data["rsi"], 1),
        vol_ratio=round(signal_data["vol_ratio"], 2),
        direction="SHORT",
    )
