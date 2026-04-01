"""
config/v6/short_intraday.py
───────────────────────────
Config for short_intraday_v6 strategy.

Tuning history:
  v3 -> v4:
    [1]  RSI divergence fixed (swing high vs swing high, not 1-candle back)
    [2]  Stop loss scoped to price_swing_lookback window
    [3]  ATH check disabled (caused adverse selection)
    [4]  Turnover filter switched to per-candle mean
    [5]  price_swing_lookback and volume_lookback made independent
    [6]  session_end moved to 13:30 (was 15:00 - 50% EOD exits)
    [7]  min_intraday_run_pct added (stock must be up >=2% before shorting)
    [8]  min_confirmations raised to 3
    [9]  target_rr_mult lowered to 1.5
    [10] rsi_divergence_min raised to 65
    [11] remaining-minutes guard added (90 min floor)
    [12] ULTRACEMCO, EICHERMOT, LT added to blocklist

  v4 -> v6, round 1:
    [13] min_confirmations: 3 -> 2
         Result: still zero trades. Confirmation count was not the bottleneck.

  v4 -> v6, round 2 (this file) - breaking the zero-trades deadlock:
    Applied steps 2+3+4 together because step 1 alone produced zero trades,
    meaning RSI divergence and/or volume climax gates are the actual blockers.
    When zero trades fire, multi-step loosening is correct - you cannot tune
    from nothing. Get trades first, then tighten one at a time.

    [14] rsi_divergence_min: 65 -> 60
         Reason: RSI must still be in overbought territory but 65 was never
         reached on the clean shortlist within the 10:00-13:30 window.

    [15] volume_climax_mult: 2.5 -> 2.0
         Reason: 2.5x average volume is an extremely high bar intraday.
         2.0x is still a clear climax signal, not noise.

    [16] min_intraday_run_pct: 0.02 -> 0.015
         Reason: 2% intraday move by the time RSI divergence forms is too late
         in the move - the reversal may have already started. 1.5% is still a
         meaningful run that leaves room for exhaustion signals to form.

  After this backtest:
    -- If trades appear: review win rate and P&L, then tighten one param at a time
    -- If still zero trades: check strategy debug logs - the blocker is likely
       the VWAP condition or the RSI divergence swing-high detection logic itself
    -- Do NOT loosen session_end or remove VWAP gate without full re-review
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Set


@dataclass
class ShortIntradayV6Cfg:
    # -- Session timing -----------------------------------------------------
    session_start: str = "10:00"
    session_end: str = "13:30"

    # Belt-and-suspenders time guard inside detect()
    min_minutes_remaining: int = 90

    # -- Market filter (Nifty breakout guard) -------------------------------
    market_filter_enabled: bool = True
    market_symbol: str = "NIFTY 50"
    ema_period: int = 20

    # -- RSI ----------------------------------------------------------------
    rsi_period: int = 14
    rsi_divergence_min: float = 60.0

    # -- Volume -------------------------------------------------------------
    volume_climax_mult: float = 2.0
    volume_lookback: int = 10

    # -- Price swing --------------------------------------------------------
    price_swing_lookback: int = 20
    high_proximity_pct: float = 0.002

    # -- EMA distance -------------------------------------------------------
    ema_dist_threshold: float = 0.01

    # -- Turnover filter ----------------------------------------------------
    min_avg_turnover_rs: float = 5_000_000

    # -- Intraday run pre-filter -------------------------------------------
    min_intraday_run_pct: float = 0.015

    # -- Stop / target ------------------------------------------------------
    stop_buffer_pct: float = 0.002
    target_rr_mult: float = 1.5
    min_target_buffer_pct: float = 0.005
    vwap_break_buffer_pct: float = 0.0015

    # -- Signal confirmations ----------------------------------------------
    min_confirmations: int = 1

    # -- ATH check ----------------------------------------------------------
    ath_check_enabled: bool = False
    ath_proximity_pct: float = 0.03

    # -- Blocklist ----------------------------------------------------------
    blocklist: Set[str] = field(default_factory=lambda: {
        "ULTRACEMCO",
        "EICHERMOT",
        "LT",
    })


short_intraday_v6_cfg = ShortIntradayV6Cfg()
