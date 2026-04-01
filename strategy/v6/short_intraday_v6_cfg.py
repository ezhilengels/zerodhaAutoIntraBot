"""
config/v4/short_intraday.py
────────────────────────────
Config for short_intraday_v4 strategy.

All new fields added in v4 are marked with their fix number for traceability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Set


@dataclass
class ShortIntradayV4Cfg:

    # ── Session timing ──────────────────────────────────────────────────────
    session_start: str = "10:00"
    session_end:   str = "13:30"          # Fix 6: was 15:00 — cuts EOD exits

    # Fix 11: belt-and-suspenders guard regardless of session_end setting
    min_minutes_remaining: int = 90       # skip entry if <90 min left in session

    # ── Market filter (Nifty breakout guard) ────────────────────────────────
    market_filter_enabled: bool = True
    market_symbol:         str  = "NIFTY 50"
    ema_period:            int  = 20

    # ── RSI ─────────────────────────────────────────────────────────────────
    rsi_period:          int   = 14
    rsi_divergence_min:  float = 65.0     # Fix 10: was 55.0 — must be overbought

    # ── Volume ──────────────────────────────────────────────────────────────
    volume_climax_mult: float = 2.5       # current vol must be 2.5× avg
    volume_lookback:    int   = 10        # Fix 5: candles for vol/turnover average

    # ── Price swing ─────────────────────────────────────────────────────────
    price_swing_lookback: int = 20        # Fix 5: candles for swing high detection
                                          #        was sharing volume_lookback (30)

    # ── EMA distance ────────────────────────────────────────────────────────
    ema_dist_threshold: float = 0.03      # 3% above EMA20 = overextended

    # ── Turnover filter ─────────────────────────────────────────────────────
    # Fix 4: compared against per-candle MEAN, not session cumulative sum
    min_avg_turnover_rs: float = 5_000_000  # ₹50L avg turnover per candle

    # ── Intraday run pre-filter ─────────────────────────────────────────────
    # Fix 7: stock must be up this much from day open before we consider shorting
    min_intraday_run_pct: float = 0.02    # 2% intraday gain minimum

    # ── Stop / target ───────────────────────────────────────────────────────
    stop_buffer_pct:        float = 0.002  # 0.2% buffer above swing high stop
    target_rr_mult:         float = 1.5   # Fix 9: was 2.0 — 1.5R more achievable
    min_target_buffer_pct:  float = 0.005 # 0.5% buffer below EMA target

    # ── Signal confirmations ────────────────────────────────────────────────
    min_confirmations: int = 3            # Fix 8: was 2 — all 3 signals required

    # ── ATH check ───────────────────────────────────────────────────────────
    # Fix 3: disabled — caused adverse selection (only shorting strongest stocks)
    ath_check_enabled:  bool  = False
    ath_proximity_pct:  float = 0.03      # kept for reference if re-enabled

    # ── Blocklist ───────────────────────────────────────────────────────────
    # Fix 12: ULTRACEMCO/EICHERMOT/LT were structural false-positive sources
    # in Nifty 100 backtest (combined drag: -510.19)
    blocklist: Set[str] = field(default_factory=lambda: {
        "ULTRACEMCO",   # infra/capex — doesn't exhaust cleanly intraday
        "EICHERMOT",    # low float, erratic volume climax signals
        "LT",           # same structural issue as ULTRACEMCO
    })


short_intraday_v4_cfg = ShortIntradayV4Cfg()
