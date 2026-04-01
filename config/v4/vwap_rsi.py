"""
config/v4/vwap_rsi.py
─────────────────────
Standalone config for VWAP + RSI bot strategy v4.

This keeps v4 fully isolated from v1/v2/v3 and allows tuning from .env.
"""

from dataclasses import dataclass
import os


@dataclass
class VWAPRsiV4Config:
    symbol: str = os.getenv("VWAP_RSI_V4_SYMBOL", "BANKNIFTY").strip().upper()
    timeframe: str = os.getenv("VWAP_RSI_V4_TIMEFRAME", "5min").strip()
    rsi_period: int = int(os.getenv("VWAP_RSI_V4_RSI_PERIOD", "14"))
    ema_fast: int = int(os.getenv("VWAP_RSI_V4_EMA_FAST", "9"))
    ema_slow: int = int(os.getenv("VWAP_RSI_V4_EMA_SLOW", "21"))
    atr_period: int = int(os.getenv("VWAP_RSI_V4_ATR_PERIOD", "14"))
    atr_sl_mult: float = float(os.getenv("VWAP_RSI_V4_ATR_SL_MULT", "1.5"))
    atr_tp_mult: float = float(os.getenv("VWAP_RSI_V4_ATR_TP_MULT", "2.5"))
    volume_mult: float = float(os.getenv("VWAP_RSI_V4_VOLUME_MULT", "1.5"))
    volume_lookback: int = int(os.getenv("VWAP_RSI_V4_VOLUME_LOOKBACK", "20"))
    min_signal_score: int = int(os.getenv("VWAP_RSI_V4_MIN_SIGNAL_SCORE", "3"))
    cooldown_candles: int = int(os.getenv("VWAP_RSI_V4_COOLDOWN_CANDLES", "3"))
    session_start: str = os.getenv("VWAP_RSI_V4_SESSION_START", "09:15").strip()
    session_end: str = os.getenv("VWAP_RSI_V4_SESSION_END", "14:30").strip()
    capital: int = int(os.getenv("VWAP_RSI_V4_CAPITAL", "100000"))
    risk_pct: float = float(os.getenv("VWAP_RSI_V4_RISK_PCT", "0.01"))
    enable_shorts: bool = os.getenv("VWAP_RSI_V4_ENABLE_SHORTS", "false").strip().lower() == "true"
    # After price moves this many ATRs in our favour, slide SL to breakeven
    # Prevents unconverted winners from becoming losses / EOD drift-backs
    atr_be_mult: float = float(os.getenv("VWAP_RSI_V4_ATR_BE_MULT", "1.0"))
    # Hard gate: max % price can be above VWAP to still enter (avoids chasing)
    # 0.005 = 0.5% was too tight and blocked early-trend gap-up names like EICHERMOT
    # 0.010 = 1.0% gives breathing room while still rejecting extended entries
    vwap_max_dist_pct: float = float(os.getenv("VWAP_RSI_V4_VWAP_MAX_DIST_PCT", "0.010"))
    # Comma-separated list of symbols to never trade, regardless of signal score
    # Use this for chronic underperformers identified in backtests
    # Example: VWAP_RSI_V4_BLOCKLIST=MARUTI,TECHM,SBILIFE
    blocklist: set = None

    def __post_init__(self):
        raw = os.getenv("VWAP_RSI_V4_BLOCKLIST", "").strip()
        self.blocklist = {s.strip().upper() for s in raw.split(",") if s.strip()}


vwap_rsi_v4_cfg = VWAPRsiV4Config()

