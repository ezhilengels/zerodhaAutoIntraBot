"""
short_intraday_v6
─────────────────
Intraday Swing Exhaustion Short Scanner.

Core logic:
  - ignore noisy morning action before session_start (10:00)
  - hard cutoff at session_end (13:30) — no new entries in final 90 min
  - skip shorts when Nifty is in a strong breakout
  - require stock has already run ≥2% intraday (something must exist to exhaust)
  - require ALL 3 exhaustion signals (min_confirmations=3):
      * RSI divergence: price at new swing high, RSI lower than RSI at prior swing high
      * volume climax: current volume > N× recent average
      * overextension above EMA20
  - trigger only after price closes below VWAP

Changelog vs v3:
  [1] RSI divergence compares RSI at current swing high vs RSI at the prior
      swing high candle in price_swing_lookback window — not one candle back.
  [2] Stop loss scoped to price_swing_lookback window, not full-session max high.
  [3] ATH check disabled — caused adverse selection (shorting strongest trending stocks).
  [4] Turnover filter uses per-candle mean, not cumulative session sum.
  [5] price_swing_lookback and volume_lookback are independent config params.
  [6] session_end moved to 13:30 — eliminates EOD exits that were 50% of trades.
  [7] min_intraday_run_pct filter added — only short stocks already up ≥2% on day.
  [8] min_confirmations raised to 3 — requires all three signals, cuts weak setups.
  [9] target_rr_mult lowered to 1.5 — 2R was too ambitious for intraday resolution.
 [10] rsi_divergence_min raised to 65 — must be genuinely overbought to qualify.
 [11] Remaining-minutes guard added — skips entry if <90 min left in session.
 [12] ULTRACEMCO, EICHERMOT, LT added to blocklist (structural false-positive sources).
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from config.settings import strategy_cfg
from config.v6.short_intraday import short_intraday_v6_cfg
from core.session import SessionState
from core.signal import Signal
from data import nse_provider as nse
from strategy.indicators import completed_candles, position_size
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


# ─────────────────────────────────────────────
# Time helpers
# ─────────────────────────────────────────────

def _minutes_between(start: str, end: str) -> int:
    """Return signed minute difference (end - start). Positive = time remaining."""
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    return (eh * 60 + em) - (sh * 60 + sm)


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
    if not short_intraday_v6_cfg.market_filter_enabled:
        return True

    nifty_df = completed_candles(nse.get_index_candles(short_intraday_v6_cfg.market_symbol))
    if nifty_df.empty or len(nifty_df) < short_intraday_v6_cfg.ema_period + 2:
        return True

    nifty_df = nifty_df.copy()
    nifty_df["ema20"] = nifty_df["close"].ewm(
        span=short_intraday_v6_cfg.ema_period, adjust=False
    ).mean()

    curr = nifty_df.iloc[-1]
    prev = nifty_df.iloc[-2]
    curr_ema = float(curr["ema20"]) if pd.notna(curr["ema20"]) else 0.0

    # Nifty making higher highs above EMA → not safe to short individual stocks
    if curr["high"] > prev["high"] and curr["close"] > curr_ema:
        return False

    return True


# ─────────────────────────────────────────────
# Intraday run pre-filter (Fix 7)
# ─────────────────────────────────────────────

def _has_intraday_run(df: pd.DataFrame) -> bool:
    """
    Only consider shorting if the stock has already run up meaningfully today.
    Uses first completed candle's open vs current close.

    Exhaustion requires something TO exhaust — flat or down stocks don't qualify.
    """
    if len(df) < 3:
        return False
    day_open = float(df.iloc[0]["open"])
    curr_close = float(df.iloc[-1]["close"])
    if day_open <= 0:
        return False
    intraday_return = (curr_close - day_open) / day_open
    return intraday_return >= short_intraday_v6_cfg.min_intraday_run_pct


# ─────────────────────────────────────────────
# Fix 1: Corrected RSI divergence
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
      - RSI must still be above rsi_min (avoids already-oversold situations)

    Fix vs v3: previously compared curr_rsi < prev_rsi (one candle back), which
    fired on any single-candle RSI dip. Now locates the prior swing high candle
    and compares RSIs at those two distinct price extremes.
    """
    if curr_high < recent_high:
        return False  # not at the swing high, skip

    lookback = df.tail(price_swing_lookback)

    # Exclude the current (last) candle to find the PRIOR swing high
    prior_window = lookback.iloc[:-1]
    if prior_window.empty:
        return False

    prior_high_idx = prior_window["high"].idxmax()
    prior_swing_rsi = float(prior_window.loc[prior_high_idx, "rsi"])

    if not pd.notna(prior_swing_rsi):
        return False

    return (
        curr_rsi < prior_swing_rsi   # RSI lower at the new price high = divergence
        and curr_rsi > rsi_min       # not already oversold
    )


