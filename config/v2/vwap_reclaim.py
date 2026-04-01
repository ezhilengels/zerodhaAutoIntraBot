"""
config/v2/vwap_reclaim.py
─────────────────────────
Standalone config for VWAP reclaim strategy v2.

This is separate from config/settings.py so the original v1 reclaim strategy
remains untouched.
"""

from dataclasses import dataclass


@dataclass
class VWAPReclaimConfig:
    # Time window
    start_time: str = "09:30"
    end_time: str = "14:15"

    # VWAP distance
    reclaim_buffer_pct: float = 0.05
    max_distance_above_vwap_pct: float = 0.45

    # Volume
    volume_multiplier_min: float = 1.2
    min_session_volume: int = 300_000

    # EMA trend filter
    trend_filter_enabled: bool = True
    ema_fast_period: int = 9
    ema_slow_period: int = 21

    # Market filter
    market_filter_enabled: bool = True
    market_symbol: str = "NIFTY 50"

    # NEW settings
    max_vwap_reclaims: int = 3
    max_sl_atr_multiple: float = 1.8


vwap_reclaim_v2_cfg = VWAPReclaimConfig()
