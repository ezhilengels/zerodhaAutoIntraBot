"""
short_intraday_v3
─────────────────
short_intraday_v2 entry logic with upgraded exit logic.

Entry:
  - same mean-reversion short filters as v2
  - market + sector weakness
  - VWAP breakdown confirmation

Exit:
  - stop above trigger high
  - target is the deeper of:
      1. EMA20 reversion
      2. fixed R-multiple target
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from config.settings import strategy_cfg
from config.v3.short_intraday import short_intraday_v3_cfg
from core.session import SessionState
from core.signal import Signal
from data import upstox_provider as nse
from strategy.indicators import completed_candles, position_size
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


SECTOR_INDEX_MAP = {
    "HDFCBANK": "NIFTY BANK", "ICICIBANK": "NIFTY BANK", "SBIN": "NIFTY BANK",
    "AXISBANK": "NIFTY BANK", "KOTAKBANK": "NIFTY BANK", "BANKBARODA": "NIFTY PSU BANK",
    "PNB": "NIFTY PSU BANK", "CANBK": "NIFTY PSU BANK", "UNIONBANK": "NIFTY PSU BANK",
    "FEDERALBNK": "NIFTY BANK", "INDUSINDBK": "NIFTY BANK",
    "TCS": "NIFTY IT", "INFY": "NIFTY IT", "WIPRO": "NIFTY IT", "TECHM": "NIFTY IT", "HCLTECH": "NIFTY IT",
    "COALINDIA": "NIFTY METAL", "HINDALCO": "NIFTY METAL", "JSWSTEEL": "NIFTY METAL",
    "TATASTEEL": "NIFTY METAL", "VEDL": "NIFTY METAL", "JINDALSTEL": "NIFTY METAL",
    "BAJAJ-AUTO": "NIFTY AUTO", "EICHERMOT": "NIFTY AUTO", "MARUTI": "NIFTY AUTO", "M&M": "NIFTY AUTO",
    "HINDUNILVR": "NIFTY FMCG", "ITC": "NIFTY FMCG", "BRITANNIA": "NIFTY FMCG",
    "ASIANPAINT": "NIFTY CONSUMPTION", "PIDILITIND": "NIFTY CONSUMPTION", "ULTRACEMCO": "NIFTY INDIA CONSUMPTION",
    "SUNPHARMA": "NIFTY PHARMA", "DRREDDY": "NIFTY PHARMA", "CIPLA": "NIFTY PHARMA",
    "RELIANCE": "NIFTY OIL & GAS", "ONGC": "NIFTY OIL & GAS", "BPCL": "NIFTY OIL & GAS", "GAIL": "NIFTY OIL & GAS",
}


def _calculate_rsi(data: pd.DataFrame, window: int = 14) -> pd.Series:
    delta = data["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=window).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _check_market_trend() -> bool:
    if not short_intraday_v3_cfg.market_filter_enabled:
        return True
    df = completed_candles(nse.get_index_candles(short_intraday_v3_cfg.market_symbol))
    if df.empty or len(df) < max(short_intraday_v3_cfg.rsi_period, short_intraday_v3_cfg.ema_period) + 2:
        return True
    df = df.copy()
    df["rsi"] = _calculate_rsi(df, short_intraday_v3_cfg.rsi_period)
    df["ema_20"] = df["close"].ewm(span=short_intraday_v3_cfg.ema_period, adjust=False).mean()
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    below_ema = bool(curr["close"] < curr["ema_20"]) if pd.notna(curr["ema_20"]) else False
    rsi_cooling = bool(pd.notna(curr["rsi"]) and pd.notna(prev["rsi"]) and curr["rsi"] < prev["rsi"])
    return below_ema or rsi_cooling


def _check_sector_trend(symbol: str) -> bool:
    if not short_intraday_v3_cfg.sector_filter_enabled:
        return True
    sector_index = SECTOR_INDEX_MAP.get(symbol.upper())
    if not sector_index:
        return True
    df = completed_candles(nse.get_index_candles(sector_index))
    if df.empty or len(df) < max(short_intraday_v3_cfg.rsi_period, short_intraday_v3_cfg.ema_period) + 2:
        return True
    df = df.copy()
    df["rsi"] = _calculate_rsi(df, short_intraday_v3_cfg.rsi_period)
    df["ema_20"] = df["close"].ewm(span=short_intraday_v3_cfg.ema_period, adjust=False).mean()
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    below_ema = bool(curr["close"] < curr["ema_20"]) if pd.notna(curr["ema_20"]) else False
    rsi_cooling = bool(pd.notna(curr["rsi"]) and pd.notna(prev["rsi"]) and curr["rsi"] < prev["rsi"])
    return below_ema or rsi_cooling


def _is_near_ath(symbol: str, ltp: float) -> bool:
    if not short_intraday_v3_cfg.require_near_ath:
        return True
    daily_df = nse.get_daily_candles(symbol, days=short_intraday_v3_cfg.ath_lookback_days)
    if daily_df.empty or ltp <= 0:
        return False
    ath_price = float(daily_df["high"].max()) if not daily_df["high"].empty else 0.0
    if ath_price <= 0:
        return False
    distance_pct = ((ath_price - ltp) / ath_price) * 100
    return distance_pct <= short_intraday_v3_cfg.ath_near_pct


def _check_short_signal(df: pd.DataFrame) -> dict:
    df = df.copy()
    df["rsi"] = _calculate_rsi(df, short_intraday_v3_cfg.rsi_period)
    df["ema_20"] = df["close"].ewm(span=short_intraday_v3_cfg.ema_period, adjust=False).mean()
    typical = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (typical * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, pd.NA)
    df["turnover"] = df["close"] * df["volume"]

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    signals = []

    session_open = float(df.iloc[0]["open"])
    running_high = float(df["high"].max())
    day_gain_pct = ((running_high - session_open) / session_open) * 100 if session_open > 0 else 0.0
    if day_gain_pct < short_intraday_v3_cfg.min_day_gain_pct:
        return {
            "action": "WAIT",
            "confirmations": [],
            "entry_price": float(curr["close"]),
            "stop_loss": float(curr["high"]) * (1 + short_intraday_v3_cfg.stop_buffer_pct),
            "ema_target": float(curr["ema_20"]) if pd.notna(curr["ema_20"]) else float(curr["close"]),
            "rsi": float(curr["rsi"]) if pd.notna(curr["rsi"]) else 0.0,
            "vol_ratio": 0.0,
            "ema_dist": 0.0,
            "vwap": float(curr["vwap"]) if pd.notna(curr["vwap"]) else 0.0,
        }

    session_turnover = float(df["turnover"].sum())
    if session_turnover < short_intraday_v3_cfg.min_avg_turnover_rs:
        return {
            "action": "WAIT",
            "confirmations": [],
            "entry_price": float(curr["close"]),
            "stop_loss": float(curr["high"]) * (1 + short_intraday_v3_cfg.stop_buffer_pct),
            "ema_target": float(curr["ema_20"]) if pd.notna(curr["ema_20"]) else float(curr["close"]),
            "rsi": float(curr["rsi"]) if pd.notna(curr["rsi"]) else 0.0,
            "vol_ratio": 0.0,
            "ema_dist": 0.0,
            "vwap": float(curr["vwap"]) if pd.notna(curr["vwap"]) else 0.0,
        }

    if curr["rsi"] > short_intraday_v3_cfg.rsi_overbought and curr["rsi"] < prev["rsi"]:
        signals.append("RSI Cooling")

    ema_dist = (curr["close"] - curr["ema_20"]) / curr["ema_20"] if curr["ema_20"] else 0.0
    if ema_dist > short_intraday_v3_cfg.ema_dist_threshold:
        signals.append(f"Overextended: {round(ema_dist * 100, 2)}%")

    avg_vol = df["volume"].tail(short_intraday_v3_cfg.volume_lookback).mean()
    vol_ratio = float(curr["volume"] / avg_vol) if avg_vol and avg_vol > 0 else 0.0
    if avg_vol > 0 and curr["volume"] > (avg_vol * short_intraday_v3_cfg.volume_climax_mult):
        signals.append("Volume Climax")

    body = abs(curr["close"] - curr["open"])
    upper_wick = curr["high"] - max(curr["open"], curr["close"])
    if upper_wick > (2 * body) and body > 0:
        signals.append("Shooting Star")

    lookback = min(short_intraday_v3_cfg.lower_high_lookback, len(df) - 1)
    if lookback >= 2:
        recent_peak = float(df["high"].iloc[-(lookback + 1):-1].max())
        lower_high = curr["high"] <= recent_peak * (1 - short_intraday_v3_cfg.lower_high_buffer_pct)
        if lower_high:
            signals.append("Lower High")

    vwap_break = bool(
        pd.notna(curr["vwap"])
        and pd.notna(prev["vwap"])
        and curr["close"] < curr["vwap"]
        and prev["close"] >= prev["vwap"]
    )
    if vwap_break:
        signals.append("VWAP Breakdown")

    return {
        "action": "SELL (MIS)" if len(signals) >= short_intraday_v3_cfg.min_confirmations and vwap_break else "WAIT",
        "confirmations": signals,
        "entry_price": float(curr["close"]),
        "stop_loss": float(curr["high"]) * (1 + short_intraday_v3_cfg.stop_buffer_pct),
        "ema_target": float(curr["ema_20"]),
        "rsi": float(curr["rsi"]) if pd.notna(curr["rsi"]) else 0.0,
        "vol_ratio": vol_ratio,
        "ema_dist": float(ema_dist) if pd.notna(ema_dist) else 0.0,
        "vwap": float(curr["vwap"]) if pd.notna(curr["vwap"]) else 0.0,
    }


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    now = current_hhmm()
    if now < short_intraday_v3_cfg.session_start or now > short_intraday_v3_cfg.session_end:
        return None
    if symbol.upper() in short_intraday_v3_cfg.blocklist:
        return None
    if not _check_market_trend():
        return None
    if not _check_sector_trend(symbol):
        return None

    df = completed_candles(nse.get_candles(symbol))
    if df.empty or len(df) < max(short_intraday_v3_cfg.rsi_period, short_intraday_v3_cfg.volume_lookback) + 2:
        return None

    ltp = float(df.iloc[-1]["close"])
    if not _is_near_ath(symbol, ltp):
        return None

    signal_data = _check_short_signal(df)
    if signal_data["action"] != "SELL (MIS)":
        return None

    entry = round(float(signal_data["entry_price"]), 2)
    stop_loss = round(float(signal_data["stop_loss"]), 2)
    if stop_loss <= entry:
        return None

    risk = stop_loss - entry
    rr_target = entry - (risk * short_intraday_v3_cfg.target_rr_mult)
    buffered_ema_target = float(signal_data["ema_target"]) - (entry * short_intraday_v3_cfg.min_target_buffer_pct)
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
        f"🔻 short_intraday_v3 | {symbol} | SHORT | entry=₹{entry} sl=₹{stop_loss} "
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
        ema_dist=round(signal_data["ema_dist"], 4),
        direction="SHORT",
    )
