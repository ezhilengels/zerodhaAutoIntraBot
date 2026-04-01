"""
config/v3/short_intraday.py
───────────────────────────
Configuration for short_intraday_v3.
"""

from dataclasses import dataclass
import os


@dataclass
class ShortIntradayV3Config:
    timeframe: str = os.getenv("SHORT_INTRADAY_V3_TIMEFRAME", "5min").strip()
    rsi_period: int = int(os.getenv("SHORT_INTRADAY_V3_RSI_PERIOD", "14"))
    ema_period: int = int(os.getenv("SHORT_INTRADAY_V3_EMA_PERIOD", "20"))
    volume_lookback: int = int(os.getenv("SHORT_INTRADAY_V3_VOLUME_LOOKBACK", "20"))

    session_start: str = os.getenv("SHORT_INTRADAY_V3_SESSION_START", "10:00").strip()
    session_end: str = os.getenv("SHORT_INTRADAY_V3_SESSION_END", "14:00").strip()
    market_symbol: str = os.getenv("SHORT_INTRADAY_V3_MARKET_SYMBOL", "NIFTY 50").strip()
    max_ranked_signals: int = int(os.getenv("SHORT_INTRADAY_V3_MAX_RANKED_SIGNALS", "5"))

    rsi_overbought: float = float(os.getenv("SHORT_INTRADAY_V3_RSI_OVERBOUGHT", "75.0"))
    ema_dist_threshold: float = float(os.getenv("SHORT_INTRADAY_V3_EMA_DIST_PCT", "0.05"))
    volume_climax_mult: float = float(os.getenv("SHORT_INTRADAY_V3_VOL_CLIMAX_MULT", "2.0"))
    min_confirmations: int = int(os.getenv("SHORT_INTRADAY_V3_MIN_CONFIRMATIONS", "2"))
    stop_buffer_pct: float = float(os.getenv("SHORT_INTRADAY_V3_STOP_BUFFER_PCT", "0.005"))
    min_avg_turnover_rs: float = float(os.getenv("SHORT_INTRADAY_V3_MIN_AVG_TURNOVER_RS", "10000000"))
    min_day_gain_pct: float = float(os.getenv("SHORT_INTRADAY_V3_MIN_DAY_GAIN_PCT", "2.5"))
    lower_high_lookback: int = int(os.getenv("SHORT_INTRADAY_V3_LOWER_HIGH_LOOKBACK", "6"))
    lower_high_buffer_pct: float = float(os.getenv("SHORT_INTRADAY_V3_LOWER_HIGH_BUFFER_PCT", "0.001"))
    require_near_ath: bool = os.getenv("SHORT_INTRADAY_V3_REQUIRE_NEAR_ATH", "true").strip().lower() == "true"
    ath_near_pct: float = float(os.getenv("SHORT_INTRADAY_V3_ATH_NEAR_PCT", "3.0"))
    ath_lookback_days: int = int(os.getenv("SHORT_INTRADAY_V3_ATH_LOOKBACK_DAYS", "252"))
    market_filter_enabled: bool = os.getenv("SHORT_INTRADAY_V3_MARKET_FILTER_ENABLED", "true").strip().lower() == "true"
    sector_filter_enabled: bool = os.getenv("SHORT_INTRADAY_V3_SECTOR_FILTER_ENABLED", "true").strip().lower() == "true"

    target_rr_mult: float = float(os.getenv("SHORT_INTRADAY_V3_TARGET_RR_MULT", "1.8"))
    min_target_buffer_pct: float = float(os.getenv("SHORT_INTRADAY_V3_MIN_TARGET_BUFFER_PCT", "0.003"))

    blocklist: set = None

    def __post_init__(self) -> None:
        raw = os.getenv("SHORT_INTRADAY_V3_BLOCKLIST", "").strip()
        self.blocklist = {s.strip().upper() for s in raw.split(",") if s.strip()}


short_intraday_v3_cfg = ShortIntradayV3Config()
