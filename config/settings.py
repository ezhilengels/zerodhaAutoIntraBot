"""
config/settings.py
──────────────────
Single source of truth for ALL configuration.

Credentials are loaded from the .env file in the project root.
Edit .env for secrets. Edit this file for strategy parameters.
"""

import os
from dataclasses import dataclass
from typing import List
from dotenv import load_dotenv

# Load .env from project root (one level up from this file)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


# ─────────────────────────────────────────────────────────────────────────────
#  CREDENTIALS  (read from .env — never hardcoded here)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TelegramConfig:
    """
    Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your .env file.
    Get bot_token from @BotFather.
    Get chat_id from @userinfobot after messaging your bot once.
    """
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id:   str = os.getenv("TELEGRAM_CHAT_ID",   "")


@dataclass
class KiteConfig:
    """
    Set KITE_API_KEY, KITE_API_SECRET, KITE_REQUEST_TOKEN in your .env file.

    Daily Login Flow:
      1. Visit: https://kite.trade/connect/login?api_key=YOUR_KEY&v=3
      2. Login with Zerodha ID + password + TOTP
      3. Copy request_token from the redirected URL
      4. Paste KITE_REQUEST_TOKEN in .env  ← refresh every morning
    """
    api_key:       str = os.getenv("KITE_API_KEY",         "")
    api_secret:    str = os.getenv("KITE_API_SECRET",      "")
    request_token: str = os.getenv("KITE_REQUEST_TOKEN",   "")


# ─────────────────────────────────────────────────────────────────────────────
#  WATCHLIST
# ─────────────────────────────────────────────────────────────────────────────

CUSTOM_WATCHLIST: List[str] = [
    "NATIONALUM",
    "ONGC",
    "COALINDIA",
    "HINDALCO",
    "TATASTEEL",
    "GAIL",
    "BEL",
    "MRPL",
]

MIS_SELECTION_WATCHLIST: List[str] = [
    "TCS",
    "INFY",
    "SBIN",
    "ICICIBANK",
    "RELIANCE",
]

PIVOT_BREAKOUT_V2_WATCHLIST: List[str] = [
    # Bank Nifty-style liquid banking names
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
    "AXISBANK",
    "KOTAKBANK",
    "BANKBARODA",
    "PNB",
    "INDUSINDBK",
    "FEDERALBNK",
    # Add RELIANCE as a strong non-bank pivot candidate
    "RELIANCE",
]

NIFTY100_WATCHLIST: List[str] = [
    "ABB",
    "ADANIENSOL",
    "ADANIENT",
    "ADANIGREEN",
    "ADANIPORTS",
    "ADANIPOWER",
    "AMBUJACEM",
    "APOLLOHOSP",
    "ASIANPAINT",
    "DMART",
    "AXISBANK",
    "BAJAJ-AUTO",
    "BAJFINANCE",
    "BAJAJFINSV",
    "BAJAJHLDNG",
    "BANKBARODA",
    "BEL",
    "BPCL",
    "BHARTIARTL",
    "BOSCHLTD",
    "BRITANNIA",
    "CGPOWER",
    "CANBK",
    "CHOLAFIN",
    "CIPLA",
    "COALINDIA",
    "CUMMINSIND",
    "DLF",
    "DIVISLAB",
    "DRREDDY",
    "EICHERMOT",
    "ETERNAL",
    "GAIL",
    "GODREJCP",
    "GRASIM",
    "HAL",
    "HCLTECH",
    "HDFCAMC",
    "HDFCBANK",
    "HDFCLIFE",
    "HINDALCO",
    "HINDZINC",
    "HINDUNILVR",
    "HYUNDAI",
    "ICICIBANK",
    "ITC",
    "INDHOTEL",
    "IOC",
    "INFY",
    "IRFC",
    "INDIGO",
    "JIOFIN",
    "JSWSTEEL",
    "JINDALSTEL",
    "KOTAKBANK",
    "LTM",
    "LT",
    "LODHA",
    "M&M",
    "MARUTI",
    "MAXHEALTH",
    "MAZDOCK",
    "MUTHOOTFIN",
    "NTPC",
    "NESTLEIND",
    "ONGC",
    "PIDILITIND",
    "PFC",
    "POWERGRID",
    "PNB",
    "RECLTD",
    "RELIANCE",
    "SBILIFE",
    "MOTHERSON",
    "SHREECEM",
    "SHRIRAMFIN",
    "ENRIN",
    "SIEMENS",
    "SBIN",
    "SOLARINDS",
    "SUNPHARMA",
    "TVSMOTOR",
    "TATACAP",
    "TCS",
    "TATACONSUM",
    "TMCV",
    "TMPV",
    "TATAPOWER",
    "TATASTEEL",
    "TECHM",
    "TITAN",
    "TORNTPHARM",
    "TRENT",
    "ULTRACEMCO",
    "UNIONBANK",
    "UNITDSPR",
    "VBL",
    "VEDL",
    "WIPRO",
    "ZYDUSLIFE",
]

