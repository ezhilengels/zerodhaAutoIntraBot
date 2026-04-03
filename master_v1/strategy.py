"""
master_v1/strategy.py
───────────────────────
SUPERCHARGED Master Combo Strategy.
Features: Volume Velocity, Hard Volume Gate, and 3-Candle Trailing SL.
"""

from typing import Optional
import pandas as pd
import numpy as np
from datetime import datetime

from core.signal import Signal
from core.session import SessionState
from data import upstox_provider as nse
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

# Import our private math island
from master_v1.indicators import (
    compute_rsi, compute_vwap, compute_atr, 
    compute_ema, compute_pivots, get_orb_levels, 
    completed_candles, get_trailing_stop_level
)

log = get_logger(__name__)

# ─────────────────────────────────────────────
#  STRATEGY SETTINGS
# ─────────────────────────────────────────────
CONFIG = {
    "min_score": 4,           # Normal points required
    "velocity_score": 3,      # Points required if Volume is Rocketing
    "velocity_mult": 2.0,     # What defines a "Rocket" (2x Vol)
    "min_vol_mult": 1.2,      # HARD GATE: Current Vol must be 20% > Avg
    "vwap_guard_pct": 0.02,   # 2.0% Max distance from VWAP
    "atr_sl_mult": 1.5,       
    "atr_tp_mult": 3.0,       
    "session_start": "09:45",
    "session_end": "15:00"
}

# ─────────────────────────────────────────────
#  THE SCORING ENGINE
# ─────────────────────────────────────────────

def get_master_score(df: pd.DataFrame, pivots: dict, orb_high: float) -> tuple[int, dict]:
    """Calculates the 0-10 score based on 5 components."""
    last = df.iloc[-1]
    score = 0
    details = {}

    # 1. ORB Brain (+2)
    orb_pass = last["close"] > orb_high if orb_high > 0 else False
    score += 2 if orb_pass else 0
    details["orb"] = orb_pass

    # 2. VWAP Brain (+2)
    vwap_pass = last["close"] > last["vwap"]
    score += 2 if vwap_pass else 0
    details["vwap"] = vwap_pass

    # 3. EMA Brain (+2)
    ema_pass = last["ema9"] > last["ema21"]
    score += 2 if ema_pass else 0
    details["ema"] = ema_pass

    # 4. RSI Brain (+1)
    rsi_pass = 50 <= last["rsi"] <= 65
    score += 1 if rsi_pass else 0
    details["rsi"] = rsi_pass

    # 5. Pivot Brain (+1)
    pivot_pass = last["close"] > pivots["R1"] if pivots["R1"] > 0 else False
    score += 1 if pivot_pass else 0
    details["pivot"] = pivot_pass

    return score, details


# ─────────────────────────────────────────────
#  THE DETECTOR
# ─────────────────────────────────────────────

def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    now = current_hhmm()
    if now < CONFIG["session_start"] or now > CONFIG["session_end"]:
        return None

    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty or len(df) < 50: return None

    df = df.copy()
    if "time" in df.columns: df = df.set_index("time")

    # 1. Calculate Indicators
    df["rsi"]  = compute_rsi(df["close"])
    df["vwap"] = compute_vwap(df)
    df["atr"]  = compute_atr(df)
    df["ema9"] = compute_ema(df["close"], 9)
    df["ema21"]= compute_ema(df["close"], 21)
    df["ema50"]= compute_ema(df["close"], 50)
    df["avg_vol"] = df["volume"].rolling(20).mean()

    orb_high, _ = get_orb_levels(df)
    daily_df = nse.get_daily_candles(symbol, days=5)
    pivots = compute_pivots(daily_df)

    last = df.iloc[-1]

    # 2. HARD GATE: Daily Trend (EMA 50)
    if last["close"] < last["ema50"]:
        return None

    # 3. HARD GATE: Institutional Volume (1.2x Avg)
    vol_ratio = last["volume"] / last["avg_vol"] if last["avg_vol"] > 0 else 0
    if vol_ratio < CONFIG["min_vol_mult"]:
        return None

    # 4. Scoring
    score, _ = get_master_score(df, pivots, orb_high)
    
    # 5. VELOCITY RULE: If volume is 2x, we only need score 3
    required_score = CONFIG["min_score"]
    if vol_ratio >= CONFIG["velocity_mult"]:
        required_score = CONFIG["velocity_score"]
        log.debug(f"⚡ VELOCITY detected for {symbol} (Vol {vol_ratio:.1f}x). Lowering score req to {required_score}")

    if score < required_score:
        return None

    # 6. THE PULLBACK GUARD
    vwap_dist = (last["close"] - last["vwap"]) / last["vwap"]
    if vwap_dist > CONFIG["vwap_guard_pct"]:
        return None

    # 7. Build Signal
    entry = round(float(last["close"]), 2)
    atr = float(last["atr"])
    stop_loss = round(entry - (CONFIG["atr_sl_mult"] * atr), 2)
    target = round(entry + (CONFIG["atr_tp_mult"] * atr), 2)

    # Backtest compatibility sizing
    from strategy.indicators import position_size
    from config.settings import strategy_cfg
    qty = position_size(entry, stop_loss, strategy_cfg.account_capital, strategy_cfg.risk_pct_per_trade, strategy_cfg.max_capital_per_trade, strategy_cfg.max_exposure_multiple)
    if qty <= 0: return None

    log.info(f"🚀 MASTER SIGNAL | {symbol} | Score: {score} | Vol: {vol_ratio:.1f}x | Entry: {entry}")

    return Signal(
        symbol=symbol, entry=entry, stop_loss=stop_loss, target=target, quantity=qty,
        capital=round(entry * qty, 2), direction="LONG", vwap=round(float(last["vwap"]), 2), rsi=round(float(last["rsi"]), 1)
    )

# ─────────────────────────────────────────────
#  DYNAMIC TRAILING & REVERSAL EXIT
# ─────────────────────────────────────────────

def should_exit_early(symbol: str, signal: Signal) -> bool:
    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty: return False
    
    df["vwap"] = compute_vwap(df)
    df["ema9"] = compute_ema(df["close"], 9)
    df["ema21"]= compute_ema(df["close"], 21)
    
    last = df.iloc[-1]
    
    # 1. 3-Candle Trailing Stop (Profit Locker)
    # If current price is in profit, check trailing stop
    if last["close"] > signal.entry:
        trailing_sl = get_trailing_stop_level(df, window=3)
        if last["close"] < trailing_sl:
            log.info(f"🔒 LOCK: 3-Candle Trailing Stop triggered for {symbol} at {last['close']}")
            return True

    # 2. EMA Flip (Trend Change)
    if last["ema9"] < last["ema21"]:
        log.info(f"🛑 EXIT: EMA Flip detected for {symbol}")
        return True
        
    # 3. VWAP Break (Loss of Support)
    if last["close"] < last["vwap"]:
        log.info(f"🛑 EXIT: VWAP Break detected for {symbol}")
        return True
        
    return False
