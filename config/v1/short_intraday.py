"""
config/v1/short_intraday.py
───────────────────────────
Configuration for short_intraday_v1.

This is a confirmation-based intraday short strategy for overextended stocks
that show exhaustion and then fail below VWAP / the most recent swing low.
"""

from dataclasses import dataclass
import os


@dataclass
class ShortIntradayConfig:
    timeframe: str = os.getenv("SHORT_INTRADAY_V1_TIMEFRAME", "5min").strip()

    rsi_period: int = int(os.getenv("SHORT_INTRADAY_V1_RSI_PERIOD", "14"))
    ema_period: int = int(os.getenv("SHORT_INTRADAY_V1_EMA_PERIOD", "20"))
    atr_period: int = int(os.getenv("SHORT_INTRADAY_V1_ATR_PERIOD", "14"))
    volume_lookback: int = int(os.getenv("SHORT_INTRADAY_V1_VOLUME_LOOKBACK", "20"))
    swing_window: int = int(os.getenv("SHORT_INTRADAY_V1_SWING_WINDOW", "3"))
    cooldown_candles: int = int(os.getenv("SHORT_INTRADAY_V1_COOLDOWN_CANDLES", "4"))

    session_start: str = os.getenv("SHORT_INTRADAY_V1_SESSION_START", "10:00").strip()
    session_end: str = os.getenv("SHORT_INTRADAY_V1_SESSION_END", "13:30").strip()

    min_day_gain_pct: float = float(os.getenv("SHORT_INTRADAY_V1_MIN_DAY_GAIN_PCT", "2.0"))
    rsi_overbought: float = float(os.getenv("SHORT_INTRADAY_V1_RSI_OVERBOUGHT", "55.0"))
    min_signal_score: int = int(os.getenv("SHORT_INTRADAY_V1_MIN_SIGNAL_SCORE", "2"))
    volume_mult_min: float = float(os.getenv("SHORT_INTRADAY_V1_VOLUME_MULT", "1.1"))
    volume_climax_mult: float = float(os.getenv("SHORT_INTRADAY_V1_VOL_CLIMAX_MULT", "1.5"))

    vwap_dist_atr_min: float = float(os.getenv("SHORT_INTRADAY_V1_VWAP_DIST_ATR_MIN", "0.75"))
    ema_dist_pct_min: float = float(os.getenv("SHORT_INTRADAY_V1_EMA_DIST_PCT", "0.008"))
    day_high_proximity: float = float(os.getenv("SHORT_INTRADAY_V1_DAY_HIGH_PROX", "0.03"))
    vwap_break_buffer_pct: float = float(os.getenv("SHORT_INTRADAY_V1_VWAP_BREAK_BUFFER_PCT", "0.0002"))

    atr_sl_mult: float = float(os.getenv("SHORT_INTRADAY_V1_ATR_SL_MULT", "1.0"))
    atr_tp_mult: float = float(os.getenv("SHORT_INTRADAY_V1_ATR_TP_MULT", "2.0"))

    market_symbol: str = os.getenv("SHORT_INTRADAY_V1_MARKET_SYMBOL", "NIFTY 50").strip()
    market_bullish_threshold_pct: float = float(os.getenv("SHORT_INTRADAY_V1_MARKET_BULLISH_PCT", "1.5"))

    blocklist: set = None

    def __post_init__(self) -> None:
        raw = os.getenv("SHORT_INTRADAY_V1_BLOCKLIST", "").strip()
        self.blocklist = {s.strip().upper() for s in raw.split(",") if s.strip()}


short_intraday_v1_cfg = ShortIntradayConfig()
