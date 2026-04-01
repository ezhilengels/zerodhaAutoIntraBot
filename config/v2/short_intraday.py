"""
config/v2/short_intraday.py
───────────────────────────
Configuration for short_intraday_v2.
"""

from dataclasses import dataclass
import os


@dataclass
class ShortIntradayV2Config:
    timeframe: str = os.getenv("SHORT_INTRADAY_V2_TIMEFRAME", "5min").strip()
    rsi_period: int = int(os.getenv("SHORT_INTRADAY_V2_RSI_PERIOD", "14"))
    ema_period: int = int(os.getenv("SHORT_INTRADAY_V2_EMA_PERIOD", "20"))
    volume_lookback: int = int(os.getenv("SHORT_INTRADAY_V2_VOLUME_LOOKBACK", "20"))

    session_start: str = os.getenv("SHORT_INTRADAY_V2_SESSION_START", "10:00").strip()
    session_end: str = os.getenv("SHORT_INTRADAY_V2_SESSION_END", "14:00").strip()

    rsi_overbought: float = float(os.getenv("SHORT_INTRADAY_V2_RSI_OVERBOUGHT", "75.0"))
    ema_dist_threshold: float = float(os.getenv("SHORT_INTRADAY_V2_EMA_DIST_PCT", "0.05"))
    volume_climax_mult: float = float(os.getenv("SHORT_INTRADAY_V2_VOL_CLIMAX_MULT", "2.0"))
    min_confirmations: int = int(os.getenv("SHORT_INTRADAY_V2_MIN_CONFIRMATIONS", "2"))
    stop_buffer_pct: float = float(os.getenv("SHORT_INTRADAY_V2_STOP_BUFFER_PCT", "0.005"))

    blocklist: set = None

    def __post_init__(self) -> None:
        raw = os.getenv("SHORT_INTRADAY_V2_BLOCKLIST", "").strip()
        self.blocklist = {s.strip().upper() for s in raw.split(",") if s.strip()}


short_intraday_v2_cfg = ShortIntradayV2Config()
