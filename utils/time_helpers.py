"""
utils/time_helpers.py
──────────────────────
Thin helpers for trading-window checks and readable timestamps.
"""

from datetime import datetime
from config.settings import scanner_cfg


def current_hhmm() -> str:
    return datetime.now().strftime("%H:%M")


def now_str() -> str:
    """Readable timestamp for logs and trade records."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_trading_time() -> bool:
    """True if NOW is inside the configured trading window."""
    t = current_hhmm()
    return scanner_cfg.trade_start_time <= t <= scanner_cfg.trade_end_time


def is_past_end_time() -> bool:
    """True if the trading window has closed for the day."""
    return current_hhmm() > scanner_cfg.trade_end_time
