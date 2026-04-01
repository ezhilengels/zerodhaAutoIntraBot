"""
short_intraday_v1
─────────────────
Confirmation-based intraday short strategy for overextended gap-up names.

Checklist:
  1. Strong day gain and price still near day high
  2. Price stretched above VWAP / EMA20
  3. RSI bearish divergence across recent swing highs
  4. Rejection candle near the top
  5. Lower high / failed retest
  6. Current candle breaks below prior swing-low / rejection low and loses VWAP
"""

from __future__ import annotations

from datetime import time
from typing import Optional

import numpy as np
import pandas as pd

from config.settings import strategy_cfg
from config.v1.short_intraday import short_intraday_v1_cfg
from core.session import SessionState
from core.signal import Signal
from data import nse_provider as nse
from strategy.indicators import completed_candles, position_size
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


def _parse_time(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _cfg() -> dict:
    return {
        "rsi_period": short_intraday_v1_cfg.rsi_period,
        "ema_period": short_intraday_v1_cfg.ema_period,
        "atr_period": short_intraday_v1_cfg.atr_period,
        "volume_lookback": short_intraday_v1_cfg.volume_lookback,
        "swing_window": short_intraday_v1_cfg.swing_window,
        "cooldown_candles": short_intraday_v1_cfg.cooldown_candles,
        "session_start": _parse_time(short_intraday_v1_cfg.session_start),
        "session_end": _parse_time(short_intraday_v1_cfg.session_end),
        "min_day_gain_pct": short_intraday_v1_cfg.min_day_gain_pct,
        "rsi_overbought": short_intraday_v1_cfg.rsi_overbought,
        "min_signal_score": short_intraday_v1_cfg.min_signal_score,
        "volume_mult_min": short_intraday_v1_cfg.volume_mult_min,
        "volume_climax_mult": short_intraday_v1_cfg.volume_climax_mult,
        "vwap_dist_atr_min": short_intraday_v1_cfg.vwap_dist_atr_min,
        "ema_dist_pct_min": short_intraday_v1_cfg.ema_dist_pct_min,
        "day_high_proximity": short_intraday_v1_cfg.day_high_proximity,
        "vwap_break_buffer_pct": short_intraday_v1_cfg.vwap_break_buffer_pct,
        "atr_sl_mult": short_intraday_v1_cfg.atr_sl_mult,
        "atr_tp_mult": short_intraday_v1_cfg.atr_tp_mult,
        "market_symbol": short_intraday_v1_cfg.market_symbol,
        "market_bullish_threshold_pct": short_intraday_v1_cfg.market_bullish_threshold_pct,
        "blocklist": short_intraday_v1_cfg.blocklist,
    }


def _compute_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _compute_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def _add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = _compute_rsi(df["close"], cfg["rsi_period"])
    df["ema20"] = _compute_ema(df["close"], cfg["ema_period"])
    df["atr"] = _compute_atr(df, cfg["atr_period"])
    df["avg_vol"] = df["volume"].rolling(cfg["volume_lookback"]).mean()
    df["vwap"] = _compute_vwap(df)

    session_open = float(df.iloc[0]["open"])
    df["day_high"] = df["high"].cummax()
    # Mean-reversion shorts should key off the strongest extension reached
    # during the day, not the final close of the current candle.
    df["day_gain_pct"] = ((df["day_high"] - session_open) / session_open) * 100
    df["ema_dist_pct"] = (df["close"] - df["ema20"]) / df["ema20"].replace(0, np.nan)
    df["vwap_dist_atr"] = (df["close"] - df["vwap"]) / df["atr"].replace(0, np.nan)
    df["near_day_high"] = ((df["day_high"] - df["close"]) / df["day_high"].replace(0, np.nan)) <= cfg["day_high_proximity"]

    df["body"] = (df["close"] - df["open"]).abs()
    df["upper_wick"] = df["high"] - df[["open", "close"]].max(axis=1)
    df["lower_wick"] = df[["open", "close"]].min(axis=1) - df["low"]
    df["bearish"] = df["close"] < df["open"]
    df["bearish_engulf"] = (
        df["bearish"]
        & (df["close"].shift(1) > df["open"].shift(1))
        & (df["open"] >= df["close"].shift(1))
        & (df["close"] <= df["open"].shift(1))
    )
    df["shooting_star"] = (
        (df["upper_wick"] >= 2 * df["body"])
        & (df["upper_wick"] >= 2 * df["lower_wick"])
        & (df["body"] > 0)
    )
    df["rejection"] = df["shooting_star"] | df["bearish_engulf"]
    df["vol_ratio"] = df["volume"] / df["avg_vol"].replace(0, np.nan)

    sw = cfg["swing_window"]
    swing_high = np.zeros(len(df), dtype=bool)
    for i in range(sw, len(df) - sw):
        if df["high"].iloc[i] == df["high"].iloc[i - sw : i + sw + 1].max():
            swing_high[i] = True
    df["swing_high"] = swing_high
    return df


def _recent_swing_positions(df: pd.DataFrame, before_idx: int, n: int = 3) -> list[int]:
    mask = df["swing_high"].iloc[:before_idx]
    idxs = list(mask[mask].index)
    return [df.index.get_loc(ix) for ix in idxs[-n:]]


def _rsi_bearish_divergence(df: pd.DataFrame, swings: list[int]) -> bool:
    if len(swings) < 2:
        return False
    a, b = swings[-2], swings[-1]
    return df["high"].iloc[b] >= df["high"].iloc[a] * 0.995 and df["rsi"].iloc[b] < df["rsi"].iloc[a]


def _lower_high(df: pd.DataFrame, swings: list[int]) -> bool:
    if len(swings) < 2:
        return False
    a, b = swings[-2], swings[-1]
    return df["high"].iloc[b] < df["high"].iloc[a] * 0.999


def _failed_bounce_below_lower_high(df: pd.DataFrame, idx: int, swings: list[int]) -> bool:
    """
    Secondary entry style:
    after a lower high is formed, allow a short when the current candle is bearish
    and closes below the prior candle low while still trading below that lower high.
    """
    if idx < 1 or len(swings) < 2:
        return False
    lower_high_pos = swings[-1]
    lower_high_price = float(df["high"].iloc[lower_high_pos])
    curr_close = float(df["close"].iloc[idx])
    prev_low = float(df["low"].iloc[idx - 1])
    curr_high = float(df["high"].iloc[idx])
    curr_bearish = bool(df["bearish"].iloc[idx])
    return curr_bearish and curr_close < prev_low and curr_high <= lower_high_price


def _breaks_recent_swing_low(df: pd.DataFrame, idx: int) -> bool:
    if idx < 2:
        return False
    prior = df.iloc[idx - 2 : idx]
    if prior.empty:
        return False
    swing_low = float(prior["low"].min())
    return float(df["close"].iloc[idx]) < swing_low


def _breaks_prior_candle_low(df: pd.DataFrame, idx: int) -> bool:
    if idx < 1:
        return False
    return float(df["close"].iloc[idx]) < float(df["low"].iloc[idx - 1])


def _market_too_bullish(cfg: dict) -> bool:
    market_df = nse.get_index_candles(cfg["market_symbol"])
    market_df = completed_candles(market_df)
    if market_df.empty:
        return False
    if "time" in market_df.columns:
        market_df = market_df.set_index("time")
    market_df = _add_indicators(market_df, cfg)
    last = market_df.iloc[-1]
    return bool(last["close"] > last["vwap"] and last["day_gain_pct"] >= cfg["market_bullish_threshold_pct"])


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    now = current_hhmm()
    if now < short_intraday_v1_cfg.session_start or now > short_intraday_v1_cfg.session_end:
        return None

    if symbol.upper() in short_intraday_v1_cfg.blocklist:
        return None

    cfg = _cfg()
    if _market_too_bullish(cfg):
        return None

    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty:
        return None
    if "time" in df.columns:
        df = df.set_index("time")

    warmup = max(cfg["ema_period"], cfg["rsi_period"], cfg["volume_lookback"]) + cfg["swing_window"] + 6
    if len(df) < warmup:
        return None

    df = _add_indicators(df, cfg)
    i = len(df) - 1
    row = df.iloc[i]

    if float(row["day_gain_pct"]) < cfg["min_day_gain_pct"]:
        return None
    if not bool(row["near_day_high"]):
        return None
    if float(row["rsi"]) < cfg["rsi_overbought"]:
        return None
    if float(row["vol_ratio"]) < cfg["volume_mult_min"]:
        return None

    swings = _recent_swing_positions(df, i)
    score = 0
    if _rsi_bearish_divergence(df, swings):
        score += 1
    if bool(row["rejection"]):
        score += 1
    if _lower_high(df, swings):
        score += 1
    failed_bounce = _failed_bounce_below_lower_high(df, i, swings)
    breakdown = _breaks_recent_swing_low(df, i) or _breaks_prior_candle_low(df, i) or failed_bounce
    if breakdown:
        score += 1
    if float(row["vwap_dist_atr"]) >= cfg["vwap_dist_atr_min"] or float(row["ema_dist_pct"]) >= cfg["ema_dist_pct_min"]:
        score += 1
    if bool(row["vol_ratio"] >= cfg["volume_climax_mult"] or (i >= 1 and bool(df["rejection"].iloc[i - 1]) and float(df["vol_ratio"].iloc[i - 1]) >= cfg["volume_climax_mult"])):
        score += 1

    vwap_break_ok = float(row["close"]) <= float(row["vwap"]) * (1 - cfg["vwap_break_buffer_pct"])
    if not vwap_break_ok:
        score -= 1

    if score < cfg["min_signal_score"]:
        return None
    if not bool(row["bearish"]):
        return None
    if not breakdown:
        return None
    if not vwap_break_ok:
        return None

    entry = round(float(row["close"]), 2)
    atr = float(row["atr"])
    if pd.isna(atr) or atr <= 0:
        return None

    stop_anchor = max(float(row["day_high"]), float(df["high"].iloc[max(0, i - 1) : i + 1].max()))
    stop_loss = round(max(stop_anchor, entry + cfg["atr_sl_mult"] * atr), 2)
    target = round(entry - cfg["atr_tp_mult"] * atr, 2)
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
        f"🔻 short_intraday_v1 | {symbol} | SHORT | entry=₹{entry} sl=₹{stop_loss} target=₹{target} "
        f"qty={qty} score={score}/6 rsi={float(row['rsi']):.1f} vwap=₹{float(row['vwap']):.2f} vol={float(row['vol_ratio']):.2f}x"
    )

    return Signal(
        symbol=symbol,
        entry=entry,
        stop_loss=stop_loss,
        target=target,
        quantity=qty,
        capital=round(entry * qty, 2),
        vwap=round(float(row["vwap"]), 2),
        rsi=round(float(row["rsi"]), 1),
        vol_ratio=round(float(row["vol_ratio"]), 2) if pd.notna(row["vol_ratio"]) else 0.0,
        direction="SHORT",
    )