WATCHLIST_MODE: str = os.getenv("WATCHLIST_MODE", "custom").strip().lower()
WATCHLIST: List[str]
if WATCHLIST_MODE == "nifty100":
    WATCHLIST = NIFTY100_WATCHLIST
elif WATCHLIST_MODE in {"pivot_breakout_v2", "pivotv2", "pivot"}:
    WATCHLIST = PIVOT_BREAKOUT_V2_WATCHLIST
else:
    WATCHLIST = CUSTOM_WATCHLIST


# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StrategyConfig:
    # Entry filters
    gap_up_threshold:    float = 2.5   # Min % gap-up from prev close required
    pullback_vwap_range: float = 0.8   # Max % LTP can be above VWAP
    rsi_min:             float = 42    # RSI lower bound (neutral pullback zone)
    rsi_max:             float = 58    # RSI upper bound (not yet overbought)
    volume_ratio_max:    float = 0.8   # Last candle volume / avg — low = quiet dip

    # Risk management
    stop_loss_pct:         float = 2.0    # SL as % below entry price
    reward_ratio:          float = 2.0    # Target = entry + (risk × reward_ratio)
    max_capital_per_trade: int   = 20000  # ₹ max deployed per trade
    account_capital:       int   = 20000  # ₹ account capital used for risk-based sizing
    risk_pct_per_trade:    float = 2.0    # Max % of account capital to risk per trade
    max_exposure_multiple: float = 3.0    # Hard cap: entry value cannot exceed this × account capital


@dataclass
class OrbConfig:
    range_start_time:      str   = "09:15"  # First candle to include in ORB range
    range_end_time:        str   = "09:30"  # Last time included in the opening range
    entry_end_time:        str   = "11:30"  # No fresh ORB entries after this time
    breakout_buffer_pct:   float = 0.1      # Break above ORB high required for entry
    volume_multiplier_min: float = 1.5      # Breakout candle volume vs recent average


@dataclass
class VWAPReclaimConfig:
    profile:                     str   = os.getenv("VWAP_RECLAIM_PROFILE", "quality").strip().lower()
    start_time:                  str   = os.getenv(
        "VWAP_RECLAIM_START_TIME",
        "09:35" if profile == "aggressive" else "09:45",
    )
    end_time:                    str   = os.getenv(
        "VWAP_RECLAIM_END_TIME",
        "11:45" if profile == "aggressive" else "11:15",
    )
    volume_multiplier_min:       float = float(
        os.getenv(
            "VWAP_RECLAIM_VOLUME_MULTIPLIER_MIN",
            "1.2" if profile == "aggressive" else "1.4",
        )
    )  # Require stronger participation on the reclaim
    min_session_volume:          int   = 0         # 0 disables the session-volume floor
    max_distance_above_vwap_pct: float = float(
        os.getenv(
            "VWAP_RECLAIM_MAX_DISTANCE_ABOVE_VWAP_PCT",
            "0.50" if profile == "aggressive" else "0.35",
        )
    )  # Avoid entries that are too extended above VWAP
    reclaim_buffer_pct:          float = float(
        os.getenv(
            "VWAP_RECLAIM_BUFFER_PCT",
            "0.03" if profile == "aggressive" else "0.10",
        )
    )  # Candle close must clear VWAP by this much
    trend_filter_enabled:        bool  = False     # Disabled by default; enable only if replay proves helpful
    ema_fast_period:             int   = 20        # Fast intraday EMA used for trend confirmation
    ema_slow_period:             int   = 50        # Slow intraday EMA used for trend confirmation
    market_filter_enabled:       bool  = os.getenv(
        "VWAP_RECLAIM_MARKET_FILTER_ENABLED",
        "false" if profile == "aggressive" else "true",
    ).lower() == "true"  # Trade only when the broader market supports longs
    market_symbol:               str   = "NIFTY 50"


