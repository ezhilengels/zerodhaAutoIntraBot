"""
config/v4/short_intraday.py
───────────────────────────
Configuration for short_intraday_v4.
"""

from dataclasses import dataclass
import os


@dataclass
class ShortIntradayV4Config:
    timeframe: str = os.getenv("SHORT_INTRADAY_V4_TIMEFRAME", "5min").strip()
    rsi_period: int = int(os.getenv("SHORT_INTRADAY_V4_RSI_PERIOD", "14"))
    ema_period: int = int(os.getenv("SHORT_INTRADAY_V4_EMA_PERIOD", "20"))
    volume_lookback: int = int(os.getenv("SHORT_INTRADAY_V4_VOLUME_LOOKBACK", "20"))

    session_start: str = os.getenv("SHORT_INTRADAY_V4_SESSION_START", "10:00").strip()
    session_end: str = os.getenv("SHORT_INTRADAY_V4_SESSION_END", "14:00").strip()
    market_symbol: str = os.getenv("SHORT_INTRADAY_V4_MARKET_SYMBOL", "NIFTY 50").strip()
    max_ranked_signals: int = int(os.getenv("SHORT_INTRADAY_V4_MAX_RANKED_SIGNALS", "5"))

    rsi_divergence_min: float = float(os.getenv("SHORT_INTRADAY_V4_RSI_DIVERGENCE_MIN", "70.0"))
    ema_dist_threshold: float = float(os.getenv("SHORT_INTRADAY_V4_EMA_DIST_PCT", "0.05"))
    volume_climax_mult: float = float(os.getenv("SHORT_INTRADAY_V4_VOL_CLIMAX_MULT", "2.5"))
    min_confirmations: int = int(os.getenv("SHORT_INTRADAY_V4_MIN_CONFIRMATIONS", "2"))
    stop_buffer_pct: float = float(os.getenv("SHORT_INTRADAY_V4_STOP_BUFFER_PCT", "0.005"))
    min_avg_turnover_rs: float = float(os.getenv("SHORT_INTRADAY_V4_MIN_AVG_TURNOVER_RS", "10000000"))
    market_filter_enabled: bool = os.getenv("SHORT_INTRADAY_V4_MARKET_FILTER_ENABLED", "true").strip().lower() == "true"

    blocklist: set = None

    def __post_init__(self) -> None:
        raw = os.getenv("SHORT_INTRADAY_V4_BLOCKLIST", "").strip()
        self.blocklist = {s.strip().upper() for s in raw.split(",") if s.strip()}


short_intraday_v4_cfg = ShortIntradayV4Config()
