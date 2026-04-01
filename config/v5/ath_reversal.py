"""
config/v5/ath_reversal.py
──────────────────────────
Configuration for the ATH Mean-Reversion Short strategy v5.

This strategy shorts stocks that are significantly extended above their
20-period EMA and showing multiple exhaustion signals (RSI divergence,
volume climax, reversal candlesticks, lower-high structure).
"""

from dataclasses import dataclass
import os


@dataclass
class ATHReversalConfig:
    symbol:             str   = os.getenv("ATH_REV_SYMBOL",       "RELIANCE").strip().upper()
    timeframe:          str   = os.getenv("ATH_REV_TIMEFRAME",    "5min").strip()

    # ── Indicator periods ──────────────────────────────────────────────
    rsi_period:         int   = int(os.getenv("ATH_REV_RSI_PERIOD",       "14"))
    ema_period:         int   = int(os.getenv("ATH_REV_EMA_PERIOD",       "20"))
    atr_period:         int   = int(os.getenv("ATH_REV_ATR_PERIOD",       "14"))
    volume_lookback:    int   = int(os.getenv("ATH_REV_VOLUME_LOOKBACK",  "20"))

    # ── Entry thresholds ───────────────────────────────────────────────
    # Minimum % price must be above VWAP to be considered overextended intraday.
    # EMA-20 on 5-min candles is useless here — VWAP is the right reference.
    # 0.02 = price must be at least 2% above VWAP to qualify.
    ema_dist_threshold: float = float(os.getenv("ATH_REV_EMA_DIST_PCT",      "0.02"))
    # Volume must be this many times average to qualify as a climax candle
    volume_climax_mult: float = float(os.getenv("ATH_REV_VOL_CLIMAX_MULT",   "1.5"))
    # Number of candles each side to identify a swing high (2 is less strict than 3)
    swing_window:       int   = int(os.getenv("ATH_REV_SWING_WINDOW",        "2"))
    # Price must be within this % of the day's running high to enter short
    day_high_proximity: float = float(os.getenv("ATH_REV_DAY_HIGH_PROX",     "0.04"))
    # RSI must be above this level to confirm overbought exhaustion zone
    rsi_overbought:     float = float(os.getenv("ATH_REV_RSI_OVERBOUGHT",    "60.0"))

    # ── Signal quality ─────────────────────────────────────────────────
    min_signal_score:   int   = int(os.getenv("ATH_REV_MIN_SIGNAL_SCORE",    "3"))
    cooldown_candles:   int   = int(os.getenv("ATH_REV_COOLDOWN_CANDLES",    "5"))

    # ── Risk management ────────────────────────────────────────────────
    # SL placed above the recent swing high (tight — short squeezes are real)
    atr_sl_mult:        float = float(os.getenv("ATH_REV_ATR_SL_MULT",       "1.0"))
    # TP targets mean reversion toward EMA
    atr_tp_mult:        float = float(os.getenv("ATH_REV_ATR_TP_MULT",       "2.0"))
    capital:            int   = int(os.getenv("ATH_REV_CAPITAL",             "100000"))
    risk_pct:           float = float(os.getenv("ATH_REV_RISK_PCT",          "0.01"))

    # ── Session ────────────────────────────────────────────────────────
    # Start at 10:00 — need at least 45 min for ATH structure to form
    # End at 14:00 — give enough time for mean reversion before EOD
    session_start:      str   = os.getenv("ATH_REV_SESSION_START", "10:00").strip()
    session_end:        str   = os.getenv("ATH_REV_SESSION_END",   "14:00").strip()

    # ── Blocklist ──────────────────────────────────────────────────────
    # Comma-separated symbols to never short (e.g. strong trending stocks)
    blocklist: set = None

    def __post_init__(self):
        raw = os.getenv("ATH_REV_BLOCKLIST", "").strip()
        self.blocklist = {s.strip().upper() for s in raw.split(",") if s.strip()}


ath_reversal_cfg = ATHReversalConfig()
