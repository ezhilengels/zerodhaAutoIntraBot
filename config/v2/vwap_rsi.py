"""
config/v2/vwap_rsi.py
─────────────────────
Standalone config for VWAP + RSI strategy v2.

This is intentionally separate from config/settings.py so the original v1
configuration remains untouched.
"""

from dataclasses import dataclass


@dataclass
class VWAPRsiConfig:
    start_time:                  str   = "09:30"   # was 09:45
    end_time:                    str   = "13:30"   # was 11:45 (big change!)
    rsi_threshold:               float = 50.0      # was 52 — slightly relaxed
    rsi_overbought:              float = 68.0      # NEW — avoid overbought
    volume_multiplier_min:       float = 1.2       # was 1.4 — slightly relaxed
    max_distance_above_vwap_pct: float = 0.50      # was 0.35 — slightly wider


vwap_rsi_v2_cfg = VWAPRsiConfig()