@dataclass
class VWAPRsiConfig:
    start_time:                  str   = "09:45"  # No VWAP+RSI entries before this time
    end_time:                    str   = "11:45"  # No fresh VWAP+RSI entries after this time
    rsi_threshold:               float = 52.0     # RSI must be above this threshold for long entries
    volume_multiplier_min:       float = 1.4      # Confirmation candle volume vs recent average
    max_distance_above_vwap_pct: float = 0.35     # Avoid entries that are too extended above VWAP


@dataclass
class PivotBreakoutConfig:
    start_time:            str   = "09:45"  # No pivot-breakout entries before this time
    end_time:              str   = "11:45"  # No fresh pivot-breakout entries after this time
    volume_multiplier_min: float = 1.5      # Breakout candle volume vs recent average
    breakout_buffer_pct:   float = 0.15     # Break above R1 required to avoid borderline signals


@dataclass
class EMACrossoverConfig:
    start_time:            str   = "09:55"  # Avoid the earliest open noise, but allow earlier trends
    end_time:              str   = "11:45"  # Keep focus on stronger morning trends
    fast_period:           int   = 9        # Fast EMA period
    slow_period:           int   = 21       # Slow EMA period
    volume_multiplier_min: float = 1.4      # Require participation on the cross without overfiltering
    min_separation_pct:    float = 0.10     # Fast EMA must clear slow EMA by this margin
    min_body_pct:          float = 0.08     # Confirmation candle body as % of open


@dataclass
class ScannerConfig:
    max_trades_per_day: int = 3
    scan_interval_secs: int = 300     # How often to scan (seconds)
    trade_start_time:   str = "09:45" # HH:MM IST
    trade_end_time:     str = "15:10" # HH:MM IST
    mis_squareoff_time: str = "15:15" # Force MIS exit before broker auto square-off


@dataclass
class ExecutionConfig:
    # When True, simulate order placement and trade logging without sending live orders.
    paper_trading: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"
    # When True, send every scan cycle's TRUE/FALSE summary to Telegram.
    show_scan_results: bool = os.getenv("SHOW_SCAN_RESULTS", "false").lower() == "true"
    # When True, persist fetched NSE candles to CSV for later replay/backtesting.
    save_replay_candles: bool = os.getenv("SAVE_REPLAY_CANDLES", "false").lower() == "true"
    # Zerodha product type for live orders: MIS for intraday, CNC for delivery.
    order_product: str = os.getenv("ORDER_PRODUCT", "MIS").strip().upper()
    # For multi-strategy mode, require at least this many strategies to agree.
    min_strategy_confirmations: int = max(1, int(os.getenv("MIN_STRATEGY_CONFIRMATIONS", "1")))


@dataclass
class PreScanConfig:
    enabled: bool = os.getenv("PRE_SCAN_ENABLED", "false").lower() == "true"
    gap_threshold_pct: float = float(os.getenv("PRE_SCAN_GAP_THRESHOLD_PCT", "0.5"))
    shortlist_only: bool = os.getenv("PRE_SCAN_SHORTLIST_ONLY", "true").lower() == "true"
    shortlist_size: int = max(1, int(os.getenv("PRE_SCAN_SHORTLIST_SIZE", "15")))
    enable_news_check: bool = os.getenv("PRE_SCAN_ENABLE_NEWS_CHECK", "false").lower() == "true"


STRATEGY_MODE: str = os.getenv("STRATEGY_MODE", "pullback").strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
#  FILE PATHS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PathConfig:
    log_file:      str = "logs/pullback_trader.log"
    trade_log_csv: str = "trades/trade_log.csv"
    replay_csv:    str = "trades/replay_candles.csv"


# ─────────────────────────────────────────────────────────────────────────────
#  SINGLETONS  — import these throughout the project
# ─────────────────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN: str = TelegramConfig().bot_token
TELEGRAM_CHAT_ID:   str = TelegramConfig().chat_id

telegram_cfg = TelegramConfig()
kite_cfg     = KiteConfig()
strategy_cfg = StrategyConfig()
orb_cfg      = OrbConfig()
vwap_reclaim_cfg = VWAPReclaimConfig()
vwap_rsi_cfg = VWAPRsiConfig()
pivot_breakout_cfg = PivotBreakoutConfig()
ema_crossover_cfg = EMACrossoverConfig()
scanner_cfg  = ScannerConfig()
execution_cfg = ExecutionConfig()
prescan_cfg = PreScanConfig()
paths_cfg    = PathConfig()