# ─────────────────────────────────────────────
# Core exhaustion detector
# ─────────────────────────────────────────────

def _detect_exhaustion(df: pd.DataFrame, symbol: str) -> dict:
    _WAIT = {
        "action":   "WAIT",
        "signals":  [],
        "ema_dist": 0.0,
        "vol_ratio": 0.0,
        "rsi":      0.0,
        "vwap":     0.0,
    }

    df = df.copy()
    df["rsi"]      = _calculate_rsi(df, short_intraday_v6_cfg.rsi_period)
    df["ema20"]    = df["close"].ewm(span=short_intraday_v6_cfg.ema_period, adjust=False).mean()
    df["vwap"]     = _calc_vwap(df)
    df["turnover"] = df["close"] * df["volume"]

    # Fix 5: independent lookback windows for price swing vs volume averaging
    price_swing_lookback = short_intraday_v6_cfg.price_swing_lookback
    volume_lookback      = short_intraday_v6_cfg.volume_lookback

    # Fix 7: stock must have run ≥min_intraday_run_pct on the day
    day_open = float(df.iloc[0]["open"]) if len(df) else 0.0
    intraday_return = ((float(df.iloc[-1]["close"]) - day_open) / day_open) if day_open > 0 else 0.0

    if not _has_intraday_run(df):
        log.debug("Intraday run filter: stock hasn't moved enough, skipping.")
        return _WAIT

    # Fix 4: per-candle average turnover, not cumulative session sum
    avg_turnover = float(df["turnover"].tail(volume_lookback).mean())
    if avg_turnover < short_intraday_v6_cfg.min_avg_turnover_rs:
        return _WAIT

    recent_high = float(df["high"].tail(price_swing_lookback).max())
    avg_vol     = float(df["volume"].tail(volume_lookback).mean())

    curr       = df.iloc[-1]
    curr_rsi   = float(curr["rsi"])   if pd.notna(curr["rsi"])   else 0.0
    curr_ema   = float(curr["ema20"]) if pd.notna(curr["ema20"]) else 0.0
    curr_vwap  = float(curr["vwap"])  if pd.notna(curr["vwap"])  else 0.0
    curr_close = float(curr["close"])
    curr_high  = float(curr["high"])

    ema_dist  = ((curr_close - curr_ema) / curr_ema) if curr_ema > 0 else 0.0
    vol_ratio = (float(curr["volume"]) / avg_vol)    if avg_vol > 0  else 0.0

    signals: list[str] = []

    # Signal 1 — Fix 1: proper RSI divergence (swing high vs prior swing high RSI)
    if _detect_rsi_divergence(
        df=df,
        curr_high=curr_high,
        curr_rsi=curr_rsi,
        recent_high=recent_high,
        rsi_min=short_intraday_v6_cfg.rsi_divergence_min,  # Fix 10: raised to 65
        price_swing_lookback=price_swing_lookback,
    ):
        signals.append("RSI Divergence")

    # Signal 2 — Volume climax
    if avg_vol > 0 and float(curr["volume"]) > avg_vol * short_intraday_v6_cfg.volume_climax_mult:
        signals.append("Volume Climax")

    # Signal 3 — Overextension above EMA20
    if ema_dist > short_intraday_v6_cfg.ema_dist_threshold:
        signals.append(f"Overextended ({ema_dist * 100:.2f}%)")

    is_below_vwap = curr_vwap > 0 and curr_close < curr_vwap

    # Fix 8: min_confirmations=3 — all three signals must fire
    confirmed = (
        len(signals) >= short_intraday_v6_cfg.min_confirmations
        and is_below_vwap
    )

    log.warning(
        f"DEBUG {symbol} | rsi={curr_rsi:.1f} | "
        f"vol_ratio={vol_ratio:.2f}x | "
        f"ema_dist={ema_dist*100:.2f}% | "
        f"below_vwap={is_below_vwap} | "
        f"intraday_run={intraday_return*100:.2f}% | "
        f"signals={signals}"
    )

    # Fix 2: stop loss scoped to price_swing_lookback window, not full session
    swing_high_stop = float(df["high"].tail(price_swing_lookback).max())
    stop_loss = swing_high_stop * (1 + short_intraday_v6_cfg.stop_buffer_pct)

    return {
        "action":      "SELL (MIS)" if confirmed else "WAIT",
        "signals":     signals,
        "entry_price": curr_close,
        "stop_loss":   stop_loss,
        "ema_target":  curr_ema,
        "ema_dist":    ema_dist,
        "vol_ratio":   vol_ratio,
        "rsi":         curr_rsi,
        "vwap":        curr_vwap,
    }


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    now = current_hhmm()

    # Fix 6: session_end is 13:30 in config — no new entries in final 90 min
    if now < short_intraday_v6_cfg.session_start or now > short_intraday_v6_cfg.session_end:
        return None

    # Fix 11: belt-and-suspenders remaining-time guard
    # Even if session_end is misconfigured, never enter with <90 min left
    SESSION_CLOSE = "15:30"
    remaining_minutes = _minutes_between(now, SESSION_CLOSE)
    if remaining_minutes < short_intraday_v6_cfg.min_minutes_remaining:
        log.debug(f"⏱ {symbol} skipped — only {remaining_minutes} min left in session.")
        return None

    # Fix 12: blocklist includes structural false-positive symbols
    if symbol.upper() in short_intraday_v6_cfg.blocklist:
        return None

    if not _check_market_safe():
        return None

    df = completed_candles(nse.get_candles(symbol))

    # Fix 5: min candles check accounts for both independent lookback params
    min_candles = max(
        short_intraday_v6_cfg.rsi_period,
        short_intraday_v6_cfg.price_swing_lookback,
        short_intraday_v6_cfg.volume_lookback,
    ) + 2
    if df.empty or len(df) < min_candles:
        return None

    signal_data = _detect_exhaustion(df, symbol)
    if signal_data["action"] != "SELL (MIS)":
        return None

    entry     = round(float(signal_data["entry_price"]), 2)
    stop_loss = round(float(signal_data["stop_loss"]),   2)

    if stop_loss <= entry:
        return None

    risk = stop_loss - entry

    # Fix 9: target_rr_mult = 1.5 (was 2.0) — more achievable intraday
    rr_target           = entry - (risk * short_intraday_v6_cfg.target_rr_mult)
    ema_target          = float(signal_data["ema_target"])
    buffered_ema_target = ema_target - (entry * short_intraday_v6_cfg.min_target_buffer_pct)
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
        f"🔻 short_intraday_v6 | {symbol} | SHORT | "
        f"entry=₹{entry} sl=₹{stop_loss} target=₹{target} qty={qty} | "
        f"signals={signal_data['signals']} | "
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
